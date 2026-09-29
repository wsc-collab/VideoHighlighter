"""Burn a talking pack: title, speech captions, call to action.

The words on screen during the body are the transcript of the assembled
cut. Nothing in the brief is turned into a caption. ``TITLE`` covers the
opening (about two and a half seconds) and ``CTA`` covers the close. The
middle is whatever was said.

Local Whisper only. ``faster-whisper`` is used when it is installed;
otherwise the ``openai-whisper`` package already required by the app.
Neither call leaves the machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from modules.club.brief import Brief
from modules.club.brand import brand_colors, brand_filter, resolve_font


# Opening and closing plates. The user asked for roughly the first 2–3 seconds.
TALKING_PLATE_S = 2.5

# A caption line is about four spoken words. The character cap is only a
# backstop for one very long token; it is not what sets the line length.
CAPTION_WORDS = 4
CAPTION_CHARS = 42

# Omitted CAPTION_SIZE scales with the frame and stays above the old 42pt.
CAPTION_SIZE_FLOOR = 64
CAPTION_SIZE_FRACTION = 0.064


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


def talking_windows(duration: float) -> dict[str, tuple[float, float]]:
    """Title, caption body, and end-card spans on the assembled cut.

    The lower-third slot is always empty: captions occupy the middle, not
    a second copy of the title. On a very short cut the three spans split
    the timeline in thirds so they still do not overlap.
    """
    duration = max(0.0, float(duration))
    empty = (0.0, 0.0)
    if duration <= 0:
        return {"title": empty, "lower": empty, "captions": empty, "end": empty}
    plate = TALKING_PLATE_S if duration >= 8 else max(1.0, duration * 0.25)
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


def _clip_pair(start: float, end: float, body_start: float, body_end: float):
    start = max(float(start), body_start)
    end = min(float(end), body_end)
    if end - start < 0.2:
        return None
    return start, end


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
        if midpoint < body_start or midpoint >= body_end:
            continue
        clipped = _clip_pair(start, end, body_start, body_end)
        if clipped is None:
            clipped = (max(start, body_start), max(end, body_start))
        kept.append((clipped[0], max(clipped[1], clipped[0]), text))
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
    outside that body are dropped. No line is added that the transcript
    did not contain.
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
        if midpoint < body_start or midpoint >= body_end:
            continue
        clipped = _clip_pair(start, end, body_start, body_end)
        if clipped is None:
            continue
        for line, line_start, line_end in _split_timed(
            text, clipped[0], clipped[1], max_chars, max_words,
        ):
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


def _alignment(position: str) -> int:
    if position in ("middle", "center"):
        return 5
    if position == "top":
        return 8
    if position == "bottom":
        return 2
    return 5


def caption_style(brief: Brief, height: int) -> dict:
    """Resolved caption look. Defaults stay readable when the brief is quiet."""
    _bar, ink = brand_colors(brief.colors)
    color = brief.caption_color or f"#{ink}"
    stroke = brief.caption_stroke or "#000000"
    width = brief.caption_stroke_width or 3
    size = brief.caption_size or max(
        CAPTION_SIZE_FLOOR, int(height * CAPTION_SIZE_FRACTION),
    )
    return {
        "color": color,
        "stroke": stroke,
        "stroke_width": width,
        "size": size,
        "position": brief.caption_position or "center",
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
    style = caption_style(brief, height)
    face, _folder = _font_face(brief, font_path)
    align = _alignment(style["position"])
    margin_v = 0 if style["position"] in ("middle", "center") else 48
    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "WrapStyle: 0",
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
            f"{_ass_color(style['stroke'])},&H64000000,"
            f"0,0,0,0,100,100,0,0,1,{style['stroke_width']},0,"
            f"{align},40,40,{margin_v},1"
        ),
        "",
        "[Events]",
        ("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
         "Effect, Text"),
    ])
    lines = [header]
    for cue in cues:
        lines.append(
            "Dialogue: 0,"
            f"{ass_timestamp(cue.start)},{ass_timestamp(cue.end)},"
            f"Caption,,0,0,0,,{_ass_text(cue.text)}"
        )
    return "\n".join(lines) + "\n"


def _filter_path(path: str) -> str:
    return (path.replace("\\", "/")
                .replace(":", r"\:")
                .replace("'", r"\'")
                .replace(",", r"\,")
                .replace("[", r"\[")
                .replace("]", r"\]"))


def transcribe_captions(path: str, model: str = "base", log_fn=print) -> dict:
    """Word-timed speech from a local Whisper build.

    ``faster-whisper`` when it imports, otherwise ``openai-whisper``.
    Returns ``{"engine", "segments"}``. Segments that fail the app's
    speech check are dropped. The text is whatever the model heard.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        WhisperModel = None
    if WhisperModel is not None:
        log_fn(f"Captions: local faster-whisper ({model}).")
        whisper_model = WhisperModel(model, device="cpu", compute_type="int8")
        segments, _info = whisper_model.transcribe(path, word_timestamps=True)
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
                   error: str | None = None) -> dict:
    rows = [cue.as_dict() if isinstance(cue, CaptionCue) else cue for cue in (cues or [])]
    return {
        "requested": brief.style == "talking",
        "burned": burned,
        "source": "speech",
        "engine": engine,
        "error": error,
        "count": len(rows),
        "cues": rows,
    }


def apply_talking_pack(src: str, dst: str, brief: Brief, duration: float,
                       cues: list[CaptionCue], log_fn=print) -> str:
    """Draw the title, the spoken captions, and the call to action.

    One ffmpeg pass. Captions go through an ASS script (libass). Title and
    CTA sit in the middle of the frame on a solid plate. Raises when
    ffmpeg fails; the caller keeps the unbranded cut.
    """
    from modules.system.app_paths import ffmpeg_exe

    windows = talking_windows(duration)
    font = ""
    if brief.wants_brand() or cues:
        font = resolve_font(brief.caption_font or brief.font, log_fn=log_fn)
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
        if not font:
            raise RuntimeError(
                "No font for captions. Set FONT or CAPTION_FONT to a .ttf path or a font name."
            )
        script = render_ass(list(cues), brief, width=width, height=height, font_path=font)
        handle, ass_path = tempfile.mkstemp(suffix=".ass", prefix="vh_cap_")
        os.close(handle)
        with open(ass_path, "w", encoding="utf-8") as handle:
            handle.write(script)
        _face, fontsdir = _font_face(brief, font)
        subtitle = f"subtitles={_filter_path(ass_path)}"
        if fontsdir:
            subtitle += f":fontsdir={_filter_path(fontsdir)}"
        graphs.append(subtitle)

    if not graphs:
        shutil.copy2(src, dst)
        return dst

    log_fn(
        f"Talking pack: title {windows['title'][0]:.1f}-{windows['title'][1]:.1f}s, "
        f"{len(cues)} caption line(s), "
        f"CTA {windows['end'][0]:.1f}-{windows['end'][1]:.1f}s."
    )
    try:
        result = subprocess.run(
            [ffmpeg_exe(), "-y", "-v", "error", "-i", src, "-vf", ",".join(graphs),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "copy", dst],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900,
        )
    finally:
        if ass_path and os.path.exists(ass_path):
            os.remove(ass_path)
    if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "ffmpeg failed"
        raise RuntimeError(f"Talking pack failed: {detail}")
    return dst
