"""Rank windows in a folder of real clips and assemble ``draft.mp4``.

Signals, in the order this path prefers them:

1. Local Whisper (``openai-whisper``), for speech overlap and KEYWORDS.
2. Audio peaks from ``modules.audio.audio_peaks``.
3. Frame-to-frame motion, sampled with ffmpeg.

``hype`` weights peaks and motion. ``talking`` weights speech and keywords.
Both styles only cut ranges that already exist in the supplied files and
concatenate those ranges. Weights change which windows are kept. They do
not create pictures, faces, voices, or songs.

When the brief sets ``TITLE``, ``SUBTITLE``, or ``CTA``, a second ffmpeg
pass draws that type on the assembled cut. ``STYLE: talking`` also burns
captions of the speech between the title and the call to action. Those
words come from local Whisper. ``NOTES`` is copied into the JSON for the
editor. It is not a prompt and it is not caption text.
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
from modules.club.captions import (
    apply_talking_pack,
    caption_cues,
    caption_record,
    talking_windows,
    transcribe_captions,
)


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
OUTPUT_NAMES = {"draft.mp4", "cuts.json", "scores.json"}

# (window seconds, hop seconds). Short punches for hype, longer holds for talk.
STYLE_WINDOW = {
    "hype": (4.0, 2.0),
    "talking": (8.0, 4.0),
}

STYLE_WEIGHTS = {
    "hype": {"audio": 1.0, "motion": 1.0, "speech": 0.25, "keyword": 0.75},
    "talking": {"audio": 0.20, "motion": 0.15, "speech": 1.0, "keyword": 1.25},
}

MOTION_SAMPLE_FPS = 4.0
MOTION_WIDTH = 96


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
        if play_index is not None:
            payload["play_index"] = play_index
        return payload


def _round(value: float) -> float:
    return round(float(value), 3)


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


def speech_coverage(segments, start: float, end: float) -> float:
    if not segments or end <= start:
        return 0.0
    covered = 0.0
    for seg in segments:
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
    for seg in segments:
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
        total = (
            weights["audio"] * audio_v
            + weights["motion"] * motion_v
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


def select_windows(windows: list[Window], min_s: float, max_s: float) -> list[Window]:
    """Greedy non-overlapping pick until the middle of the requested range.

    The draft stays inside ``max_s``. Once ``min_s`` is met, a window that
    does not fit is skipped rather than trimmed. Below ``min_s``, the next
    window may be shortened to the remaining room when at least one second
    is left. The result is score order; callers reorder for playback.
    """
    ranked = sorted(windows, key=lambda w: (-w.score, w.source.casefold(), w.start))
    target = (min_s + max_s) / 2.0
    chosen: list[Window] = []
    total = 0.0
    for window in ranked:
        if any(_same_source_overlap(window, kept) for kept in chosen):
            continue
        room = max_s - total
        if room <= 0.05:
            break
        dur = window.duration
        if dur <= 0:
            continue
        piece = window
        if dur > room + 1e-3:
            if total >= min_s or room < 1.0:
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
            )
        chosen.append(piece)
        total += piece.duration
        if total >= target - 1e-6:
            break
    return chosen


def playback_order(windows: list[Window], style: str) -> list[Window]:
    """Hype plays strongest first. Talking plays in clip-name, then time, order."""
    if style == "talking":
        return sorted(windows, key=lambda w: (w.source.casefold(), w.start))
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

    Caption text is the transcript of ``draft_path``. A missing transcript
    still draws the title and the call to action. A failed burn leaves the
    unbranded file in place.
    """
    windows = talking_windows(assembled)
    body_start, body_end = windows["captions"]
    segments: list = []
    engine = None
    error = None
    if not use_whisper:
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
    record = caption_record(brief, cues=cues, engine=engine, burned=False, error=error)
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
        record = caption_record(brief, cues=cues, engine=engine, burned=True, error=error)
        if brief.wants_brand():
            cuts["brand"] = brand_record(brief, applied=True)
            scores["brand"] = cuts["brand"]
    except Exception as exc:
        record = caption_record(
            brief, cues=cues, engine=engine, burned=False,
            error=error or str(exc),
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
    whisper_model: str = "base",
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

    clip_reports = []
    windows: list[Window] = []
    for path in clips:
        report = {"source": path.name, "duration_s": None, "error": None}
        try:
            info = probe_fn(str(path))
            duration = float(info.get("duration") or 0)
            report["duration_s"] = _round(duration)
            if duration <= 0:
                raise RuntimeError("duration is 0")
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
            segments = []
            if use_whisper and whisper_status == whisper_model:
                try:
                    segments = transcribe_live(str(path), whisper_model, log_fn) or []
                except Exception as exc:
                    log_fn(f"whisper skipped for {path.name}: {exc}")
                    segments = []
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
            windows.extend(found)
            log_fn(f"  {path.name}: {duration:.1f}s, {len(found)} windows")
        except Exception as exc:
            report["error"] = str(exc)
            log_fn(f"  skipped {path.name}: {exc}")
        clip_reports.append(report)

    selected = select_windows(windows, brief.length_min_s, brief.length_max_s)
    ordered = playback_order(selected, brief.style)
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

    scores = {
        "policy": ASSEMBLE_ONLY,
        "brief": brief.as_dict(),
        "signals": {
            "whisper": whisper_status,
            "audio_peaks": True,
            "motion": True,
        },
        "order": "chronological" if brief.style == "talking" else "score",
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry used by ``python -m modules.club`` and ``python main.py --club``."""
    parser = argparse.ArgumentParser(
        prog="club-montage",
        description=(
            "Assemble a talking cut from real clips and a brief.md. "
            "Burns a title, captions of the speech, and a call to action. "
            "Does not generate footage, faces, voices, or music."
        ),
    )
    parser.add_argument("folder", nargs="?", help="Folder containing the clips and brief.md")
    parser.add_argument("--club", help="Clips folder. Same as the positional argument; used by main.py.")
    parser.add_argument("--brief", help="Path to brief.md (default: <folder>/brief.md)")
    parser.add_argument("--out", help="Output directory (default: the clips folder)")
    parser.add_argument(
        "--whisper-model",
        default="base",
        help="Local Whisper model name (default: base). Weights stay on this machine.",
    )
    parser.add_argument(
        "--no-whisper",
        action="store_true",
        help="Rank on audio peaks and motion only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write cuts.json and scores.json without encoding draft.mp4.",
    )
    args = parser.parse_args(argv)
    folder = args.club or args.folder
    if not folder:
        parser.error("give the clips folder, for example: python -m modules.club ./clips")
    try:
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
    if not cuts.get("cuts"):
        return 1
    return 0
