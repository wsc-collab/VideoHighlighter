"""Cut timeline: speech and motion on one clock, and LENGTH: none."""

from __future__ import annotations

import json
from pathlib import Path

from modules.club.brief import parse_brief
from modules.club.montage import run_club_montage
from modules.club.timeline import (
    SILENCE_TAIL_S,
    SPEECH_PAD_S,
    build_timeline,
    moments_for_file,
)


def _seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_a_quiet_line_is_padded_and_held_through_the_silence_tail():
    moments = moments_for_file({
        "source": "lesson.mp4",
        "duration_s": 20,
        "motion": [0.0] * 20,
        "segments": [_seg(2.0, 3.0, "hold your finish")],
    })
    assert len(moments) == 1
    moment = moments[0]
    assert moment["in"] == 2.0 - SPEECH_PAD_S
    assert moment["out"] == 3.0 + SILENCE_TAIL_S
    assert moment["short_silence"] is False
    assert moment["cut_mid_action"] is False
    assert moment["take"] == ""
    assert moment["prefer"] is False


def test_the_cut_waits_out_the_action_then_keeps_the_quiet():
    # Speech ends at 2s. The picture keeps moving through second 4.
    motion = [0.0, 0.1, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    moments = moments_for_file({
        "source": "lesson.mp4",
        "duration_s": 20,
        "motion": motion,
        "segments": [_seg(1.0, 2.0, "watch this swing")],
    })
    moment = moments[0]
    assert moment["out"] >= 5.0 + SILENCE_TAIL_S - 0.05
    assert moment["out"] == 5.0 + SILENCE_TAIL_S
    assert moment["cut_mid_action"] is False
    assert moment["in"] == 1.0 - SPEECH_PAD_S


def test_a_demonstration_that_starts_after_the_line_is_not_cut_off():
    # The second the words end is quiet. The swing is the next two seconds.
    motion = [0.0, 0.0, 0.02, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    moments = moments_for_file({
        "source": "lesson.mp4",
        "duration_s": 20,
        "motion": motion,
        "segments": [_seg(1.0, 2.2, "watch this swing")],
    })
    moment = moments[0]
    assert moment["out"] >= 5.0 + SILENCE_TAIL_S - 0.05
    assert moment["cut_mid_action"] is False
    assert moment["out"] > 2.2 + SILENCE_TAIL_S


def test_a_later_bump_is_not_this_lines_action():
    motion = [0.0] * 12
    motion[8] = 1.0
    moments = moments_for_file({
        "source": "lesson.mp4",
        "duration_s": 12,
        "motion": motion,
        "segments": [_seg(1.0, 2.0, "hold your finish")],
    })
    assert moments[0]["out"] == 2.0 + SILENCE_TAIL_S
    assert moments[0]["cut_mid_action"] is False


def test_a_following_line_stops_the_tail_and_the_left_pad():
    moments = moments_for_file({
        "source": "lesson.mp4",
        "duration_s": 20,
        "motion": [0.0] * 20,
        "segments": [
            _seg(0.0, 1.0, "ready and watch"),
            _seg(3.0, 4.0, "now the finish"),
        ],
    })
    first, second = moments
    assert first["silence_after_s"] == 2.0
    assert first["out"] < 3.0
    assert first["out"] > 1.0
    assert second["in"] == 3.0 - SPEECH_PAD_S
    assert second["in"] > first["speech_end"]
    assert "ready" not in second["text"]


def test_digit_spam_is_not_a_moment():
    moments = moments_for_file({
        "source": "lesson.mp4",
        "duration_s": 8,
        "motion": [0.2] * 8,
        "segments": [_seg(0.0, 4.0, "1, 5, 5, 5, 5")],
    })
    assert moments == []


def test_a_repeated_line_prefers_the_doing_take():
    saying = {
        "source": "say.mp4",
        "duration_s": 12,
        "motion": [0.0] * 12,
        "segments": [_seg(1.0, 2.5, "hold your finish")],
    }
    doing_motion = [0.0] * 12
    doing_motion[2] = 0.2
    doing_motion[3] = 1.0
    doing_motion[4] = 0.8
    doing = {
        "source": "do.mp4",
        "duration_s": 12,
        "motion": doing_motion,
        "segments": [_seg(1.0, 2.5, "Hold your finish!")],
    }
    payload = build_timeline([saying, doing], "medium", engine="test")
    by_source = {
        moment["source"]: moment
        for item in payload["files"]
        for moment in item["moments"]
    }
    assert by_source["do.mp4"]["take"] == "doing"
    assert by_source["do.mp4"]["prefer"] is True
    assert by_source["say.mp4"]["take"] == "saying"
    assert by_source["say.mp4"]["prefer"] is False
    assert by_source["do.mp4"]["repeats"] == 2
    assert by_source["do.mp4"]["motion_after"] > by_source["say.mp4"]["motion_after"]
    assert payload["whisper"] == "medium"
    assert payload["files"][1]["segments"][0]["text"] == "Hold your finish!"
    assert len(payload["files"][1]["motion"]) == 12


def test_motion_that_never_settles_is_flagged():
    moments = moments_for_file({
        "source": "lesson.mp4",
        "duration_s": 8,
        "motion": [1.0] * 8,
        "segments": [_seg(1.0, 2.0, "keep going now")],
    })
    assert moments[0]["cut_mid_action"] is True
    assert moments[0]["out"] > 2.0


def test_length_none_is_open_and_does_not_fill(tmp_path):
    brief = parse_brief("LENGTH: none\nSTYLE: talking\nMUST_INCLUDE: \"hold your finish\"\n")
    assert brief.open_length is True
    assert (brief.length_min_s, brief.length_max_s) == (0.0, 0.0)
    assert brief.as_dict()["open_length"] is True

    (tmp_path / "brief.md").write_text(
        "LENGTH: none\nSTYLE: talking\nMUST_INCLUDE: \"hold your finish\"\n",
        encoding="utf-8",
    )
    (tmp_path / "lesson.mp4").write_bytes(b"a")
    (tmp_path / "other.mp4").write_bytes(b"b")

    def transcribe(path, model, log_fn):
        assert model == "medium"
        name = Path(path).name
        if name == "lesson.mp4":
            return [_seg(1.0, 2.4, "hold your finish")]
        return [_seg(0.0, 6.0, "we play at four and stay a while")]

    cuts = run_club_montage(
        tmp_path,
        probe=lambda _p: {"duration": 20},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0.0] * 20,
        transcribe=transcribe,
        cut=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("encode")),
        combine=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("encode")),
        dry_run=True,
    )
    assert [row["source"] for row in cuts["cuts"]] == ["lesson.mp4"]
    assert cuts["cuts"][0]["forced"] is True
    assert cuts["within_brief"] is True
    assert cuts["whisper"] == "medium"
    timeline = json.loads((tmp_path / "timeline.json").read_text(encoding="utf-8"))
    assert timeline["whisper"] == "medium"
    texts = [moment["text"] for item in timeline["files"] for moment in item["moments"]]
    assert "hold your finish" in texts
    assert any("we play at four" in text for text in texts)

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "brief.md").write_text("LENGTH: none\nSTYLE: talking\n", encoding="utf-8")
    (empty / "lesson.mp4").write_bytes(b"a")
    bare = run_club_montage(
        empty,
        probe=lambda _p: {"duration": 20},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0.0] * 20,
        transcribe=lambda *_a, **_k: [_seg(0.0, 4.0, "plenty of speech here")],
        dry_run=True,
    )
    assert bare["cuts"] == []
    assert bare["within_brief"] is False
    assert bare["draft_written"] is False
