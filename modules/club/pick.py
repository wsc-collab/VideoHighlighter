"""Transcript a clips folder, then turn chosen lines into forced windows.

The first step writes ``transcript.md`` and ``transcript.json`` and stops.
Nothing is cut. A person reads the transcript and either names the lines
to keep or leaves the choice to the ranker.

``MUST_INCLUDE`` is matched against that transcript. ``INCLUDE_WINDOWS``
is a file plus a start and end. Both become windows the assembler has to
cut. The ranker fills whatever duration is still free.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from modules.club.brief import Brief


CLIP_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

# A matched line is padded so the cut does not clip the first or last word.
# A one-word hit is widened to at least a second so it is watchable.
QUOTE_PAD_S = 0.25
QUOTE_MIN_S = 1.0


@dataclass(frozen=True)
class ForcedSpan:
    source: str
    path: str
    start: float
    end: float
    include: str
    kind: str


def format_timestamp(seconds: float) -> str:
    """``m:ss.mmm`` or ``h:mm:ss.mmm`` for the readable transcript."""
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{minutes}:{secs:02d}.{millis:03d}"


def _round(value: float) -> float:
    return round(float(value), 3)


def _tokens(text: str) -> list[str]:
    """Words for matching. Apostrophes stay inside the word; other marks go."""
    folded = (
        str(text or "")
        .casefold()
        .replace("'", "")
        .replace("’", "")
        .replace("‘", "")
    )
    return re.sub(r"[^a-z0-9\s]+", " ", folded).split()


def segments_of(raw) -> list[dict]:
    """Segment list from either a Whisper list or ``{"segments": ...}``."""
    if isinstance(raw, dict) and "segments" in raw:
        return list(raw.get("segments") or [])
    return list(raw or [])


def _word_stream(segments) -> list[tuple[str, float, float]]:
    """``(token, start, end)`` across a file.

    Real word times are used when the engine returned them. Otherwise the
    segment's words share its span evenly, so a quote can still be placed
    when only segment times exist.
    """
    stream: list[tuple[str, float, float]] = []
    for seg in segments or []:
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        words = list(seg.get("words") or [])
        if words:
            for word in words:
                token = str(word.get("text") or word.get("word") or "")
                for piece in _tokens(token):
                    stream.append((
                        piece,
                        float(word.get("start") or start),
                        float(word.get("end") or end),
                    ))
            continue
        pieces = _tokens(str(seg.get("text") or ""))
        if not pieces:
            continue
        span = max(0.01, end - start)
        step = span / len(pieces)
        for index, piece in enumerate(pieces):
            stream.append((piece, start + index * step, start + (index + 1) * step))
    return stream


def _quote_and_file(moment: str) -> tuple[str, str]:
    """``(quote, file or "")``. A clip name before the colon limits the search."""
    text = str(moment or "").strip()
    if ":" not in text:
        return text, ""
    head, tail = text.split(":", 1)
    head = head.strip()
    if Path(head).suffix.lower() in CLIP_SUFFIXES:
        return tail.strip(), head
    return text, ""


def match_quote(moment: str, files: list[dict]) -> dict | None:
    """First file, then time, where ``moment`` appears as a run of words.

    ``files`` entries need ``source``, ``path``, and ``segments``. An
    optional ``source.mp4: quote`` prefix searches that file only.
    """
    quote, only = _quote_and_file(moment)
    needle = _tokens(quote)
    if not needle:
        return None
    pool = files
    if only:
        pool = [item for item in files if str(item.get("source") or "").casefold() == only.casefold()]
    width = len(needle)
    for item in pool:
        stream = _word_stream(item.get("segments") or [])
        if len(stream) < width:
            continue
        for index in range(0, len(stream) - width + 1):
            if [stream[index + offset][0] for offset in range(width)] != needle:
                continue
            return {
                "source": item.get("source") or "",
                "path": item.get("path") or "",
                "start": stream[index][1],
                "end": stream[index + width - 1][2],
                "text": quote,
            }
    return None


def pad_quote(start: float, end: float, duration: float | None) -> tuple[float, float]:
    """Widen a matched line so the words are not cut off."""
    if end < start:
        end = start
    if end - start < QUOTE_MIN_S:
        mid = (start + end) / 2.0
        start = mid - QUOTE_MIN_S / 2.0
        end = mid + QUOTE_MIN_S / 2.0
    start = max(0.0, start - QUOTE_PAD_S)
    end = end + QUOTE_PAD_S
    if duration and duration > 0:
        end = min(float(duration), end)
        start = min(start, max(0.0, end - 0.1))
    if end <= start:
        end = start + 0.1
    return start, end


def _overlaps(a: ForcedSpan, b: ForcedSpan) -> bool:
    return (
        a.source == b.source
        and a.start < b.end - 1e-3
        and b.start < a.end - 1e-3
    )


def resolve_includes(
    brief: Brief,
    files: list[dict],
    durations: dict[str, float],
    log_fn=print,
) -> tuple[list[ForcedSpan], list[dict]]:
    """Forced spans plus a row for every request, matched or not.

    ``files`` is the transcript file list (source, path, segments).
    Explicit windows are not padded. Quotes are. A later request that
    overlaps one already kept is recorded and not cut twice.
    """
    by_name = {str(item.get("source") or ""): item for item in files}
    by_fold = {name.casefold(): item for name, item in by_name.items()}
    kept: list[ForcedSpan] = []
    rows: list[dict] = []

    def _keep(span: ForcedSpan, row: dict) -> None:
        if any(_overlaps(span, earlier) for earlier in kept):
            row["matched"] = True
            row["kept"] = False
            row["reason"] = "overlaps an earlier include"
            log_fn(
                f"Skipping {span.source} {span.start:.2f}-{span.end:.2f}s: "
                "it overlaps a highlight already kept."
            )
            rows.append(row)
            return
        kept.append(span)
        row["matched"] = True
        row["kept"] = True
        row["source"] = span.source
        row["start"] = _round(span.start)
        row["end"] = _round(span.end)
        rows.append(row)

    for moment in brief.must_include:
        found = match_quote(moment, files)
        if not found or not found.get("path"):
            log_fn(f"MUST_INCLUDE not in the transcript: {moment}")
            rows.append({"kind": "must_include", "text": moment, "matched": False, "kept": False})
            continue
        duration = durations.get(found["source"])
        start, end = pad_quote(float(found["start"]), float(found["end"]), duration)
        _keep(
            ForcedSpan(found["source"], found["path"], start, end, moment, "must_include"),
            {"kind": "must_include", "text": moment},
        )

    for source, start, end in brief.include_windows:
        item = by_name.get(source) or by_fold.get(source.casefold())
        if item is None:
            raise ValueError(
                f"INCLUDE_WINDOWS file {source!r} is not in the clips folder."
            )
        name = str(item.get("source") or source)
        path = str(item.get("path") or "")
        duration = durations.get(name)
        if duration and start >= duration:
            raise ValueError(
                f"INCLUDE_WINDOWS {name} starts at {start:g}s, "
                f"past the clip ({duration:g}s)."
            )
        if duration and end > duration:
            log_fn(f"Clamping {name} to the end of the clip ({duration:.2f}s).")
            end = duration
        _keep(
            ForcedSpan(name, path, start, end, f"{name} {start:g}-{end:g}", "include_window"),
            {"kind": "include_window", "text": f"{name} {start:g}-{end:g}"},
        )
    return kept, rows


def transcript_payload(files: list[dict], whisper: str) -> dict:
    """JSON document: one entry per clip, with segment and word times."""
    rows = []
    for item in files:
        segments = []
        for seg in item.get("segments") or []:
            words = []
            for word in seg.get("words") or []:
                token = str(word.get("text") or word.get("word") or "").strip()
                if not token:
                    continue
                words.append({
                    "start": _round(float(word.get("start") or 0)),
                    "end": _round(float(word.get("end") or 0)),
                    "text": token,
                })
            segments.append({
                "start": _round(float(seg.get("start") or 0)),
                "end": _round(float(seg.get("end") or 0)),
                "text": str(seg.get("text") or "").strip(),
                "words": words,
            })
        rows.append({
            "source": item.get("source") or "",
            "duration_s": item.get("duration_s"),
            "error": item.get("error"),
            "segments": segments,
        })
    return {"whisper": whisper, "files": rows}


def render_transcript_md(payload: dict) -> str:
    """A transcript a person can read: file, then each line with its times."""
    lines = [
        "# Transcript",
        "",
        f"Whisper: {payload.get('whisper') or 'unknown'}",
        "",
        "Ask which lines to highlight, or skip and let the ranker choose.",
        "Add the choice to brief.md as MUST_INCLUDE (a quoted line) or",
        "INCLUDE_WINDOWS (a file and a start-end), then run the assemble step.",
        "",
    ]
    for item in payload.get("files") or []:
        lines.append(f"## {item.get('source') or 'clip'}")
        lines.append("")
        if item.get("error"):
            lines.append(f"Could not transcribe: {item['error']}")
            lines.append("")
            continue
        segments = item.get("segments") or []
        if not segments:
            lines.append("(no speech)")
            lines.append("")
            continue
        for seg in segments:
            lines.append(
                f"### {format_timestamp(seg['start'])}–{format_timestamp(seg['end'])}"
            )
            lines.append("")
            lines.append(str(seg.get("text") or "").strip())
            lines.append("")
            for word in seg.get("words") or []:
                lines.append(
                    f"- {format_timestamp(word['start'])}–{format_timestamp(word['end'])} "
                    f"{word.get('text') or ''}"
                )
            if seg.get("words"):
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load_transcript(path: Path) -> dict | None:
    """Parsed ``transcript.json``, or ``None`` when the file is absent or unreadable."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def files_from_payload(payload: dict | None, clips: list[Path]) -> list[dict]:
    """Transcript rows aligned to the clips currently in the folder.

    A saved transcript is reused so the assemble step does not have to
    hear the folder again. Clips with no saved row stay empty and can
    still be transcribed live.
    """
    saved = {}
    if payload:
        for item in payload.get("files") or []:
            name = str(item.get("source") or "")
            if name:
                saved[name.casefold()] = item
    rows = []
    for path in clips:
        item = saved.get(path.name.casefold())
        if item is None:
            rows.append({
                "source": path.name,
                "path": str(path),
                "duration_s": None,
                "segments": None,
                "error": None,
            })
            continue
        rows.append({
            "source": path.name,
            "path": str(path),
            "duration_s": item.get("duration_s"),
            "segments": list(item.get("segments") or []),
            "error": item.get("error"),
        })
    return rows
