"""Burn a talking pack: title, speech captions, call to action.

The words on screen during the body are the transcript of the assembled
cut. Nothing in the brief is turned into a caption, and no spoken line is
copied up into ``TITLE``. The title is the brief's editorial line (who
and what the video is). ``CTA`` is optional and covers the close when set.
The middle is whatever was said. The title is bold white with no stroke.
A call to action, when the brief sets one, uses that same look at a
slightly smaller size. Captions are one line, white with a black stroke,
sized so a short cue covers about three quarters of the frame width, and
still smaller than the title. When ``CAPTION_POSITION`` is omitted the
center of the line sits at two-fifths of the frame height up from the
bottom, below the middle. A phrase that would overflow becomes the next
timed cue. Brief ``COLORS`` do not tint them.

Local Whisper only. ``faster-whisper`` is used when it is installed;
otherwise the ``openai-whisper`` package already required by the app.
Neither call leaves the machine.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from modules.club.brief import Brief
from modules.club.brand import brand_filter, neutral_ink, resolve_font, type_font


# Opening and closing plates. The user asked for roughly the first 2–3 seconds.
TALKING_PLATE_S = 2.5

# A caption is one line. A phrase that would run past the frame becomes
# the next timed cue. The character cap is only a backstop.
CAPTION_WORDS = 4
CAPTION_CHARS = 42

# A short cue (a few words, about eighteen characters) should cover about
# three quarters of the frame. ``CAPTION_EM`` is the same width-per-point
# estimate the one-line split uses. The result stays under the title.
CAPTION_TYPICAL_CHARS = 18
CAPTION_WIDTH_SHARE = 0.75
CAPTION_EM = 0.55

# Two-fifths of the frame height, measured up from the bottom to the
# vertical center of a ``two_fifths`` caption. From the top that center
# is at ``(1 - CAPTION_Y_TWO_FIFTHS) × height``, below the middle.
# Do not feed this fraction to a top-aligned MarginV. Alignment 8 measures
# MarginV down to the top of the line, so ``0.4 × height`` puts that top
# at three-fifths of the way up and the line sits above the middle.
CAPTION_Y_TWO_FIFTHS = 0.4


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }


def talking_windows(duration: float, *, cta: bool = True) -> dict[str, tuple[float, float]]:
    """Title, caption body, and end-card spans on the assembled cut.

    The lower-third slot is always empty: captions occupy the middle, not
    a second copy of the title. Captions start when the title ends, so
    nothing is burned on top of the title. On a very short cut the three
    spans split the timeline in thirds so they still do not overlap. With
    no call to action the end span stays empty and captions run through
    the close, so the cut does not finish on a blank card.
    """
    duration = max(0.0, float(duration))
    empty = (0.0, 0.0)
    if duration <= 0:
        return {"title": empty, "lower": empty, "captions": empty, "end": empty}
    plate = TALKING_PLATE_S if duration >= 8 else max(1.0, duration * 0.25)
    if not cta:
        title_end = min(plate, duration)
        return {
            "title": (0.0, title_end),
            "lower": empty,
            "captions": (title_end, duration),
            "end": empty,
        }
    if duration < plate * 2 + 0.4:
        third = duration / 3.0
        return {
            "title": (0.0, third),
            "lower": empty,
            "captions": (third, third * 2),
            "end": (third * 2, duration),
        }
    return {
        "title": (0.0, plate),
        "lower": empty,
        "captions": (plate, duration - plate),
        "end": (duration - plate, duration),
    }


def _word_text(word: dict) -> str:
    return " ".join(str(word.get("text") or word.get("word") or "").split())


def _body_slice(start: float, end: float, body_start: float, body_end: float):
    """Portion of a span that falls inside the caption body.

    Speech that began under the title and is still going when the plate
    clears keeps the overlapping tail. The visible start is the plate, so
    the burned cue begins the instant the title ends.
    """
    lo = max(float(start), float(body_start))
    hi = min(float(end), float(body_end))
    if hi - lo < 1e-3:
        return None
    return lo, hi


def _split_timed(text: str, start: float, end: float, max_chars: int,
                 max_words: int = CAPTION_WORDS):
    """Break one spoken line into caption lines. Times follow the words."""
    words = text.split()
    if not words:
        return
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and (len(current) >= max_words or len(candidate) > max_chars):
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    total = sum(len(line) for line in lines) or 1
    span = max(0.0, end - start)
    cursor = start
    for index, line in enumerate(lines):
        if index == len(lines) - 1:
            line_end = end
        else:
            line_end = cursor + span * (len(line) / total)
        if line_end - cursor >= 0.15:
            yield line, cursor, line_end
        cursor = line_end


def _cues_from_words(words, body_start: float, body_end: float,
                     max_chars: int, max_words: int) -> list[CaptionCue]:
    kept = []
    for word in words or []:
        text = _word_text(word)
        if not text:
            continue
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        midpoint = (start + end) / 2.0
        # A word still in progress at the title clear has its middle under
        # the plate. Dropping it on that middle is what left a gap after
        # the title even though someone was talking.
        if end <= body_start or midpoint >= body_end:
            continue
        clipped = _body_slice(start, end, body_start, body_end)
        if clipped is None:
            continue
        kept.append((clipped[0], clipped[1], text))
    cues: list[CaptionCue] = []
    bucket: list[tuple[float, float, str]] = []

    def flush() -> None:
        if not bucket:
            return
        text = " ".join(item[2] for item in bucket)
        start = bucket[0][0]
        end = max(item[1] for item in bucket)
        if end <= start:
            end = start + 0.3
        cues.append(CaptionCue(start, end, text))
        bucket.clear()

    for item in kept:
        candidate = " ".join(part[2] for part in bucket + [item])
        if bucket and (len(bucket) >= max_words or len(candidate) > max_chars):
            flush()
        bucket.append(item)
    flush()
    return cues


def caption_cues(
    segments,
    *,
    body_start: float,
    body_end: float,
    max_chars: int = CAPTION_CHARS,
    max_words: int = CAPTION_WORDS,
) -> list[CaptionCue]:
    """Caption lines whose text is taken only from ``segments``.

    A segment may carry ``words`` (``start``, ``end``, ``text`` or ``word``).
    Without words, the segment text is split across its own time span and
    clipped to the body between the title and the call to action. Lines
    that finish before the title, or start on the end card, are dropped.
    A line that began under the title and is still going when the plate
    clears is kept, and the cue starts at that instant rather than waiting
    for the next word that begins after the title. No line is added that
    the transcript did not contain.
    """
    if body_end <= body_start:
        return []
    cues: list[CaptionCue] = []
    for seg in segments or []:
        words = seg.get("words") if isinstance(seg, dict) else None
        if words:
            cues.extend(_cues_from_words(
                words, body_start, body_end, max_chars, max_words,
            ))
            continue
        text = " ".join(str(seg.get("text") or "").split())
        if not text:
            continue
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        midpoint = (start + end) / 2.0
        if end <= body_start or midpoint >= body_end:
            continue
        clipped = _body_slice(start, end, body_start, body_end)
        if clipped is None:
            continue
        lines = list(_split_timed(
            text, clipped[0], clipped[1], max_chars, max_words,
        ))
        if not lines:
            lines = [(text, clipped[0], clipped[1])]
        for line, line_start, line_end in lines:
            cues.append(CaptionCue(line_start, line_end, line))
    return cues


def ass_timestamp(seconds: float) -> str:
    """ASS ``H:MM:SS.cs``."""
    cs_total = int(round(max(0.0, float(seconds)) * 100))
    hours, cs_total = divmod(cs_total, 360000)
    minutes, cs_total = divmod(cs_total, 6000)
    secs, cs = divmod(cs_total, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_color(hex_color: str) -> str:
    raw = hex_color.lstrip("#")
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    return f"&H00{bb}{gg}{rr}&"


def _ass_text(text: str) -> str:
    return (text.replace("\\", r"\\")
                .replace("{", r"\{")
                .replace("}", r"\}")
                .replace("\n", r"\N"))


def two_fifths_center_y(height: int) -> int:
    """Script Y of the vertical center of a ``two_fifths`` caption.

    Two-fifths of the way up the frame. From the top that is
    ``(1 - CAPTION_Y_TWO_FIFTHS) × height``, which is below the middle.
    """
    height = max(int(height), 1)
    return int(round(height * (1.0 - CAPTION_Y_TWO_FIFTHS)))


def caption_anchor(position: str, height: int) -> tuple[int, int]:
    """ASS ``(Alignment, MarginV)`` for a caption position.

    ``two_fifths`` uses middle-center alignment. MarginV is ignored for
    alignment 5, so the Y is not a margin: ``caption_pos_override`` places
    the center of the line with ``\\pos``. A top-center margin of
    ``CAPTION_Y_TWO_FIFTHS × height`` would pin the top of the line there
    and sit above the middle. ``center`` and ``middle`` stay at mid-frame
    (alignment 5, MarginV 0) with no ``\\pos``. ``top`` and ``bottom`` keep
    a 48px edge margin.
    """
    if position == "two_fifths" or position in ("middle", "center"):
        return 5, 0
    if position == "top":
        return 8, 48
    if position == "bottom":
        return 2, 48
    return 5, 0


def caption_pos_override(position: str, width: int, height: int) -> str:
    """Event override that puts a ``two_fifths`` line on its center.

    Empty for every other position, which still burn from Alignment and
    MarginV alone. ``\\an5`` is the center of the line. ``\\pos`` is in
    PlayRes pixels, X at the middle of the frame.
    """
    if position != "two_fifths":
        return ""
    x = max(int(width), 1) // 2
    y = two_fifths_center_y(height)
    return f"{{\\an5\\pos({x},{y})}}"


def default_caption_size(width: int, height: int) -> int:
    """Point size when ``CAPTION_SIZE`` is omitted.

    Chosen so a short cue of about ``CAPTION_TYPICAL_CHARS`` covers
    ``CAPTION_WIDTH_SHARE`` of ``width``. Capped under the title's starting
    size (``height * 0.055``) so a wide frame does not grow captions past
    the title.
    """
    width = max(int(width), 1)
    height = max(int(height), 1)
    size = (width * CAPTION_WIDTH_SHARE) / (CAPTION_TYPICAL_CHARS * CAPTION_EM)
    title = max(18, int(height * 0.055))
    size = min(size, title * 0.8)
    return max(18, int(round(size)))


def _line_capacity(frame_width: int, font_size: int) -> tuple[int, float]:
    """``(max characters, pixels per character)`` for one caption line."""
    usable = max(40, int(max(frame_width, 1) * 0.82))
    char_px = max(1.0, float(font_size) * CAPTION_EM)
    return max(8, int(usable / char_px)), char_px


def one_line_pieces(text: str, frame_width: int, font_size: int) -> list[str]:
    """Split ``text`` into lines that each fit the frame. No line holds a break."""
    words = str(text or "").split()
    if not words:
        return []
    max_chars, char_px = _line_capacity(frame_width, font_size)
    usable = max_chars * char_px
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        too_wide = current and (
            len(candidate) > max_chars or len(candidate) * char_px > usable
        )
        if too_wide:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def split_wide_cues(cues, frame_width: int, font_size: int) -> list[CaptionCue]:
    """One cue per on-screen line.

    A cue that would wrap is replaced by later cues that share its time
    span. Words stay in order. Nothing is added that was not in the cue.
    """
    shown: list[CaptionCue] = []
    for cue in cues or []:
        pieces = one_line_pieces(getattr(cue, "text", ""), frame_width, font_size)
        if not pieces:
            continue
        if len(pieces) == 1:
            shown.append(CaptionCue(cue.start, cue.end, pieces[0]))
            continue
        total = sum(len(piece) for piece in pieces) or 1
        span = max(0.0, float(cue.end) - float(cue.start))
        cursor = float(cue.start)
        for index, piece in enumerate(pieces):
            if index == len(pieces) - 1:
                end = float(cue.end)
            else:
                end = cursor + span * (len(piece) / total)
            if end <= cursor:
                end = cursor + 0.15
            shown.append(CaptionCue(cursor, end, piece))
            cursor = end
    return shown


def caption_style(brief: Brief, width: int, height: int) -> dict:
    """Resolved caption look. Type is white or black, with the other as the stroke.

    ``COLORS`` does not tint captions. A cream or orange ``CAPTION_COLOR``
    is drawn as white. A dark request is drawn as black, and the stroke
    flips so the line stays readable.
    """
    ink = neutral_ink(brief.caption_color or "FFFFFF")
    if brief.caption_stroke:
        stroke_ink = neutral_ink(brief.caption_stroke)
    else:
        stroke_ink = "000000" if ink == "FFFFFF" else "FFFFFF"
    if stroke_ink == ink:
        stroke_ink = "000000" if ink == "FFFFFF" else "FFFFFF"
    color = f"#{ink}"
    stroke = f"#{stroke_ink}"
    frame_width = int(width)
    size = brief.caption_size or default_caption_size(frame_width, height)
    stroke_width = brief.caption_stroke_width or max(1, min(3, size // 8))
    position = brief.caption_position or (
        "two_fifths" if brief.style == "talking" else "center"
    )
    align, margin_v = caption_anchor(position, height)
    return {
        "color": color,
        "stroke": stroke,
        "stroke_width": stroke_width,
        "size": size,
        "position": position,
        "alignment": align,
        "margin_v": margin_v,
        "anchor_y": two_fifths_center_y(height) if position == "two_fifths" else 0,
        "pos": caption_pos_override(position, frame_width, height),
    }


def _family_of(path: str) -> str:
    """Font family name inside ``path``, when fontconfig can read it."""
    if not path or shutil.which("fc-query") is None:
        return ""
    try:
        result = subprocess.run(
            ["fc-query", "-f", "%{family}", path],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    family = (result.stdout or "").split(",")[0].strip()
    if result.returncode == 0 and family:
        return family
    return ""


def _font_face(brief: Brief, resolved_path: str) -> tuple[str, str]:
    """``(ASS font name, directory of the font file)``."""
    spec = (brief.caption_font or brief.font or "").strip()
    path = resolved_path or ""
    if spec and not os.path.isfile(os.path.expanduser(spec)):
        face = spec
    else:
        face = _family_of(path) or (
            os.path.splitext(os.path.basename(path))[0].replace("-", " ") if path
            else "DejaVu Sans"
        )
    folder = os.path.dirname(path) if path else ""
    return face, folder


def render_ass(cues: list[CaptionCue], brief: Brief, *, width: int, height: int,
               font_path: str) -> str:
    """An ASS script of the spoken lines. Title and CTA are not in here."""
    style = caption_style(brief, width, height)
    face, _folder = _font_face(brief, font_path)
    align = style["alignment"]
    margin_v = style["margin_v"]
    override = style.get("pos") or ""
    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
         "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding"),
        (
            f"Style: Caption,{face},{style['size']},"
            f"{_ass_color(style['color'])},{_ass_color(style['color'])},"
            f"{_ass_color(style['stroke'])},&HFF000000,"
            f"0,0,0,0,100,100,0,0,1,{style['stroke_width']},0,"
            f"{align},40,40,{margin_v},1"
        ),
        "",
        "[Events]",
        ("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
         "Effect, Text"),
    ])
    lines = [header]
    for cue in split_wide_cues(cues, int(width), int(style["size"])):
        lines.append(
            "Dialogue: 0,"
            f"{ass_timestamp(cue.start)},{ass_timestamp(cue.end)},"
            f"Caption,,0,0,0,,{override}{_ass_text(cue.text)}"
        )
    return "\n".join(lines) + "\n"


_CUE_TIME = re.compile(
    r"^(?P<start>\d+(?::\d{1,2}){0,2}(?:\.\d+)?)\s*[–—-]\s*"
    r"(?P<end>\d+(?::\d{1,2}){0,2}(?:\.\d+)?)(?:\s+(?P<text>.*))?$"
)


def render_captions_md(cues) -> str:
    """A caption list a person can correct before the next burn.

    Each cue is a time range on its own line and the words on the next.
    One line of words. Title and the call to action are not in this file.
    """
    from modules.club.pick import format_timestamp
    lines = [
        "# Captions",
        "",
        "Edit the words. One cue is a time line, then one line of words.",
        "Save this file and run the assemble step again to burn it.",
        "Delete this file to transcribe again.",
        "",
    ]
    for cue in cues or []:
        start = getattr(cue, "start", None)
        end = getattr(cue, "end", None)
        text = getattr(cue, "text", None)
        if start is None and isinstance(cue, dict):
            start, end, text = cue.get("start"), cue.get("end"), cue.get("text")
        lines.append(f"{format_timestamp(float(start))}–{format_timestamp(float(end))}")
        lines.append(" ".join(str(text or "").split()))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_captions_md(text: str) -> list[CaptionCue]:
    """Cues from ``captions.md``. Lines that are not a time range are skipped.

    A time line may carry the words on the same line, or on the next line.
    """
    from modules.club.brief import parse_clock
    cues: list[CaptionCue] = []
    pending: tuple[float, float] | None = None
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _CUE_TIME.match(line)
        if match:
            if pending is not None:
                cues.append(CaptionCue(pending[0], pending[1], ""))
            start = parse_clock(match.group("start"))
            end = parse_clock(match.group("end"))
            words = " ".join((match.group("text") or "").split())
            if words:
                if end > start:
                    cues.append(CaptionCue(start, end, words))
                pending = None
            else:
                pending = (start, end)
            continue
        if pending is None:
            continue
        words = " ".join(line.split())
        if words and pending[1] > pending[0]:
            cues.append(CaptionCue(pending[0], pending[1], words))
        pending = None
    return [cue for cue in cues if cue.text]


def _filter_path(path: str) -> str:
    return (path.replace("\\", "/")
                .replace(":", r"\:")
                .replace("'", r"\'")
                .replace(",", r"\,")
                .replace("[", r"\[")
                .replace("]", r"\]"))


# ``base`` mis-heard short coaching cues ("finish" as "if I", and a Spanish
# line). ``small`` is faster and less reliable. ``medium`` is the talking-pack
# default. ``--whisper-model large-v3`` is the larger step when a take is
# still muddy. ``--whisper-model small`` is the faster fallback.
DEFAULT_WHISPER_MODEL = "medium"
WHISPER_MODEL_HELP = (
    "Local Whisper model name (default: medium). "
    "medium is the talking-pack default. "
    "Use large-v3 when a take is still muddy, or small for a faster pass. "
    "The name is written on transcript.json, timeline.json, and scores.json. "
    "Weights stay on this machine."
)


def transcribe_captions(path: str, model: str = DEFAULT_WHISPER_MODEL, log_fn=print) -> dict:
    """Word-timed speech from a local Whisper build.

    ``faster-whisper`` when it imports, otherwise ``openai-whisper``.
    Returns ``{"engine", "segments"}``. Segments that fail the app's
    speech check are dropped. The text is whatever the model heard.

    Language is English. Leaving it on auto-detect is what turned a short
    English cue into a Spanish line. ``medium`` is the default; ``base``
    mis-heard short coaching cues. Pass ``small`` for a faster pass, or
    ``large-v3`` when ``medium`` is still muddy.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        WhisperModel = None
    if WhisperModel is not None:
        log_fn(f"Captions: local faster-whisper ({model}).")
        whisper_model = WhisperModel(model, device="cpu", compute_type="int8")
        segments, _info = whisper_model.transcribe(
            path, word_timestamps=True, language="en",
        )
        return {"engine": "faster-whisper", "segments": _segments_from_faster(segments)}
    log_fn(f"Captions: local openai-whisper ({model}).")
    return {"engine": "whisper", "segments": _segments_from_openai(path, model, log_fn)}


def _keep(text: str) -> bool:
    from modules.audio.transcript import is_valid_speech
    return bool(text.strip()) and is_valid_speech(text)


def _segments_from_faster(segments) -> list:
    kept = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not _keep(text):
            continue
        words = []
        for word in seg.words or []:
            token = (word.word or "").strip()
            if not token:
                continue
            words.append({
                "start": float(word.start),
                "end": float(word.end),
                "text": token,
            })
        kept.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": text,
            "words": words,
        })
    return kept


def _segments_from_openai(path: str, model: str, log_fn) -> list:
    import whisper
    from modules.system.cuda_check import cuda_usable
    import torch
    device = "cuda" if cuda_usable(torch) else "cpu"
    log_fn(f"Captions: loading Whisper on {device}.")
    loaded = whisper.load_model(model, device=device)
    result = loaded.transcribe(
        path,
        word_timestamps=True,
        language="en",
        verbose=False,
        temperature=0.0,
        condition_on_previous_text=False,
        fp16=device == "cuda",
    )
    kept = []
    for seg in result.get("segments") or []:
        text = str(seg.get("text") or "").strip()
        if not _keep(text):
            continue
        words = []
        for word in seg.get("words") or []:
            token = str(word.get("word") or "").strip()
            if not token:
                continue
            words.append({
                "start": float(word["start"]),
                "end": float(word["end"]),
                "text": token,
            })
        kept.append({
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": text,
            "words": words,
        })
    return kept


def caption_record(brief: Brief, *, cues=None, engine=None, burned: bool = False,
                   error: str | None = None, source: str = "speech") -> dict:
    rows = [cue.as_dict() if isinstance(cue, CaptionCue) else cue for cue in (cues or [])]
    return {
        "requested": brief.style == "talking",
        "burned": burned,
        "source": source,
        "engine": engine,
        "error": error,
        "count": len(rows),
        "cues": rows,
    }


def apply_talking_pack(src: str, dst: str, brief: Brief, duration: float,
                       cues: list[CaptionCue], log_fn=print) -> str:
    """Draw the title, the spoken captions, and the call to action.

    One ffmpeg pass. Captions are written to ``captions.ass`` beside the
    draft and that file is what gets burned. Title and CTA sit in the
    middle of the frame on the picture: a soft shadow, no stroke, no plate.
    A brief with no CTA does not draw an end card. Raises when ffmpeg
    fails; the caller keeps the unbranded cut.
    """
    from modules.system.app_paths import ffmpeg_exe

    windows = talking_windows(duration, cta=bool((brief.cta or "").strip()))
    font = ""
    if brief.wants_brand() or cues:
        font = resolve_font(type_font(brief), log_fn=log_fn)
    width, height = 1920, 1080
    try:
        from modules.media.video_probe import probe_video
        info = probe_video(src)
        width = int(info.get("width") or width) or width
        height = int(info.get("height") or height) or height
    except Exception as exc:
        log_fn(f"Using 1920x1080 for type size ({exc}).")

    graphs = []
    if brief.wants_brand():
        if not font:
            raise RuntimeError(
                "No font for the title or call to action. Set FONT to a .ttf path or a font name."
            )
        plate = brand_filter(
            brief, duration, font, width, height,
            windows={"title": windows["title"], "lower": windows["lower"], "end": windows["end"]},
            centered=True,
        )
        if plate:
            graphs.append(plate)

    ass_path = ""
    if cues:
        # Replace the caller's list so cuts.json matches the one-line cues.
        cues[:] = split_wide_cues(
            list(cues), width, caption_style(brief, width, height)["size"],
        )
        if not font:
            raise RuntimeError(
                "No font for captions. Set FONT or CAPTION_FONT to a .ttf path or a font name."
            )
        script = render_ass(list(cues), brief, width=width, height=height, font_path=font)
        # Beside the draft, not a temp file, so the burn is the file on disk.
        ass_path = str(Path(dst).expanduser().resolve().parent / "captions.ass")
        with open(ass_path, "w", encoding="utf-8") as handle:
            handle.write(script)
        log_fn(f"captions.ass: {ass_path}")
        _face, fontsdir = _font_face(brief, font)
        subtitle = f"subtitles={_filter_path(ass_path)}"
        if fontsdir:
            subtitle += f":fontsdir={_filter_path(fontsdir)}"
        graphs.append(subtitle)

    if not graphs:
        shutil.copy2(src, dst)
        return dst

    end_on, end_off = windows["end"]
    cta_note = (
        f"CTA {end_on:.1f}-{end_off:.1f}s."
        if end_off > end_on else "no call to action."
    )
    log_fn(
        f"Talking pack: title {windows['title'][0]:.1f}-{windows['title'][1]:.1f}s, "
        f"{len(cues)} caption line(s), {cta_note}"
    )
    result = subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", src, "-vf", ",".join(graphs),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "copy", dst],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "ffmpeg failed"
        raise RuntimeError(f"Talking pack failed: {detail}")
    return dst
