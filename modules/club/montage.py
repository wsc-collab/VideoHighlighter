"""Rank windows in a folder of real clips and assemble ``draft.mp4``.

Signals, in the order a talking pack prefers them after any must-include:

1. Speech from local Whisper (how much of the window is someone talking).
2. Motion inside those talking windows.
3. KEYWORDS from the brief.
4. Audio energy, a weak tie-break. A same-session lesson mic is not dropped.
   A file far under the loudest, the usual phone-next-to-a-boom case, gets
   a small score haircut and stays in the pool.

``hype`` still weights peaks and motion.
Both styles only cut ranges that already exist in the supplied files and
concatenate those ranges. Weights change which windows are kept. They do
not create pictures, faces, voices, or songs.

When the brief sets ``TITLE``, ``SUBTITLE``, or ``CTA``, a second ffmpeg
pass draws that type on the assembled cut. ``STYLE: talking`` also burns
captions of the speech between the title and the call to action. Those
words come from local Whisper. ``NOTES`` is copied into the JSON for the
editor. It is not a prompt and it is not caption text.

``pick`` (or ``--transcript-only``) writes a transcript and stops, so a
person can choose lines before anything is cut. ``MUST_INCLUDE`` and
``INCLUDE_WINDOWS`` force those moments in. The ranker still fills the
rest of LENGTH. On a talking cut that fill is high speech, then action.
A silent or low-motion window ranks at the bottom and is not used in
the middle of the pack while talking or action is still available. A
Whisper line that is digit spam or has no words is not speech, so it
cannot score a window as someone talking. A file whose mean volume is
room tone cannot fill a talking gap either, even when the picture is
moving and the transcript claims the whole file is speech. When LENGTH
still needs time, a must-include grows along the real speech after it
instead of taking that quiet clip. A setup line (pause, hold, check
feet, and the same kind of foreshadow: watch, ready, finish, here we
go) holds that window open two more seconds so the beat is not cut off.
That hold is the talking default for every clip, not a KEYWORDS match. A
kept highlight that is missing from the final cut stops the run. With
neither field, the ranker chooses on its own. Talking playback follows
clip name and time, except the opener: that cut is the ``TITLE_UNDER``
/ ``OPEN_SCENE`` match when the brief names who is under the title
(a slash separates alternatives), otherwise a must-include, otherwise
a window with speech, so a silent still does not sit under the title.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from modules.club.brief import Brief, BriefError, load_brief
from modules.club.brand import apply_brand, brand_record
from modules.club.pick import (
    ForcedSpan,
    files_from_payload,
    load_transcript,
    render_transcript_md,
    resolve_includes,
    segments_of,
    transcript_payload,
)
from modules.club.captions import (
    apply_talking_pack,
    caption_cues,
    caption_record,
    parse_captions_md,
    render_captions_md,
    talking_windows,
    transcribe_captions,
)

# ``base`` mis-heard short coaching cues ("finish" as "if I", and a Spanish
# line). ``small`` is the talking-pack default. ``--whisper-model medium``
# is the larger step when a take is still muddy.
DEFAULT_WHISPER_MODEL = "small"


# Stated on every run so a later reader can see what the files are.
ASSEMBLE_ONLY = {
    "assemble_only": True,
    "generated_broll": False,
    "generated_faces": False,
    "generated_voices": False,
    "generated_songs": False,
    "note": (
        "Windows are cut from the supplied clips and concatenated. "
        "On-screen type, when the brief asks for it, is drawn on that cut. "
        "Talking captions are the speech in that cut, not written copy. "
        "Nothing is synthesized: no B-roll, faces, voices, or songs."
    ),
}

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
# Written into the clips folder on a previous run. Never a source.
OUTPUT_NAMES = {"draft.mp4", "cuts.json", "scores.json", "transcript.md", "transcript.json"}

# (window seconds, hop seconds). Short punches for hype, longer holds for talk.
STYLE_WINDOW = {
    "hype": (4.0, 2.0),
    "talking": (8.0, 4.0),
}

# Talking: speech outranks everything that can stack on a silent window.
# Motion is next, then keywords, then audio energy. The numbers are the
# most each signal can add when it is fully on (0–1).
STYLE_WEIGHTS = {
    "hype": {"audio": 1.0, "motion": 1.0, "speech": 0.25, "keyword": 0.75},
    "talking": {"audio": 0.12, "motion": 1.0, "speech": 4.0, "keyword": 0.45},
}

MOTION_SAMPLE_FPS = 4.0
MOTION_WIDTH = 96

# A same-session lesson mic sits inside this spread, so a few dB of
# difference is not a reason to hide a clip. A file this far under the
# loudest is the usual phone-next-to-a-boom case: it stays in the ranker
# with a small score haircut, and speech can still win.
MIXED_GAP_DB = 18.0
MIC_SOFT_FACTOR = 0.85
# Below both of these, a window is a silent still. The title does not open
# on one when a must-include or a window with speech is in the cut.
OPEN_SPEECH_FLOOR = 0.08
OPEN_MOTION_FLOOR = 0.05
# Filler has to be mostly someone talking. An 8s grid window with a short
# cue in it stays under this and is not used to plug a gap: that cue is
# recut tight to the speech instead. Motion ranks action among windows
# that already clear the floor. It does not rescue a silent window.
FILL_SPEECH_FLOOR = 0.35
# Below this, motion is not action. A window under both floors is a silent
# still: it ranks at the bottom and is not a mid-pack filler while a
# talking or action window is still available.
ACTION_FLOOR = 0.15
# ffmpeg ``volumedetect`` mean at or below this is room tone, not a soft
# mic. A phone 18 dB under the boom is still louder and can fill on real
# speech. A file near -56 dB cannot win a talking gap, even if Whisper
# covered it and the picture moved.
SILENCE_MEAN_DB = -50.0
FILL_MIN_S = 1.0
FILL_MAX_S = 8.0
FILL_GAP_S = 0.6
FILL_PAD_S = 0.2
_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


@dataclass
class Window:
    source: str
    path: str
    start: float
    end: float
    audio: float
    motion: float
    speech: float
    keyword: float
    keyword_hits: list[str] = field(default_factory=list)
    score: float = 0.0
    trimmed: bool = False
    forced: bool = False
    include: str = ""
    kind: str = ""
    heard: str = ""
    # False when the file's mean volume is room tone. Motion and a fake
    # transcript do not make that window a talking fill.
    audible: bool = True

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self, *, selected: bool = False, play_index: int | None = None) -> dict:
        payload = {
            "source": self.source,
            "start": _round(self.start),
            "end": _round(self.end),
            "duration": _round(self.duration),
            "audio": _round(self.audio),
            "motion": _round(self.motion),
            "speech": _round(self.speech),
            "keyword": _round(self.keyword),
            "keyword_hits": list(self.keyword_hits),
            "score": _round(self.score),
            "trimmed": self.trimmed,
            "selected": selected,
        }
        if self.forced:
            payload["forced"] = True
            payload["include"] = self.include
            if self.kind:
                payload["kind"] = self.kind
        if play_index is not None:
            payload["play_index"] = play_index
        return payload


def _round(value: float) -> float:
    return round(float(value), 3)


def measure_mean_volume_db(path: str) -> float | None:
    """Mean volume in dB from ffmpeg ``volumedetect``, or ``None`` if unread."""
    from modules.system.app_paths import ffmpeg_exe
    try:
        result = subprocess.run(
            [ffmpeg_exe(), "-nostdin", "-v", "info", "-i", path,
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _MEAN_VOLUME.search((result.stderr or "") + (result.stdout or ""))
    if not match:
        return None
    return float(match.group(1))


def quiet_clip_names(readings, gap_db: float = MIXED_GAP_DB) -> list[str]:
    """Names far enough under the loudest reading to count as a soft extra.

    ``readings`` is ``(name, mean_db or None)`` in folder order. ``None``
    is not marked: a file that could not be metered is not a bad mic.
    The loudest file always stays, so a one-file folder is unchanged.
    A folder whose quietest file is inside the gap is one lesson mic and
    nothing is marked. Marked files are not removed. The ranker applies
    ``MIC_SOFT_FACTOR`` and still lets speech win.
    """
    known = [db for _name, db in readings if db is not None]
    if len(known) < 2:
        return []
    loudest = max(known)
    return [
        name for name, db in readings
        if db is not None and loudest - db >= gap_db
    ]


def list_clips(folder: str | Path) -> list[Path]:
    """Video files sitting directly in ``folder``, in name order.

    Subfolders are left alone so an export directory is not scored as source.
    ``draft.mp4`` from a previous run is not a source.
    """
    root = Path(folder)
    clips = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file():
            continue
        if path.name.lower() in OUTPUT_NAMES or path.name.startswith("vh_brand_"):
            continue
        if path.suffix.lower() in VIDEO_SUFFIXES:
            clips.append(path)
    return clips


def iter_windows(duration: float, window: float, hop: float):
    """Yield ``(start, end)`` covering ``duration``.

    A clip shorter than the window is one window. Otherwise windows step by
    ``hop``, and a final window is anchored to the end when the last step
    would leave a tail unscored.
    """
    if duration <= 0 or window <= 0:
        return
    if duration <= window:
        yield 0.0, duration
        return
    hop = hop if hop > 0 else window
    starts = []
    t = 0.0
    while t + window <= duration + 1e-6:
        starts.append(t)
        t += hop
    tail = duration - window
    if not starts or tail > starts[-1] + 0.25:
        starts.append(tail)
    for start in starts:
        yield start, min(duration, start + window)


def peaks_per_second(peak_times, duration: float) -> list[float]:
    """Count of audio-peak timestamps in each whole second."""
    n = max(1, int(math.ceil(duration))) if duration > 0 else 0
    counts = [0.0] * n
    for raw in peak_times or []:
        t = float(raw)
        if t < 0:
            continue
        sec = int(t)
        if sec >= n:
            sec = n - 1
        if n:
            counts[sec] += 1.0
    return counts


def normalise(values: list[float]) -> list[float]:
    """Scale a per-second series so its busiest second is 1."""
    if not values:
        return []
    peak = max(values)
    if peak <= 0:
        return [0.0] * len(values)
    return [v / peak for v in values]


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def window_mean(series: list[float], start: float, end: float) -> float:
    """Time-weighted mean of a per-second series over ``[start, end)``."""
    if not series or end <= start:
        return 0.0
    acc = 0.0
    covered = 0.0
    for i, value in enumerate(series):
        ov = _overlap(start, end, i, i + 1)
        if ov <= 0:
            continue
        acc += float(value) * ov
        covered += ov
    if covered <= 0:
        return 0.0
    return acc / covered


# A real word is letters, at least two of them. Digits are not words:
# "1,5,5,5,5" is Whisper filling silence, not someone talking.
_LETTER_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_FILLER_WORD = re.compile(
    r"^(?:oh+|ah+|ha+|um+|uh+|hmm+|mm+|mhm+)$",
    re.IGNORECASE,
)


def is_real_speech_text(text: str) -> bool:
    """True when transcript text is words, not silence junk.

    Digit spam (``1,5,5,5,5``), a blank line, and filler with no words do
    not count as someone talking. A short real cue still does. A line that
    is mostly digits does not become speech because one stray word is in it.
    """
    raw = " ".join(str(text or "").split())
    if len(raw) < 2:
        return False
    words = [w for w in _LETTER_WORD.findall(raw) if not _FILLER_WORD.fullmatch(w)]
    if not words:
        return False
    digits = len(re.findall(r"\d", raw))
    letters = len(re.findall(r"[^\W\d_]", raw, flags=re.UNICODE))
    if digits >= 4 and digits > letters:
        return False
    return True


def _real_speech_segments(segments) -> list:
    return [
        seg for seg in segments or []
        if is_real_speech_text(str(seg.get("text") or ""))
    ]


def clip_is_audible(mean_db: float | None) -> bool:
    """False when a metered file is room tone.

    ``None`` stays audible: a file that could not be metered is not
    treated as silence.
    """
    if mean_db is None:
        return True
    return float(mean_db) > SILENCE_MEAN_DB


def speech_coverage(segments, start: float, end: float) -> float:
    """Share of ``[start, end)`` covered by real speech.

    A Whisper segment whose text is digit spam or has no words does not
    count. One junk line across a whole file is not speech coverage 1.
    """
    if not segments or end <= start:
        return 0.0
    covered = 0.0
    for seg in _real_speech_segments(segments):
        covered += _overlap(start, end, float(seg["start"]), float(seg["end"]))
    return min(1.0, covered / (end - start))


def _contains_keyword(haystack: str, keyword: str) -> bool:
    """Match a keyword in transcript text.

    A single word uses a boundary so ``ace`` does not fire inside ``place``.
    A phrase is a plain substring so ``match point`` still matches.
    """
    needle = keyword.casefold()
    if not needle:
        return False
    if re.search(r"\s", needle):
        return needle in haystack
    return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None


def keyword_hits(segments, start: float, end: float, keywords) -> list[str]:
    if not keywords or not segments:
        return []
    texts = []
    for seg in _real_speech_segments(segments):
        if _overlap(start, end, float(seg["start"]), float(seg["end"])) > 0:
            texts.append(str(seg.get("text") or ""))
    haystack = "\n".join(texts).casefold()
    return [kw for kw in keywords if _contains_keyword(haystack, kw)]


def keyword_strength(hits: list[str], cap: int = 3) -> float:
    if cap <= 0:
        return 0.0
    return min(1.0, len(hits) / cap)


def score_windows_for_clip(
    *,
    source: str,
    path: str,
    duration: float,
    style: str,
    keywords,
    peak_times,
    motion_series: list[float],
    segments,
) -> list[Window]:
    """Score every candidate window in one clip. Series are raw, not weighted."""
    weights = STYLE_WEIGHTS[style]
    window, hop = STYLE_WINDOW[style]
    # A requested draft shorter than the style window still has to fit.
    audio = normalise(peaks_per_second(peak_times, duration))
    motion = normalise(list(motion_series or []))
    scored = []
    for start, end in iter_windows(duration, window, hop):
        hits = keyword_hits(segments, start, end, keywords)
        audio_v = window_mean(audio, start, end)
        motion_v = window_mean(motion, start, end)
        speech_v = speech_coverage(segments, start, end)
        keyword_v = keyword_strength(hits)
        # Motion only helps a window that is already someone talking.
        # A silent swing does not outrank the coach.
        motion_for_score = motion_v * speech_v if style == "talking" else motion_v
        total = (
            weights["audio"] * audio_v
            + weights["motion"] * motion_for_score
            + weights["speech"] * speech_v
            + weights["keyword"] * keyword_v
        )
        scored.append(Window(
            source=source,
            path=path,
            start=start,
            end=end,
            audio=audio_v,
            motion=motion_v,
            speech=speech_v,
            keyword=keyword_v,
            keyword_hits=hits,
            score=total,
        ))
    return scored


def _same_source_overlap(a: Window, b: Window) -> bool:
    return a.source == b.source and a.start < b.end - 1e-3 and b.start < a.end - 1e-3


def is_silent_still(window: Window) -> bool:
    """True when a window is neither talking nor action.

    These rank under every talking or moving window. They are not used to
    fill the middle of a pack while one of those is still available.
    Room tone is a silent still even when speech was scored at 1 and the
    picture moved: that score came from a transcript, not from the audio.
    """
    if not window.audible:
        return True
    return window.speech < FILL_SPEECH_FLOOR and window.motion < ACTION_FLOOR


def _talking_pack_rank(window: Window):
    """Talking, then action, then score. Silent stills sort last."""
    dead = 1 if is_silent_still(window) else 0
    return (
        dead,
        -window.speech,
        -window.motion,
        -window.score,
        window.source.casefold(),
        window.start,
    )


def _skip_silent_filler(
    window: Window,
    pool: list[Window],
    chosen: list[Window],
    prior: list[Window] | None = None,
) -> bool:
    """Skip a silent still when the pack already has, or can still take, talking or action.

    A dead clip is not how the length gets finished once a real window is in
    the cut, and it is not taken ahead of one that is still available.
    ``prior`` is the must-includes already kept, which are not in ``chosen``
    while filler is being picked.
    """
    if not is_silent_still(window):
        return False
    held = [*(prior or ()), *chosen]
    if any(kept.forced or not is_silent_still(kept) for kept in held):
        return True
    for other in pool:
        if other is window or is_silent_still(other):
            continue
        if any(_same_source_overlap(other, kept) for kept in chosen):
            continue
        return True
    return False


def select_windows(
    windows: list[Window],
    min_s: float,
    max_s: float,
    target: float | None = None,
    rank=None,
    prefer_alive: bool = False,
    prior: list[Window] | None = None,
) -> list[Window]:
    """Greedy non-overlapping pick until the middle of the requested range.

    The draft stays inside ``max_s``. Once ``min_s`` is met, a window that
    does not fit is skipped rather than trimmed. Below ``min_s``, the next
    window may be shortened to the remaining room when at least one second
    is left. The result is score order; callers reorder for playback.

    ``target`` overrides the midpoint. Highlighted windows use that so the
    filler stops where the original brief's midpoint still is. ``rank``
    replaces the score sort. Talking filler uses it so speech, then
    action, leads, and a loud silent window cannot.

    ``prefer_alive`` skips a silent still while a talking or action window
    is still available, and it will trim that talking window into the
    leftover room instead of plugging the hole with silence. ``prior``
    counts as already kept, so a must-include blocks a silent filler even
    though that filler is chosen from its own pool.
    """
    def _by_score(window: Window):
        return (-window.score, window.source.casefold(), window.start)

    ranked = sorted(windows, key=rank or _by_score)
    if target is None:
        target = (min_s + max_s) / 2.0
    chosen: list[Window] = []
    total = 0.0
    for window in ranked:
        if any(_same_source_overlap(window, kept) for kept in chosen):
            continue
        if prefer_alive and _skip_silent_filler(window, windows, chosen, prior):
            continue
        room = max_s - total
        if room <= 0.05:
            break
        dur = window.duration
        if dur <= 0:
            continue
        piece = window
        alive = prefer_alive and not is_silent_still(window)
        if dur > room + 1e-3:
            if room < 1.0 or (total >= min_s and not alive):
                continue
            piece = Window(
                source=window.source,
                path=window.path,
                start=window.start,
                end=window.start + room,
                audio=window.audio,
                motion=window.motion,
                speech=window.speech,
                keyword=window.keyword,
                keyword_hits=list(window.keyword_hits),
                score=window.score,
                trimmed=True,
                forced=window.forced,
                include=window.include,
                kind=window.kind,
                heard=window.heard,
                audible=window.audible,
            )
        chosen.append(piece)
        total += piece.duration
        if total >= target - 1e-6:
            break
    return chosen


def _talking_fill_rank(window: Window):
    """Speech first, then action. Silent stills sort last, not into the middle."""
    return _talking_pack_rank(window)


def is_talking_fill(window: Window) -> bool:
    """True when a window can fill length on a talking cut.

    High speech qualifies on its own. Action (motion) ranks those windows
    but does not admit a silent swing. A window under the speech floor is
    the silent or low-motion pause and stays out. Room tone does not fill
    even when speech coverage was scored at 1.
    """
    if not window.audible:
        return False
    return window.speech >= FILL_SPEECH_FLOOR


def _blocked_ranges(source: str, blocked: list[Window]) -> list[tuple[float, float]]:
    spans = sorted(
        (window.start, window.end)
        for window in blocked
        if window.source == source and window.end > window.start
    )
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1e-3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _free_ranges(duration: float, blocked: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cursor = 0.0
    free: list[tuple[float, float]] = []
    for start, end in blocked:
        if start > cursor + 1e-3:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + 1e-3:
        free.append((cursor, duration))
    return [(start, end) for start, end in free if end - start >= 0.8]


def _text_between(segments, start: float, end: float) -> str:
    parts = []
    for seg in segments or []:
        if not is_real_speech_text(str(seg.get("text") or "")):
            continue
        if _overlap(start, end, float(seg.get("start") or 0), float(seg.get("end") or 0)) <= 0:
            continue
        text = " ".join(str(seg.get("text") or "").split())
        if text:
            parts.append(text)
    return " ".join(parts)


def _nearest_scored(source: str, start: float, end: float, windows: list[Window]) -> Window | None:
    best = None
    best_ov = 0.0
    for window in windows:
        if window.source != source:
            continue
        ov = _overlap(start, end, window.start, window.end)
        if ov > best_ov:
            best_ov = ov
            best = window
    if best is not None:
        return best
    for window in windows:
        if window.source == source and window.path:
            return window
    return None


def _speech_runs(segments, free: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces: list[tuple[float, float]] = []
    for seg in _real_speech_segments(segments):
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        if end <= start:
            continue
        for left, right in free:
            ov0 = max(start, left)
            ov1 = min(end, right)
            if ov1 - ov0 >= 0.3:
                pieces.append((ov0, ov1))
    if not pieces:
        return []
    pieces.sort()
    merged = [pieces[0]]
    for start, end in pieces[1:]:
        if start <= merged[-1][1] + FILL_GAP_S:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    runs: list[tuple[float, float]] = []
    for start, end in merged:
        host = next((span for span in free if start >= span[0] - 1e-6 and end <= span[1] + 1e-6), None)
        if host is None:
            continue
        pad_s = max(host[0], start - FILL_PAD_S)
        pad_e = min(host[1], end + FILL_PAD_S)
        if pad_e - pad_s < FILL_MIN_S:
            need = FILL_MIN_S - (pad_e - pad_s)
            grow_left = min(pad_s - host[0], need / 2.0)
            grow_right = min(host[1] - pad_e, need / 2.0)
            pad_s -= grow_left
            pad_e += grow_right
            leftover = FILL_MIN_S - (pad_e - pad_s)
            if leftover > 0:
                take = min(pad_s - host[0], leftover)
                pad_s -= take
                leftover -= take
                pad_e += min(host[1] - pad_e, leftover)
        if pad_e - pad_s < 0.8:
            continue
        length = pad_e - pad_s
        if length <= FILL_MAX_S + 1e-3:
            runs.append((pad_s, pad_e))
            continue
        before = len(runs)
        hop = FILL_MAX_S / 2.0
        t = pad_s
        while t < pad_e - 0.8:
            stop = min(pad_e, t + FILL_MAX_S)
            if stop - t >= FILL_MIN_S:
                runs.append((t, stop))
            if stop >= pad_e - 1e-3:
                break
            nxt = t + hop
            if nxt <= t:
                break
            t = nxt
        if len(runs) > before and pad_e - runs[-1][1] >= FILL_MIN_S:
            runs.append((max(runs[-1][1], pad_e - FILL_MAX_S), pad_e))
    return runs


def speech_fill_windows(
    windows: list[Window],
    blocked: list[Window],
    segments_by_source: dict | None,
    durations: dict | None = None,
    soft_sources: set | None = None,
    inaudible_sources: set | None = None,
) -> list[Window]:
    """Tight talking windows in the time must-includes left open.

    The 8s grid often overlaps a short highlight and then loses the speech
    beside it. These windows are cut to the speech that is still free, so
    the ranker can fill LENGTH with that talking instead of a silent clip.
    """
    if not segments_by_source:
        return []
    durations = durations or {}
    soft = soft_sources or set()
    quiet = inaudible_sources or set()
    found: list[Window] = []
    sources = set(segments_by_source) | {window.source for window in windows}
    for source in sources:
        segments = segments_by_source.get(source) or []
        if not segments:
            continue
        duration = float(durations.get(source) or 0)
        if duration <= 0:
            ends = [window.end for window in windows if window.source == source]
            ends += [float(seg.get("end") or 0) for seg in segments]
            duration = max(ends) if ends else 0.0
        if duration <= 0:
            continue
        free = _free_ranges(duration, _blocked_ranges(source, blocked))
        weights = STYLE_WEIGHTS["talking"]
        for start, end in _speech_runs(segments, free):
            parent = _nearest_scored(source, start, end, windows)
            speech_v = speech_coverage(segments, start, end)
            motion_v = parent.motion if parent is not None else 0.0
            audio_v = parent.audio if parent is not None else 0.0
            keyword_v = parent.keyword if parent is not None else 0.0
            hits = list(parent.keyword_hits) if parent is not None else []
            path = parent.path if parent is not None and parent.path else source
            motion_for_score = motion_v * speech_v
            score = (
                weights["audio"] * audio_v
                + weights["motion"] * motion_for_score
                + weights["speech"] * speech_v
                + weights["keyword"] * keyword_v
            )
            if source in soft:
                score *= MIC_SOFT_FACTOR
            window = Window(
                source=source,
                path=path,
                start=start,
                end=end,
                audio=audio_v,
                motion=motion_v,
                speech=speech_v,
                keyword=keyword_v,
                keyword_hits=hits,
                score=score,
                heard=_text_between(segments, start, end),
                audible=source not in quiet,
            )
            if is_talking_fill(window):
                found.append(window)
    return found


def _speech_extend_limit(window: Window, windows: list[Window], duration: float | None) -> float:
    """How far ``window`` may grow without crossing the next cut or the file."""
    limit = None
    if duration and duration > window.end:
        limit = float(duration)
    for other in windows:
        if other is window or other.source != window.source:
            continue
        if other.start >= window.end - 1e-3:
            limit = other.start if limit is None else min(limit, other.start)
    if limit is None:
        return window.end
    return limit


def _chain_speech_end(end: float, segments, limit: float, target: float) -> float:
    """Walk ``end`` forward through real speech that sits against it.

    A gap bigger than ``FILL_GAP_S`` stops the walk. The end stays inside
    ``limit`` and does not pass ``target``.
    """
    cursor = end
    cap = min(limit, target)
    if cap <= cursor + 0.02:
        return end
    progressed = True
    while progressed and cursor < cap - 0.02:
        progressed = False
        for seg in segments:
            start = float(seg.get("start") or 0)
            stop = float(seg.get("end") or start)
            if stop <= cursor + 0.02:
                continue
            if start > cursor + FILL_GAP_S + 1e-6:
                continue
            nxt = min(cap, stop + FILL_PAD_S)
            if nxt > cursor + 0.02:
                cursor = nxt
                progressed = True
    return cursor


def extend_forced_along_speech(
    windows: list[Window],
    segments_by_source: dict | None,
    durations: dict | None,
    need_s: float,
    log_fn=None,
    inaudible_sources: set | None = None,
) -> list[Window]:
    """Grow must-includes along the real speech that follows them.

    LENGTH padding comes from that speech. A later quiet clip is not how
    the gap is filled. The start stays put so the forced window still
    matches. Growth stops at the next cut on the same file, at the end of
    the speech, or once ``need_s`` is covered. A room-tone file is not
    grown: the must-include stays, and the silence after it does not.
    """
    if need_s <= 0.05 or not segments_by_source:
        return windows
    log_fn = log_fn or (lambda *_a, **_k: None)
    durations = durations or {}
    quiet = inaudible_sources or set()
    result = list(windows)
    order = sorted(
        range(len(result)),
        key=lambda i: (result[i].source.casefold(), result[i].start),
    )
    remaining = need_s
    for index in order:
        if remaining <= 0.05:
            break
        window = result[index]
        if window.kind != "must_include" or not window.audible:
            continue
        if window.source in quiet:
            continue
        segments = _real_speech_segments(segments_by_source.get(window.source) or [])
        if not segments:
            continue
        limit = _speech_extend_limit(window, result, durations.get(window.source))
        new_end = _chain_speech_end(window.end, segments, limit, window.end + remaining)
        if new_end <= window.end + 0.05:
            continue
        log_fn(
            f"Extending {window.source} {window.start:.2f}-{new_end:.2f}s "
            "along the speech after the must-include."
        )
        result[index] = _with_end(window, new_end)
        remaining -= new_end - window.end
    return result


def select_with_forced(
    windows: list[Window],
    forced: list[Window],
    min_s: float,
    max_s: float,
    *,
    style: str = "",
    segments_by_source: dict | None = None,
    durations: dict | None = None,
    soft_sources: set | None = None,
    inaudible_sources: set | None = None,
    log_fn=None,
) -> list[Window]:
    """Keep every highlighted window, then fill toward the brief's midpoint.

    No highlights means the ranker alone, same as ``select_windows``.
    Highlights that already reach the midpoint are not padded. They are
    kept even when they run past ``max_s``: the person asked for those
    moments. Filler windows that overlap a highlight are left out.

    A talking brief still fills what the highlights did not cover. The
    filler has to be high speech (action breaks the tie). Silent and
    low-motion windows stay out, so a must-include gap is not a pause.
    Room tone stays out even when its transcript says the file is all
    speech. A must-include is extended along the real speech after it
    before any other filler is considered.
    """
    if not forced:
        if style != "talking":
            return select_windows(windows, min_s, max_s)
        pool = list(windows)
        pool.extend(speech_fill_windows(
            windows, [], segments_by_source, durations, soft_sources,
            inaudible_sources,
        ))
        return select_windows(
            pool, min_s, max_s, rank=_talking_pack_rank, prefer_alive=True,
        )
    chosen = list(forced)
    total = sum(window.duration for window in chosen)
    midpoint = (min_s + max_s) / 2.0
    if style == "talking" and total < midpoint - 1e-6 and total < max_s - 0.05:
        chosen = extend_forced_along_speech(
            chosen, segments_by_source, durations,
            min(midpoint, max_s) - total,
            log_fn=log_fn,
            inaudible_sources=inaudible_sources,
        )
        total = sum(window.duration for window in chosen)
    if total >= midpoint - 1e-6 or total >= max_s - 0.05:
        result = chosen
    else:
        pool = [
            window for window in windows
            if not any(_same_source_overlap(window, kept) for kept in chosen)
        ]
        rank = None
        prefer_alive = False
        if style == "talking":
            pool.extend(speech_fill_windows(
                windows, chosen, segments_by_source, durations, soft_sources,
                inaudible_sources,
            ))
            rank = _talking_pack_rank
            prefer_alive = True
        room = max_s - total
        filler_target = midpoint - total
        filler = select_windows(
            pool, filler_target, max(filler_target, room),
            target=filler_target, rank=rank, prefer_alive=prefer_alive,
            prior=chosen if prefer_alive else None,
        )
        result = chosen + filler
    assert_forced_windows_kept(forced, result)
    return result


def _with_end(window: Window, end: float) -> Window:
    if abs(window.end - end) <= 0.02:
        return window
    return Window(
        source=window.source,
        path=window.path,
        start=window.start,
        end=end,
        audio=window.audio,
        motion=window.motion,
        speech=window.speech,
        keyword=window.keyword,
        keyword_hits=list(window.keyword_hits),
        score=window.score,
        trimmed=window.trimmed or end < window.end - 0.02,
        forced=window.forced,
        include=window.include,
        kind=window.kind,
        heard=window.heard,
        audible=window.audible,
    )


def _with_range(window: Window, start: float, end: float) -> Window:
    if abs(window.start - start) <= 0.02 and abs(window.end - end) <= 0.02:
        return window
    return Window(
        source=window.source,
        path=window.path,
        start=start,
        end=end,
        audio=window.audio,
        motion=window.motion,
        speech=window.speech,
        keyword=window.keyword,
        keyword_hits=list(window.keyword_hits),
        score=window.score,
        trimmed=True,
        forced=window.forced,
        include=window.include,
        kind=window.kind,
        heard=window.heard,
        audible=window.audible,
    )


def extend_coaching_beats(
    windows: list[Window],
    segments_by_source: dict | None,
    durations: dict | None = None,
    log_fn=print,
) -> list[Window]:
    """Hold every talking window open two seconds after a setup line.

    Pause, hold, and check-feet started this. The same gap follows a
    foreshadow ("watch this", "ready", "finish") so the cut does not end
    on the cue. It runs on every kept clip. Brief KEYWORDS are not
    consulted. A later must-include on the same clip keeps its start;
    anything else moves out of the way.
    """
    from modules.club.pick import extend_coaching_window

    segments_by_source = segments_by_source or {}
    durations = durations or {}
    extended: list[Window] = []
    for window in windows:
        start, end = extend_coaching_window(
            window.start,
            window.end,
            durations.get(window.source),
            segments_by_source.get(window.source) or [],
        )
        if end > window.end + 0.05:
            log_fn(
                f"Holding {window.source} {window.start:.2f}-{end:.2f}s "
                "so the coaching beat after the cue can land."
            )
        extended.append(_with_end(window, end))
    return _resolve_coaching_overlaps(extended)


def _resolve_coaching_overlaps(windows: list[Window], drop_short: bool = True) -> list[Window]:
    """Keep an extended beat from landing on top of the next cut."""
    groups: dict[str, list[int]] = {}
    for index, window in enumerate(windows):
        groups.setdefault(window.source, []).append(index)
    drop: set[int] = set()
    for indexes in groups.values():
        indexes.sort(key=lambda index: windows[index].start)
        for pos in range(len(indexes) - 1):
            prev_i = indexes[pos]
            next_i = indexes[pos + 1]
            if prev_i in drop or next_i in drop:
                continue
            prev = windows[prev_i]
            nxt = windows[next_i]
            if prev.end <= nxt.start + 1e-3:
                continue
            if nxt.forced:
                windows[prev_i] = _with_end(prev, min(prev.end, nxt.start))
            else:
                moved = _with_range(nxt, max(nxt.start, prev.end), nxt.end)
                if drop_short and moved.duration < 0.8:
                    drop.add(next_i)
                    continue
                windows[next_i] = moved
    return [
        window for index, window in enumerate(windows)
        if index not in drop and (window.forced or window.duration >= 0.3)
    ]


def _forced_window(span: ForcedSpan) -> Window:
    return Window(
        source=span.source,
        path=span.path,
        start=span.start,
        end=span.end,
        audio=0.0,
        motion=0.0,
        speech=1.0,
        keyword=0.0,
        score=0.0,
        forced=True,
        include=span.include,
        kind=span.kind,
    )


def _windows_match(span: Window, cut: Window) -> bool:
    """Whether ``cut`` still contains the forced window.

    The end may move later so a coaching beat can land. The start stays
    put, and a cut that ends early does not count.
    """
    return (
        span.source == cut.source
        and abs(span.start - cut.start) <= 0.02
        and cut.end >= span.end - 0.02
    )


def assert_forced_windows_kept(forced: list[Window], cuts: list[Window]) -> None:
    """Raise when a forced window was dropped before the cut list was built.

    Every ``MUST_INCLUDE`` and ``INCLUDE_WINDOWS`` span that was kept has to
    be in the list that will be written. Overlap skips and unmatched quotes
    never enter ``forced``, so they are not invented here.
    """
    missing = [span for span in forced if not any(_windows_match(span, cut) for cut in cuts)]
    if not missing:
        return
    detail = "; ".join(
        f"{window.source} {window.start:.2f}–{window.end:.2f}s"
        f"{(' (' + window.include + ')') if window.include else ''}"
        for window in missing
    )
    raise BriefError(
        "A must-include is missing from the final cut. "
        f"Refusing to assemble without it: {detail}"
    )


def assert_includes_in_cuts(include_rows: list[dict], cuts: list[Window]) -> None:
    """Raise when a row marked kept is absent from the final cut list.

    ``kept`` is false for an unmatched quote and for a window skipped because
    it overlaps one already kept. Those stay out. A row that says it was
    kept and then does not appear in the cut list stops the run.
    """
    missing = []
    for row in include_rows or []:
        if not row.get("kept"):
            continue
        try:
            start = float(row["start"])
            end = float(row["end"])
        except (KeyError, TypeError, ValueError):
            missing.append(row)
            continue
        source = str(row.get("source") or "")
        found = any(
            cut.source == source
            and abs(cut.start - start) <= 0.02
            and cut.end >= end - 0.02
            for cut in cuts
        )
        if not found:
            missing.append(row)
    if not missing:
        return
    detail = "; ".join(
        f"{row.get('kind') or 'include'} {row.get('text') or row.get('source') or ''}".strip()
        for row in missing
    )
    raise BriefError(
        "A must-include is missing from the final cut. "
        f"Refusing to assemble without it: {detail}"
    )


def _scene_text(window: Window) -> str:
    return " ".join(
        part for part in (window.source, window.include, window.heard) if part
    )


def _scene_needles(scene: str) -> list[str]:
    """Phrases ``TITLE_UNDER`` can match.

    A slash splits alternatives (``John / pink shirt``). A phrase with no
    slash stays one phrase, so a description still has to occur as written.
    """
    needles = []
    for piece in re.split(r"[/／]", scene or ""):
        needle = " ".join(piece.casefold().split())
        if needle:
            needles.append(needle)
    return needles


def _scene_hit(window: Window, scene: str) -> bool:
    """Whether ``scene`` names this window's file or what was said in it."""
    haystack = _scene_text(window).casefold()
    if not haystack:
        return False
    for needle in _scene_needles(scene):
        if re.search(r"\b" + re.escape(needle) + r"\b", haystack):
            return True
    return False


def _scene_can_lead(window: Window) -> bool:
    """A named person leads only when the window is talking or a must-include.

    A silent still whose file name happens to match stays off the title.
    Motion without speech is a silent swing, not who the title should open on.
    """
    if window.kind == "must_include":
        return True
    return window.speech >= OPEN_SPEECH_FLOOR


def _open_tier(window: Window, scene: str = "") -> int:
    """How strongly a window should lead a talking cut. Higher is better."""
    if scene and _scene_hit(window, scene) and _scene_can_lead(window):
        return 4
    if window.kind == "must_include":
        return 3
    if window.forced:
        return 2
    if window.speech >= OPEN_SPEECH_FLOOR or window.motion >= OPEN_MOTION_FLOOR:
        return 1
    return 0


def _lead_with_talking(ordered: list[Window], scene: str = "") -> list[Window]:
    """Move a talking window to the front. Leave the rest in clip order.

    When the brief names who is under the title, a matching talking window
    leads. Otherwise a must-include leads, then any other forced window,
    then the window with the most speech. A silent still stays first only
    when nothing else in the cut is talking or was forced in.
    """
    if len(ordered) < 2:
        return ordered
    best = max(_open_tier(window, scene) for window in ordered)
    if best == 0 or _open_tier(ordered[0], scene) == best:
        lead = ordered
    else:
        candidates = [window for window in ordered if _open_tier(window, scene) == best]
        opener = max(candidates, key=lambda window: (window.speech, window.motion))
        lead = [opener, *[window for window in ordered if window is not opener]]
    return _sink_silent_fillers(lead)


def _sink_silent_fillers(ordered: list[Window]) -> list[Window]:
    """Keep silent stills at the bottom of the pack, after talking and action.

    A must-include stays where the opener rules put it. A filler that is
    neither talking nor moving does not play between those windows, which
    is the dead clip right after the title.
    """
    if len(ordered) < 2:
        return ordered
    head, tail = ordered[0], ordered[1:]
    alive = [window for window in tail if window.forced or not is_silent_still(window)]
    dead = [window for window in tail if not window.forced and is_silent_still(window)]
    if not dead:
        return ordered
    return [head, *alive, *dead]


def playback_order(windows: list[Window], style: str, scene: str = "") -> list[Window]:
    """Hype plays strongest first.

    Talking plays in clip-name, then time, order, after the opener. The
    opener is who ``TITLE_UNDER`` / ``OPEN_SCENE`` names when that match
    is talking, otherwise a must-include, otherwise a window with speech
    or motion. A silent still does not sit under the title while a talking
    window is later in the list.
    """
    if style == "talking":
        ordered = sorted(windows, key=lambda w: (w.source.casefold(), w.start))
        return _lead_with_talking(ordered, scene)
    return list(windows)


def _as_gray(frame) -> np.ndarray:
    """One gray frame as int16 samples. Bytes are raw pixels, not text."""
    if isinstance(frame, (bytes, bytearray, memoryview)):
        return np.frombuffer(frame, dtype=np.uint8).astype(np.int16)
    return np.asarray(frame, dtype=np.int16)


def motion_from_frames(frames: list, sample_fps: float, duration: float) -> list[float]:
    """Per-second mean absolute frame difference, unnormalised.

    ``frames`` are flat ``uint8`` gray images of equal length, in time order.
    Used by the ffmpeg sampler and by tests that never open a video.
    """
    n = max(1, int(math.ceil(duration))) if duration > 0 else 0
    buckets: list[list[float]] = [[] for _ in range(n)]
    if len(frames) < 2 or sample_fps <= 0 or n == 0:
        return [0.0] * n
    prev = _as_gray(frames[0])
    for index in range(1, len(frames)):
        cur = _as_gray(frames[index])
        if cur.shape != prev.shape:
            prev = cur
            continue
        mae = float(np.mean(np.abs(cur - prev))) / 255.0
        sec = int((index - 1) / sample_fps)
        if sec >= n:
            sec = n - 1
        buckets[sec].append(mae)
        prev = cur
    return [sum(b) / len(b) if b else 0.0 for b in buckets]


def _scaled_gray_size(width: int, height: int, target_w: int = MOTION_WIDTH) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return target_w, 54
    w = target_w if target_w % 2 == 0 else target_w + 1
    h = int(round(height * (w / float(width))))
    h = max(2, h - h % 2)
    return w, h


def sample_motion(path: str, duration: float, log_fn=print) -> list[float]:
    """Gray-frame differences via ffmpeg. Empty motion if the read fails.

    The clip is decoded at a few frames a second and a small width. The
    number that comes back is how much the picture changed, which is the
    signal ``hype`` ranks on. No frames are stored.
    """
    n = max(1, int(math.ceil(duration))) if duration > 0 else 0
    zeros = [0.0] * n
    try:
        from modules.system.app_paths import ffmpeg_exe
        from modules.media.video_probe import probe_video
        info = probe_video(path)
        w, h = _scaled_gray_size(int(info.get("width") or 0), int(info.get("height") or 0))
        cmd = [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-i", path,
            "-an",
            "-vf", f"fps={MOTION_SAMPLE_FPS},scale={w}:{h}:flags=bilinear,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        log_fn(f"motion skipped for {os.path.basename(path)}: {exc}")
        return zeros
    frame_size = w * h
    frames = []
    stderr_box: list[bytes] = []

    def _drain_stderr():
        if proc.stderr is not None:
            stderr_box.append(proc.stderr.read())

    drain = threading.Thread(target=_drain_stderr)
    drain.start()
    try:
        if proc.stdout is not None:
            while True:
                buf = proc.stdout.read(frame_size)
                if len(buf) < frame_size:
                    break
                frames.append(np.frombuffer(buf, dtype=np.uint8).copy())
    finally:
        drain.join()
        proc.wait()
    if proc.returncode not in (0, None) and not frames:
        err = b"".join(stderr_box).decode("utf-8", errors="replace").strip()[-300:]
        log_fn(f"motion skipped for {os.path.basename(path)}: {err or 'ffmpeg failed'}")
        return zeros
    return motion_from_frames(frames, MOTION_SAMPLE_FPS, duration)


def default_probe(path: str) -> dict:
    from modules.media.video_probe import probe_video
    return probe_video(path)


def default_peaks(path: str) -> list[float]:
    from modules.audio.audio_peaks import extract_audio_peaks
    return [float(t) for t in extract_audio_peaks(path)]


def default_transcribe(path: str, model: str, log_fn=print):
    from modules.audio.transcript import get_transcript_segments
    return get_transcript_segments(
        path,
        model_name=model,
        log_fn=log_fn,
        enable_diarization=False,
        cleanup=True,
    )


def default_cut(src, start, end, dst, mode="cpu"):
    from modules.media.video_cutter import cut_video
    cut_video(src, start, end, dst, mode=mode)


def default_combine(files, output, log_fn=print):
    from modules.media.combine_videos import combine_videos
    return combine_videos(files, output, log_fn=log_fn)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _burn_talking_pack(
    draft_path, destination, brief, assembled, cuts, scores, *,
    use_whisper: bool, whisper_model: str, caption_fn, talking_burn, log_fn,
) -> None:
    """Title, speech captions, and CTA on a talking cut.

    Caption text comes from ``captions.md`` when that file is already in
    the folder, so a corrected cue list is what gets burned. Otherwise
    Whisper writes ``captions.md`` first and the burn uses that file.
    A missing transcript still draws the title. A failed burn leaves the
    unbranded file in place. No call to action means no end card.
    """
    windows = talking_windows(assembled, cta=bool((brief.cta or "").strip()))
    body_start, body_end = windows["captions"]
    segments: list = []
    engine = None
    error = None
    source = "speech"
    md_path = Path(destination) / "captions.md"
    cues = []
    if md_path.is_file():
        cues = parse_captions_md(md_path.read_text(encoding="utf-8"))
        engine = "captions.md"
        source = "captions.md"
        log_fn(f"captions.md: {md_path}")
        log_fn("Using the captions already in captions.md.")
    elif not use_whisper:
        engine = "skipped"
        log_fn("Captions skipped. Whisper is off, so no speech was read.")
    else:
        try:
            raw = caption_fn(str(draft_path), whisper_model, log_fn)
            if isinstance(raw, dict) and "segments" in raw:
                engine = raw.get("engine") or "whisper"
                segments = list(raw.get("segments") or [])
            else:
                engine = "whisper"
                segments = list(raw or [])
        except Exception as exc:
            engine = "error"
            error = str(exc)
            log_fn(f"Caption transcript failed ({exc}). Title and CTA still draw.")
        cues = caption_cues(segments, body_start=body_start, body_end=body_end)
        md_path.write_text(render_captions_md(cues), encoding="utf-8")
        log_fn(f"captions.md: {md_path}")
        log_fn("Edit captions.md, then run again to burn the corrected lines.")
    record = caption_record(
        brief, cues=cues, engine=engine, burned=False, error=error, source=source,
    )
    if not cues and not brief.wants_brand():
        cuts["captions"] = record
        scores["captions"] = record
        return
    branded = None
    try:
        fd, branded = tempfile.mkstemp(
            suffix=".mp4", prefix="vh_brand_", dir=str(destination),
        )
        os.close(fd)
        talking_burn(str(draft_path), branded, brief, assembled, cues, log_fn)
        os.replace(branded, draft_path)
        branded = None
        record = caption_record(
            brief, cues=cues, engine=engine, burned=True, error=error, source=source,
        )
        if brief.wants_brand():
            cuts["brand"] = brand_record(brief, applied=True)
            scores["brand"] = cuts["brand"]
    except Exception as exc:
        record = caption_record(
            brief, cues=cues, engine=engine, burned=False,
            error=error or str(exc), source=source,
        )
        if brief.wants_brand():
            cuts["brand"] = brand_record(brief, applied=False, error=str(exc))
            scores["brand"] = cuts["brand"]
        log_fn(
            f"Talking pack skipped ({exc}). draft.mp4 is the unbranded cut."
        )
    finally:
        if branded and os.path.exists(branded):
            os.remove(branded)
    cuts["captions"] = record
    scores["captions"] = record


def run_club_montage(
    folder: str | Path,
    *,
    brief_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    use_whisper: bool = True,
    dry_run: bool = False,
    log_fn=print,
    probe=None,
    peaks=None,
    motion=None,
    transcribe=None,
    cut=None,
    combine=None,
    brand=None,
    caption_transcribe=None,
    caption_burn=None,
    loudness=None,
) -> dict:
    """Score ``folder`` against its brief and write the three outputs.

    Returns the cuts payload. Raises ``BriefError`` for a bad brief.
    A folder with no usable windows still writes ``scores.json`` and
    returns a payload whose ``cuts`` list is empty.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise BriefError(f"Clips folder does not exist: {root}")
    brief = load_brief(brief_path or (root / "brief.md"))
    destination = Path(out_dir).expanduser().resolve() if out_dir else root
    destination.mkdir(parents=True, exist_ok=True)

    probe_fn = probe or default_probe
    peaks_fn = peaks or default_peaks
    motion_fn = motion or sample_motion
    transcribe_fn = transcribe or default_transcribe
    cut_fn = cut or default_cut
    combine_fn = combine or default_combine
    brand_fn = brand or apply_brand
    caption_fn = caption_transcribe or transcribe_captions
    talking_burn = caption_burn or apply_talking_pack

    whisper_status = "skipped" if not use_whisper else whisper_model
    transcribe_live = transcribe_fn
    if use_whisper and transcribe is None:
        try:
            import whisper  # noqa: F401
        except Exception as exc:
            whisper_status = f"unavailable: {exc}"
            log_fn(f"Whisper unavailable ({exc}). Ranking on audio peaks and motion.")

            def transcribe_live(_path, _model, _log_fn=print):
                return []

    clips = list_clips(root)
    loudness_fn = loudness or measure_mean_volume_db
    soft_quiet: list[str] = []
    volumes: dict[str, float | None] = {}
    inaudible: set[str] = set()
    if brief.style == "talking" and len(clips) > 1:
        readings = []
        for path in clips:
            try:
                db = loudness_fn(str(path))
            except Exception as exc:
                log_fn(f"volume skipped for {path.name}: {exc}")
                db = None
            volumes[path.name] = None if db is None else round(float(db), 1)
            readings.append((path.name, db))
            if not clip_is_audible(db):
                inaudible.add(path.name)
        soft_quiet = quiet_clip_names(readings)
    soft_set = set(soft_quiet)
    mic_mode = "off" if brief.style != "talking" else ("mixed" if soft_quiet else "same_session")
    mic_preference = {
        "enabled": brief.style == "talking",
        "mode": mic_mode,
        "gap_db": MIXED_GAP_DB if brief.style == "talking" else None,
        "factor": MIC_SOFT_FACTOR if soft_quiet else 1.0,
        "soft": soft_quiet,
        "dropped": [],
        "volumes_db": volumes,
        "note": (
            "Talking folders keep every clip. A same-session lesson mic "
            f"(inside {MIXED_GAP_DB:g} dB of the loudest file) is not "
            "penalised. A file that far under the loudest stays in the "
            f"ranker with a {MIC_SOFT_FACTOR:g} score haircut, so a quiet "
            "phone does not bury the boom, and clear speech can still win. "
            "Level only, not a picture match."
        ),
    }
    log_fn(
        f"Club montage: {len(clips)} clip(s), "
        f"LENGTH {brief.length_min_s:g}-{brief.length_max_s:g}s, "
        f"STYLE {brief.style}."
    )
    log_fn(ASSEMBLE_ONLY["note"])
    if brief.style == "hype":
        log_fn(
            "STYLE hype cuts highlights only. Silent hype montages stay in "
            "CapCut; this pack does not build them."
        )
    elif brief.style == "talking":
        log_fn("Talking pack: title, captions of the speech, then the call to action.")
        if soft_quiet:
            known = [db for db in volumes.values() if db is not None]
            loudest = max(known) if known else None
            for name in soft_quiet:
                log_fn(
                    f"  {name}: mean volume {volumes[name]} dB is "
                    f"{MIXED_GAP_DB:g} dB or more under the loudest clip "
                    f"({loudest} dB). Kept, with a small score haircut."
                )
        for name in sorted(inaudible, key=str.casefold):
            log_fn(
                f"  {name}: mean volume {volumes[name]} dB is at or below "
                f"{SILENCE_MEAN_DB:g} dB, so it cannot fill a talking gap."
            )

    saved_payload = load_transcript(destination / "transcript.json")
    saved_rows = {
        row["source"].casefold(): row
        for row in files_from_payload(saved_payload, clips)
    }
    if saved_payload:
        log_fn("Using transcript.json for speech. The folder is not transcribed again.")

    clip_reports = []
    windows: list[Window] = []
    durations: dict[str, float] = {}
    heard: dict[str, list] = {}
    for path in clips:
        report = {"source": path.name, "duration_s": None, "error": None}
        saved = saved_rows.get(path.name.casefold()) or {}
        saved_segments = saved.get("segments")
        if path.name in soft_set:
            report["mic"] = "soft"
            report["mean_volume_db"] = volumes.get(path.name)
        try:
            info = probe_fn(str(path))
            duration = float(info.get("duration") or 0)
            report["duration_s"] = _round(duration)
            if duration <= 0:
                raise RuntimeError("duration is 0")
            durations[path.name] = duration
            try:
                peak_times = peaks_fn(str(path))
            except Exception as exc:
                log_fn(f"audio peaks skipped for {path.name}: {exc}")
                peak_times = []
            try:
                motion_series = motion_fn(str(path), duration)
            except Exception as exc:
                log_fn(f"motion skipped for {path.name}: {exc}")
                motion_series = []
            segments: list = []
            if saved_segments is not None:
                segments = list(saved_segments)
            elif use_whisper and whisper_status == whisper_model:
                try:
                    segments = segments_of(transcribe_live(str(path), whisper_model, log_fn))
                except Exception as exc:
                    log_fn(f"whisper skipped for {path.name}: {exc}")
                    segments = []
            heard[path.name] = segments
            found = score_windows_for_clip(
                source=path.name,
                path=str(path),
                duration=duration,
                style=brief.style,
                keywords=brief.keywords,
                peak_times=peak_times,
                motion_series=motion_series,
                segments=segments,
            )
            if path.name in soft_set:
                for window in found:
                    window.score *= MIC_SOFT_FACTOR
            if path.name in inaudible:
                for window in found:
                    window.audible = False
            windows.extend(found)
            log_fn(f"  {path.name}: {duration:.1f}s, {len(found)} windows")
        except Exception as exc:
            report["error"] = str(exc)
            log_fn(f"  skipped {path.name}: {exc}")
        clip_reports.append(report)

    if brief.must_include or brief.include_windows:
        for path in clips:
            if durations.get(path.name, 0) > 0:
                continue
            try:
                info = probe_fn(str(path))
                durations[path.name] = float(info.get("duration") or 0)
            except Exception as exc:
                log_fn(f"duration skipped for {path.name}: {exc}")

    transcript_files = [
        {
            "source": path.name,
            "path": str(path),
            "segments": heard.get(path.name, []),
        }
        for path in clips
    ]
    try:
        spans, include_rows = resolve_includes(brief, transcript_files, durations, log_fn)
    except ValueError as exc:
        raise BriefError(str(exc)) from exc
    forced = [_forced_window(span) for span in spans]
    if forced:
        log_fn(f"Forcing {len(forced)} highlighted window(s) into the cut.")

    selected = select_with_forced(
        windows, forced, brief.length_min_s, brief.length_max_s,
        style=brief.style,
        segments_by_source=heard,
        durations=durations,
        soft_sources=soft_set,
        inaudible_sources=inaudible,
        log_fn=log_fn,
    )
    if brief.style == "talking":
        selected = extend_coaching_beats(selected, heard, durations, log_fn)
        for window in selected:
            if window.heard:
                continue
            window.heard = _text_between(heard.get(window.source), window.start, window.end)
    ordered = playback_order(selected, brief.style, brief.title_under)
    try:
        assert_includes_in_cuts(include_rows, ordered)
    except BriefError as exc:
        log_fn(str(exc))
        raise
    if brief.style == "talking" and ordered:
        lead = ordered[0]
        who = f" ({brief.title_under})" if brief.title_under else ""
        log_fn(
            f"Opening under the title{who}: {lead.source} "
            f"{lead.start:.2f}–{lead.end:.2f}s."
        )
        forced_total = sum(window.duration for window in ordered if window.forced)
        if (
            forced
            and forced_total < ((brief.length_min_s + brief.length_max_s) / 2.0) - 0.05
            and not any(not window.forced and window.speech >= FILL_SPEECH_FLOOR for window in ordered)
        ):
            log_fn(
                "No high-speech window left to fill LENGTH. "
                "The cut stays shorter than the brief rather than inserting a silent pause."
            )
    assembled = sum(w.duration for w in ordered)
    within = brief.length_min_s - 1e-3 <= assembled <= brief.length_max_s + 1e-3

    play_of = {
        (w.source, round(w.start, 3)): i for i, w in enumerate(ordered)
    }
    chosen_by_start = {(w.source, round(w.start, 3)): w for w in ordered}
    rows = []
    for candidate in sorted(windows, key=lambda item: (-item.score, item.source.casefold(), item.start)):
        chosen = chosen_by_start.get((candidate.source, round(candidate.start, 3)))
        if chosen is None:
            rows.append(candidate.as_dict(selected=False))
            continue
        rows.append(chosen.as_dict(
            selected=True,
            play_index=play_of[(chosen.source, round(chosen.start, 3))],
        ))
    listed = {(row["source"], row["start"]) for row in rows}
    for window in ordered:
        key = (window.source, _round(window.start))
        if key in listed:
            continue
        rows.append(window.as_dict(
            selected=True,
            play_index=play_of[(window.source, round(window.start, 3))],
        ))
        listed.add(key)

    scores = {
        "policy": ASSEMBLE_ONLY,
        "brief": brief.as_dict(),
        "signals": {
            "whisper": whisper_status,
            "audio_peaks": True,
            "motion": True,
            "transcript": "reused" if saved_payload else ("live" if use_whisper else "off"),
        },
        "includes": include_rows,
        "order": "chronological" if brief.style == "talking" else "score",
        "mic_preference": mic_preference,
        "clips": clip_reports,
        "window_count": len(windows),
        "selected_seconds": _round(assembled),
        "within_brief": bool(within and ordered),
        "draft_written": False,
        "brand": brand_record(brief),
        "captions": caption_record(brief),
        "windows": rows,
    }
    cuts = {
        "policy": ASSEMBLE_ONLY,
        "output": "draft.mp4",
        "style": brief.style,
        "order": scores["order"],
        "length_min_s": brief.length_min_s,
        "length_max_s": brief.length_max_s,
        "assembled_seconds": _round(assembled),
        "within_brief": scores["within_brief"],
        "notes": brief.notes,
        "keywords": list(brief.keywords),
        "title_under": brief.title_under,
        "includes": include_rows,
        "mic_preference": mic_preference,
        "draft_written": False,
        "brand": brand_record(brief),
        "captions": caption_record(brief),
        "cuts": [
            w.as_dict(selected=True, play_index=i)
            for i, w in enumerate(ordered)
        ],
    }

    scores_path = destination / "scores.json"
    cuts_path = destination / "cuts.json"
    draft_path = destination / "draft.mp4"
    _write_json(scores_path, scores)
    _write_json(cuts_path, cuts)
    log_fn(f"Wrote {cuts_path.name} and {scores_path.name} ({len(ordered)} cuts, {assembled:.1f}s).")

    if not ordered:
        log_fn("No windows to assemble.")
        return cuts

    if dry_run:
        log_fn("Dry run: draft.mp4 was not encoded.")
        return cuts

    temp_dir = tempfile.mkdtemp(prefix="vh_club_")
    pieces = []
    try:
        for i, window in enumerate(ordered):
            piece = os.path.join(temp_dir, f"cut_{i:03d}.mp4")
            log_fn(
                f"Cutting {i + 1}/{len(ordered)}: {window.source} "
                f"{window.start:.2f}-{window.end:.2f}s"
            )
            cut_fn(window.path, window.start, window.end, piece, "cpu")
            pieces.append(piece)
        log_fn(f"Joining {len(pieces)} cuts into {draft_path.name}")
        combine_fn(pieces, str(draft_path), log_fn)
    except Exception:
        log_fn("Encode failed. cuts.json and scores.json still describe the plan.")
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    cuts["draft_written"] = True
    scores["draft_written"] = True
    if brief.style == "talking":
        _burn_talking_pack(
            draft_path, destination, brief, assembled, cuts, scores,
            use_whisper=use_whisper and whisper_status == whisper_model,
            whisper_model=whisper_model,
            caption_fn=caption_fn,
            talking_burn=talking_burn,
            log_fn=log_fn,
        )
    elif brief.wants_brand():
        branded = None
        try:
            fd, branded = tempfile.mkstemp(
                suffix=".mp4", prefix="vh_brand_", dir=str(destination),
            )
            os.close(fd)
            log_fn("Branding the assembled cut with the brief's type.")
            brand_fn(str(draft_path), branded, brief, assembled, log_fn)
            os.replace(branded, draft_path)
            branded = None
            record = brand_record(brief, applied=True)
        except Exception as exc:
            record = brand_record(brief, applied=False, error=str(exc))
            log_fn(
                f"On-screen type skipped ({exc}). "
                "draft.mp4 is the unbranded cut."
            )
        finally:
            if branded and os.path.exists(branded):
                os.remove(branded)
        cuts["brand"] = record
        scores["brand"] = record
    _write_json(scores_path, scores)
    _write_json(cuts_path, cuts)
    log_fn(f"Wrote {draft_path}")
    return cuts


def run_transcript(
    folder: str | Path,
    *,
    out_dir: str | Path | None = None,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    log_fn=print,
    probe=None,
    transcribe=None,
) -> dict:
    """Write ``transcript.md`` and ``transcript.json`` and do not assemble.

    Every clip in the folder is heard, including a quiet phone take, so the
    person can choose it or skip it. A brief is not required for this step.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise BriefError(f"Clips folder does not exist: {root}")
    destination = Path(out_dir).expanduser().resolve() if out_dir else root
    destination.mkdir(parents=True, exist_ok=True)
    probe_fn = probe or default_probe
    transcribe_fn = transcribe or transcribe_captions
    if transcribe is None:
        try:
            import whisper  # noqa: F401
        except Exception as exc:
            raise BriefError(f"Whisper unavailable ({exc}).") from exc

    clips = list_clips(root)
    log_fn(f"Transcript only: {len(clips)} clip(s). No draft will be assembled.")
    log_fn(ASSEMBLE_ONLY["note"])
    files = []
    for path in clips:
        row: dict = {
            "source": path.name,
            "path": str(path),
            "duration_s": None,
            "segments": [],
            "error": None,
        }
        try:
            info = probe_fn(str(path))
            row["duration_s"] = _round(float(info.get("duration") or 0))
        except Exception as exc:
            log_fn(f"duration skipped for {path.name}: {exc}")
        try:
            row["segments"] = segments_of(transcribe_fn(str(path), whisper_model, log_fn))
        except Exception as exc:
            row["error"] = str(exc)
            log_fn(f"whisper skipped for {path.name}: {exc}")
        files.append(row)
        log_fn(f"  {path.name}: {len(row['segments'])} lines")

    payload = transcript_payload(files, whisper_model)
    md_path = destination / "transcript.md"
    json_path = destination / "transcript.json"
    md_path.write_text(render_transcript_md(payload), encoding="utf-8")
    _write_json(json_path, payload)
    log_fn("Transcript only. No draft was assembled.")
    log_fn(f"transcript.md: {md_path}")
    log_fn(f"transcript.json: {json_path}")
    log_fn(
        "Show this transcript and ask which lines to highlight, or skip "
        "and let the ranker choose."
    )
    return {
        "transcript_md": str(md_path),
        "transcript_json": str(json_path),
        "whisper": whisper_model,
        "files": payload["files"],
        "draft_written": False,
    }


def _folder_from_args(parser, args) -> tuple[str | None, bool]:
    """``(folder, transcript_only)`` from ``pick`` or ``--transcript-only``."""
    positionals = list(args.folder or [])
    transcript_only = bool(args.transcript_only)
    if positionals and positionals[0] == "pick":
        transcript_only = True
        positionals = positionals[1:]
    elif positionals and positionals[-1] == "pick":
        transcript_only = True
        positionals = positionals[:-1]
    if len(positionals) > 1:
        parser.error("give one clips folder")
    folder = args.club or (positionals[0] if positionals else None)
    # ``python main.py --club pick <folder>`` stores pick in --club.
    if folder == "pick":
        transcript_only = True
        folder = positionals[0] if positionals else None
    return folder, transcript_only


def main(argv: list[str] | None = None) -> int:
    """CLI entry used by ``python -m modules.club`` and ``python main.py --club``."""
    parser = argparse.ArgumentParser(
        prog="club-montage",
        description=(
            "Assemble a talking cut from real clips and a brief.md. "
            "Burns a title, captions of the speech, and a call to action. "
            "`pick` or --transcript-only writes a transcript and stops. "
            "Does not generate footage, faces, voices, or music."
        ),
    )
    parser.add_argument(
        "folder",
        nargs="*",
        help="Clips folder. `pick <folder>` writes a transcript and stops.",
    )
    parser.add_argument("--club", help="Clips folder. Same as the positional argument; used by main.py.")
    parser.add_argument("--brief", help="Path to brief.md (default: <folder>/brief.md)")
    parser.add_argument("--out", help="Output directory (default: the clips folder)")
    parser.add_argument(
        "--whisper-model",
        default=DEFAULT_WHISPER_MODEL,
        help=(
            "Local Whisper model name (default: small). "
            "Use medium when a take is still muddy. Weights stay on this machine."
        ),
    )
    parser.add_argument(
        "--no-whisper",
        action="store_true",
        help="Rank on audio peaks and motion only. Not valid with pick.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write cuts.json and scores.json without encoding draft.mp4.",
    )
    parser.add_argument(
        "--transcript-only",
        action="store_true",
        help=(
            "Transcribe the folder, write transcript.md and transcript.json, "
            "and stop. No draft."
        ),
    )
    args = parser.parse_args(argv)
    folder, transcript_only = _folder_from_args(parser, args)
    if not folder:
        parser.error(
            "give the clips folder, for example: python -m modules.club ./clips"
        )
    try:
        if transcript_only:
            result = run_transcript(
                folder,
                out_dir=args.out,
                whisper_model=args.whisper_model,
            )
        else:
            cuts = run_club_montage(
                folder,
                brief_path=args.brief,
                out_dir=args.out,
                whisper_model=args.whisper_model,
                use_whisper=not args.no_whisper,
                dry_run=args.dry_run,
            )
    except BriefError as exc:
        print(f"brief: {exc}")
        return 2
    except Exception as exc:
        print(f"club montage failed: {exc}")
        return 1
    if transcript_only:
        return 0 if result.get("files") else 1
    if not cuts.get("cuts"):
        return 1
    return 0
