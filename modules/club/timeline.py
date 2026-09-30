"""Speech and motion on one clock, so a cut can be chosen from both.

A talking pack used to be edited by picking fixed windows (an 8 second
grid, or a numbered group from a list). Those lists can still help a
person name a line they want kept. The in and out points come from here:
each moment of real speech, the motion around it, and a suggested range.

The range is padded before the words. It does not end on the last word.
It waits until the picture has settled, then keeps about two and a half
seconds of quiet (inside a 2–3 second band when the clip has that much
room). It does not run into the next line. When the same words are said
again, the take with more motion after the line is the doing take.
"""

from __future__ import annotations

import math

from modules.club.pick import _tokens


# Left of the first word, and the minimum quiet kept on the right when a
# longer tail will not fit. Long enough to hear the breath in, short
# enough that the previous line stays out.
SPEECH_PAD_S = 0.75
# After the picture settles, hold this much quiet. It sits between the
# two-second floor and the three-second ceiling.
SILENCE_TAIL_S = 2.5
SILENCE_TAIL_MIN_S = 2.0
SILENCE_TAIL_MAX_S = 3.0
# A second this busy, compared with the motion around the line, is still
# the action. The cut waits. It does not chase motion forever.
ACTION_RATIO = 0.45
ACTION_EXTEND_MAX_S = 6.0
# The demonstration may start just after the last word. Farther than this
# with no motion yet, and a later bump is a different moment.
START_LOOKAHEAD_S = 2.0
# A breath this long inside a swing is not the end of the action.
QUIET_GAP_S = 1.0
# Raw frame-difference below this is compression noise on a locked-off
# camera, not a demonstration. A flat noisy clip must not count as action
# just because every second is the same small number.
MOTION_ACTION_FLOOR = 0.02
# Same gap the ranker uses when it joins adjacent speech into one window.
SPEECH_GAP_S = 0.6
# A one-word hiccup is not "the same line" said twice.
REPEAT_MIN_TOKENS = 2

GUIDE = {
    "speech_pad_s": SPEECH_PAD_S,
    "silence_tail_s": SILENCE_TAIL_S,
    "silence_tail_min_s": SILENCE_TAIL_MIN_S,
    "silence_tail_max_s": SILENCE_TAIL_MAX_S,
    "action_ratio": ACTION_RATIO,
    "action_extend_max_s": ACTION_EXTEND_MAX_S,
    "start_lookahead_s": START_LOOKAHEAD_S,
    "quiet_gap_s": QUIET_GAP_S,
    "motion_action_floor": MOTION_ACTION_FLOOR,
}

ROLE = (
    "Choose in and out from moments, by comparing motion around the words. "
    "in is already padded before the speech. out waits until the picture "
    "settles, then keeps about 2.5 seconds of quiet, and does not cross "
    "the next line. When the same line is said more than once, prefer the "
    "doing take (more motion after the words) unless the person asked for "
    "the other one. Write the chosen ranges as INCLUDE_WINDOWS. "
    "MUST_INCLUDE is the person's list of quotes, not a group number. "
    "The fixed windows in scores.json are a fallback fill, not the editor."
)


def _round(value: float, places: int = 3) -> float:
    return round(float(value), places)


def _duration_of(item: dict) -> float:
    raw = item.get("duration_s")
    try:
        duration = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        return duration
    ends = [
        float(seg.get("end") or 0)
        for seg in item.get("segments") or []
    ]
    return max(ends) if ends else 0.0


def _motion_clock(series, duration: float) -> list[float]:
    """One raw motion sample per whole second, missing seconds as zeros."""
    n = max(0, int(math.ceil(duration))) if duration > 0 else 0
    values = []
    for raw in series or []:
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            values.append(0.0)
    if n == 0:
        return values
    if len(values) < n:
        values.extend([0.0] * (n - len(values)))
    return values[:n]


def _normalise(values: list[float]) -> list[float]:
    if not values:
        return []
    peak = max(values)
    if peak <= 0:
        return [0.0] * len(values)
    return [v / peak for v in values]


def _mean(series: list[float], start: float, end: float) -> float:
    if not series or end <= start:
        return 0.0
    acc = 0.0
    covered = 0.0
    for index, value in enumerate(series):
        overlap = min(end, index + 1) - max(start, float(index))
        if overlap <= 0:
            continue
        acc += float(value) * overlap
        covered += overlap
    if covered <= 0:
        return 0.0
    return acc / covered


def _at(series: list[float], t: float) -> float:
    if not series:
        return 0.0
    index = int(t)
    if index < 0:
        index = 0
    if index >= len(series):
        index = len(series) - 1
    return float(series[index])


def _peak_between(series: list[float], start: float, end: float) -> float:
    if not series:
        return 0.0
    lo = max(0, int(math.floor(start)))
    hi = min(len(series) - 1, int(math.floor(max(start, end))))
    if hi < lo:
        return 0.0
    return max(float(series[i]) for i in range(lo, hi + 1))


def _active(series: list[float], t: float, peak: float) -> bool:
    if peak < MOTION_ACTION_FLOOR:
        return False
    return _at(series, t) >= ACTION_RATIO * peak


def _settled_at(
    series: list[float],
    speech_start: float,
    speech_end: float,
    limit: float,
) -> tuple[float, bool]:
    """When the action after the line has dropped, and whether it never did.

    A quiet second on the last word is not the end if the demonstration
    starts within a couple of seconds. A one-second breath inside the
    swing is not the end either. A bump later than that, with quiet in
    between, belongs to another moment. ``still`` is true when the busy
    stretch runs into the cap.
    """
    cap = min(float(limit), speech_end + ACTION_EXTEND_MAX_S)
    if cap < speech_end:
        cap = speech_end
    peak = _peak_between(series, speech_start - 1.0, cap)
    if peak < MOTION_ACTION_FLOOR:
        return speech_end, False
    last = None
    quiet_for = 0.0
    t = speech_end
    step = 0.5
    while t < cap - 1e-6:
        if _active(series, t, peak):
            if last is None and (t - speech_end) > START_LOOKAHEAD_S + 1e-6:
                break
            last = t
            quiet_for = 0.0
        elif last is None:
            if (t - speech_end) > START_LOOKAHEAD_S:
                break
        else:
            quiet_for += step
            if quiet_for >= QUIET_GAP_S - 1e-6:
                break
        t += step
    if last is None:
        return speech_end, False
    settled = min(cap, math.floor(last) + 1.0)
    # The walk ran out of room on a busy sample, so the picture had not
    # dropped before the next line or the cap.
    still = (cap - float(last)) <= step + 0.05
    return settled, bool(still)


def _copy_segments(segments) -> list[dict]:
    copied = []
    for seg in segments or []:
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
        copied.append({
            "start": _round(float(seg.get("start") or 0)),
            "end": _round(float(seg.get("end") or 0)),
            "text": str(seg.get("text") or "").strip(),
            "words": words,
        })
    return copied


def _speech_runs(segments) -> list[tuple[float, float, str]]:
    """Real speech joined across a short breath. Junk lines are left out."""
    from modules.club.montage import is_real_speech_text

    pieces: list[tuple[float, float, str]] = []
    for seg in segments or []:
        text = " ".join(str(seg.get("text") or "").split())
        if not is_real_speech_text(text):
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        if end <= start:
            continue
        pieces.append((start, end, text))
    if not pieces:
        return []
    pieces.sort()
    runs: list[tuple[float, float, str]] = [pieces[0]]
    for start, end, text in pieces[1:]:
        prev_start, prev_end, prev_text = runs[-1]
        if start <= prev_end + SPEECH_GAP_S:
            joined = " ".join(part for part in (prev_text, text) if part)
            runs[-1] = (prev_start, max(prev_end, end), joined)
        else:
            runs.append((start, end, text))
    return runs


def _moment(
    source: str,
    speech_start: float,
    speech_end: float,
    text: str,
    *,
    prev_end: float | None,
    next_start: float | None,
    duration: float,
    motion: list[float],
    motion_norm: list[float],
) -> dict:
    left_wall = 0.0 if prev_end is None else float(prev_end)
    inn = max(left_wall, speech_start - SPEECH_PAD_S)
    if duration > 0:
        inn = min(inn, max(0.0, duration - 0.05))
    inn = min(inn, speech_start)

    next_wall = duration if duration > speech_end else speech_end
    if next_start is not None:
        next_wall = max(speech_end, float(next_start) - 0.05)
    elif duration > speech_end:
        next_wall = duration
    silence_after = 0.0
    if next_start is not None:
        silence_after = max(0.0, float(next_start) - speech_end)
    elif duration > speech_end:
        silence_after = duration - speech_end

    settled, still = _settled_at(motion, speech_start, speech_end, next_wall)
    tail_room = next_wall - settled
    if tail_room >= SILENCE_TAIL_MIN_S - 1e-6:
        tail = min(SILENCE_TAIL_S, SILENCE_TAIL_MAX_S, tail_room)
        out = min(next_wall, settled + tail)
    else:
        out = next_wall
    # ``still`` means the action never dropped, including when the walk
    # hit its cap and the quiet tail was added on top of motion.
    cut_mid = bool(still)
    if not cut_mid and tail_room < SILENCE_TAIL_MIN_S - 1e-6:
        cut_mid = _active(
            motion, max(speech_end, out - 0.05),
            _peak_between(motion, speech_start - 1.0, max(out, speech_end)),
        )
    if out < speech_end:
        out = speech_end
    short = silence_after + 1e-6 < SILENCE_TAIL_MIN_S
    after_end = min(next_wall, speech_end + SILENCE_TAIL_S)
    return {
        "source": source,
        "text": text,
        "speech_start": _round(speech_start),
        "speech_end": _round(speech_end),
        "in": _round(inn),
        "out": _round(out),
        "silence_after_s": _round(silence_after),
        "short_silence": short,
        "cut_mid_action": bool(cut_mid),
        "motion_during": _round(_mean(motion_norm, speech_start, speech_end), 4),
        "motion_after": _round(_mean(motion_norm, speech_end, after_end), 4),
        "take": "",
        "prefer": False,
        "repeats": 1,
    }


def _line_key(text: str) -> str:
    tokens = _tokens(text)
    if len(tokens) < REPEAT_MIN_TOKENS:
        return ""
    return " ".join(tokens)


def _mark_repeats(moments: list[dict]) -> None:
    """The busier take after a repeated line is the one where they do it.

    Motion is normalised inside each clip, so a quiet camera and a wide
    shot can be compared. A line said once is not labelled.
    """
    groups: dict[str, list[dict]] = {}
    for moment in moments:
        key = _line_key(moment.get("text") or "")
        if not key:
            continue
        groups.setdefault(key, []).append(moment)
    for group in groups.values():
        if len(group) < 2:
            continue
        for moment in group:
            moment["repeats"] = len(group)
        ranked = sorted(
            group,
            key=lambda moment: (
                float(moment.get("motion_after") or 0),
                float(moment.get("motion_during") or 0),
                str(moment.get("source") or ""),
                float(moment.get("speech_start") or 0),
            ),
            reverse=True,
        )
        best = ranked[0]
        second = ranked[1]
        # No picture change on any take: do not invent a doing take.
        if float(best.get("motion_after") or 0) <= 0 and float(best.get("motion_during") or 0) <= 0:
            continue
        if (
            float(best.get("motion_after") or 0) == float(second.get("motion_after") or 0)
            and float(best.get("motion_during") or 0) == float(second.get("motion_during") or 0)
        ):
            continue
        for moment in group:
            moment["take"] = "doing" if moment is best else "saying"
            moment["prefer"] = moment is best


def moments_for_file(item: dict) -> list[dict]:
    """Suggested cuts for one clip. ``item`` needs segments and may have motion."""
    source = str(item.get("source") or "")
    duration = _duration_of(item)
    motion = _motion_clock(item.get("motion"), duration)
    motion_norm = _normalise(motion)
    runs = _speech_runs(item.get("segments") or [])
    found = []
    for index, (start, end, text) in enumerate(runs):
        prev_end = runs[index - 1][1] if index else None
        next_start = runs[index + 1][0] if index + 1 < len(runs) else None
        found.append(_moment(
            source, start, end, text,
            prev_end=prev_end,
            next_start=next_start,
            duration=duration,
            motion=motion,
            motion_norm=motion_norm,
        ))
    return found


def build_timeline(
    files: list[dict],
    whisper: str,
    *,
    engine: str | None = None,
    transcript_whisper: str | None = None,
) -> dict:
    """One document: full transcript, per-second motion, suggested in/out.

    ``files`` entries use ``source``, ``duration_s``, ``segments``, and
    ``motion`` (raw per-second frame difference). Segments are copied so
    the transcript and the motion can be read without a second file.
    """
    rows = []
    flat: list[dict] = []
    for item in files or []:
        duration = _duration_of(item)
        motion = _motion_clock(item.get("motion"), duration)
        moments = moments_for_file({**item, "duration_s": duration, "motion": motion})
        flat.extend(moments)
        rows.append({
            "source": str(item.get("source") or ""),
            "duration_s": _round(duration) if duration else item.get("duration_s"),
            "motion": [_round(value, 4) for value in motion],
            "motion_norm": [_round(value, 4) for value in _normalise(motion)],
            "segments": _copy_segments(item.get("segments") or []),
            "moments": moments,
        })
    _mark_repeats(flat)
    payload = {
        "whisper": whisper or "unknown",
        "role": ROLE,
        "guide": dict(GUIDE),
        "files": rows,
    }
    if engine:
        payload["engine"] = engine
    if transcript_whisper and transcript_whisper != payload["whisper"]:
        payload["transcript_whisper"] = transcript_whisper
    return payload


def render_timeline_md(payload: dict) -> str:
    """A readable twin of ``timeline.json`` for the person choosing lines."""
    guide = payload.get("guide") or GUIDE
    lines = [
        "# Cut timeline",
        "",
        f"Whisper: {payload.get('whisper') or 'unknown'}",
        "",
        "Choose in and out from the moments below by comparing the motion",
        "around each line. The padding and the quiet tail are already in",
        "`in` and `out`. A repeated line marks the busier take as doing.",
        "Copy a range into brief.md as INCLUDE_WINDOWS. Name a quote as",
        "MUST_INCLUDE when the person said it has to be in. Ask for LENGTH,",
        "or none, before encoding. Do not pick a group number from a list.",
        "",
        (
            f"Pad {guide.get('speech_pad_s')}s before the words. "
            f"After the picture settles, hold about {guide.get('silence_tail_s')}s "
            f"of quiet (between {guide.get('silence_tail_min_s')}s and "
            f"{guide.get('silence_tail_max_s')}s when the clip has room)."
        ),
        "",
    ]
    if payload.get("engine"):
        lines.insert(4, f"Engine: {payload['engine']}")
        lines.insert(5, "")
    for item in payload.get("files") or []:
        lines.append(f"## {item.get('source') or 'clip'}")
        lines.append("")
        norm = item.get("motion_norm") or []
        if norm and max(norm) > 0:
            peak_at = max(range(len(norm)), key=lambda index: norm[index])
            lines.append(
                f"Busiest second: {peak_at}–{peak_at + 1} "
                f"(motion {norm[peak_at]:.2f} of this clip)."
            )
            lines.append("")
        moments = item.get("moments") or []
        if not moments:
            lines.append("(no speech to cut)")
            lines.append("")
            continue
        for moment in moments:
            label = moment.get("take") or "line"
            prefer = " preferred" if moment.get("prefer") else ""
            lines.append(
                f"### {moment['in']:.3f}–{moment['out']:.3f}  {label}{prefer}"
            )
            lines.append("")
            lines.append(str(moment.get("text") or "").strip())
            lines.append("")
            notes = [
                f"speech {moment['speech_start']:.3f}–{moment['speech_end']:.3f}",
                f"motion after {moment['motion_after']:.2f}",
                f"quiet after the line {moment['silence_after_s']:.1f}s",
            ]
            if moment.get("repeats", 1) > 1:
                notes.append(f"said {moment['repeats']} times")
            if moment.get("short_silence"):
                notes.append("less than 2s of quiet available")
            if moment.get("cut_mid_action"):
                notes.append("cut while the picture was still moving")
            lines.append("- " + " · ".join(notes))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
