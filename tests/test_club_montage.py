"""Club montage: brief.md plus a folder of real clips.

The ranker and the brief parser are pure. The folder run is exercised with
stand-ins for probe, peaks, motion, Whisper, and ffmpeg so the test does
not encode video or download a speech model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.club.brief import BriefError, parse_brief
from modules.club.montage import (
    ASSEMBLE_ONLY,
    Window,
    is_silent_still,
    keyword_hits,
    list_clips,
    motion_from_frames,
    playback_order,
    run_club_montage,
    score_windows_for_clip,
    select_windows,
)
from modules.club.montage import main as club_main


BRIEF = """\
# Woodinville court clips

LENGTH: 6-10s
STYLE: hype
KEYWORDS: ace, match point
NOTES: Prefer: the last shot of the point.
Keep the real audio.
"""


def test_brief_reads_length_style_keywords_and_notes():
    brief = parse_brief(BRIEF)
    assert (brief.length_min_s, brief.length_max_s) == (6, 10)
    assert brief.style == "hype"
    assert brief.keywords == ("ace", "match point")
    assert "Prefer: the last shot" in brief.notes
    assert "Keep the real audio." in brief.notes
    assert brief.wants_brand() is False
    assert brief.title == "" and brief.cta == ""


def test_brief_accepts_heading_form_and_a_single_length():
    text = """\
## LENGTH
30s

## STYLE
talking

## KEYWORDS
- rally
- putt

## NOTES
Interview cut.
"""
    brief = parse_brief(text)
    assert (brief.length_min_s, brief.length_max_s) == (30, 30)
    assert brief.style == "talking"
    assert brief.keywords == ("rally", "putt")
    assert brief.notes == "Interview cut."


def test_brief_rejects_an_unknown_style_and_a_missing_length():
    with pytest.raises(BriefError):
        parse_brief("LENGTH: 20-35s\nSTYLE: cinematic\n")
    with pytest.raises(BriefError):
        parse_brief("STYLE: hype\nKEYWORDS: ace\n")


def test_keyword_ace_does_not_match_inside_place():
    segments = [{"start": 0.0, "end": 2.0, "text": "back to the place"}]
    assert keyword_hits(segments, 0, 4, ["ace"]) == []
    segments[0]["text"] = "what an ace"
    assert keyword_hits(segments, 0, 4, ["ace"]) == ["ace"]
    segments[0]["text"] = "that was match point"
    assert keyword_hits(segments, 0, 4, ["match point"]) == ["match point"]


def test_hype_prefers_motion_and_talking_prefers_speech():
    motion = score_windows_for_clip(
        source="move.mp4", path="move.mp4", duration=4, style="hype",
        keywords=(), peak_times=[], motion_series=[0, 1, 0, 0], segments=[],
    )
    speech = score_windows_for_clip(
        source="talk.mp4", path="talk.mp4", duration=4, style="hype",
        keywords=(), peak_times=[], motion_series=[0, 0, 0, 0],
        segments=[{"start": 0.0, "end": 1.0, "text": "hello"}],
    )
    assert motion[0].score > speech[0].score

    motion_t = score_windows_for_clip(
        source="move.mp4", path="move.mp4", duration=4, style="talking",
        keywords=(), peak_times=[], motion_series=[0, 1, 0, 0], segments=[],
    )
    speech_t = score_windows_for_clip(
        source="talk.mp4", path="talk.mp4", duration=4, style="talking",
        keywords=(), peak_times=[], motion_series=[0, 0, 0, 0],
        segments=[{"start": 0.0, "end": 1.0, "text": "hello"}],
    )
    assert speech_t[0].score > motion_t[0].score


def test_talking_fill_is_speech_then_motion_then_keywords_then_audio():
    """After a must-include, the ranker follows that order. Audio cannot lead."""
    spoken = [{"start": 0.0, "end": 8.0, "text": "coach lesson finish"}]
    motion = [1, 1, 1, 1, 1, 1, 1, 1]
    still = [0, 0, 0, 0, 0, 0, 0, 0]

    def one(segments, series, keywords=(), peaks=None):
        return score_windows_for_clip(
            source="a.mp4", path="a.mp4", duration=8, style="talking",
            keywords=keywords, peak_times=list(peaks or []),
            motion_series=series, segments=segments,
        )[0]

    speech = one(spoken, still)
    speech_and_motion = one(spoken, motion)
    speech_and_keywords = one(spoken, still, keywords=("coach", "lesson", "finish"))
    silent_busy = one([], motion, peaks=[1, 2, 3, 4, 5, 6, 7])
    assert speech_and_motion.score > speech_and_keywords.score > speech.score
    assert speech.score > silent_busy.score
    # Motion does not score on a window with nobody talking.
    assert silent_busy.motion > 0
    assert silent_busy.score < 1


def test_selection_stays_inside_the_brief_and_skips_overlaps():
    windows = [
        Window("a.mp4", "a.mp4", 0, 4, 0, 0, 0, 0, score=3),
        Window("a.mp4", "a.mp4", 2, 6, 0, 0, 0, 0, score=9),
        Window("a.mp4", "a.mp4", 4, 8, 0, 0, 0, 0, score=3),
        Window("b.mp4", "b.mp4", 0, 4, 0, 0, 0, 0, score=2),
    ]
    chosen = select_windows(windows, min_s=6, max_s=10)
    # a 4-8 overlaps the kept 2-6 window, so the second cut is the other file.
    assert [(w.source, w.start, w.end) for w in chosen] == [
        ("a.mp4", 2, 6),
        ("b.mp4", 0, 4),
    ]
    assert all(w.end - w.start > 0 for w in chosen)
    total = sum(w.duration for w in chosen)
    assert 6 <= total <= 10
    assert not (chosen[0].source == chosen[1].source and chosen[0].start < chosen[1].end and chosen[1].start < chosen[0].end)


def test_selection_trims_only_to_reach_the_minimum():
    windows = [Window("a.mp4", "a.mp4", 0, 8, 0, 0, 0, 0, score=5)]
    chosen = select_windows(windows, min_s=3, max_s=3)
    assert len(chosen) == 1
    assert chosen[0].trimmed
    assert chosen[0].duration == pytest.approx(3)


def test_talking_plays_in_clip_order_and_hype_keeps_score_order():
    strong_late = Window("b.mp4", "b.mp4", 0, 4, 0, 0, 0, 0, score=9)
    quiet_early = Window("a.mp4", "a.mp4", 1, 5, 0, 0, 0, 0, score=1)
    talking = playback_order([strong_late, quiet_early], "talking")
    assert [w.source for w in talking] == ["a.mp4", "b.mp4"]
    hype = playback_order([strong_late, quiet_early], "hype")
    assert [w.source for w in hype] == ["b.mp4", "a.mp4"]


def test_motion_from_frames_is_zero_until_the_picture_changes():
    still = [b"\x10" * 4, b"\x10" * 4]
    assert motion_from_frames(still, sample_fps=1, duration=2) == [0.0, 0.0]
    changed = [b"\x00" * 4, b"\xff" * 4]
    series = motion_from_frames(changed, sample_fps=1, duration=1)
    assert series[0] == pytest.approx(1.0)


def test_list_clips_ignores_a_previous_draft(tmp_path):
    (tmp_path / "rally.mov").write_bytes(b"a")
    (tmp_path / "draft.mp4").write_bytes(b"old")
    (tmp_path / "vh_brand_leftover.mp4").write_bytes(b"tmp")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "other.mp4").write_bytes(b"b")
    names = [p.name for p in list_clips(tmp_path)]
    assert names == ["rally.mov"]


def test_run_writes_draft_cuts_and_scores(tmp_path):
    (tmp_path / "brief.md").write_text(BRIEF, encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"a")
    (tmp_path / "b.mp4").write_bytes(b"b")
    (tmp_path / "draft.mp4").write_bytes(b"stale")
    seen = []

    def probe(path):
        seen.append(Path(path).name)
        return {"duration": 8.0, "width": 320, "height": 180}

    def peaks(path):
        return [1.0] if path.endswith("a.mp4") else [6.0]

    def motion(path, duration):
        if path.endswith("a.mp4"):
            return [0, 1, 0, 0, 0, 0, 0, 0]
        return [0.0] * 8

    def transcribe(path, model, log_fn):
        assert model == "medium"
        if path.endswith("a.mp4"):
            return [{"start": 1.0, "end": 2.0, "text": "what an ace"}]
        return [{"start": 0.0, "end": 1.0, "text": "back to the place"}]

    def cut(src, start, end, dst, mode="cpu"):
        assert mode == "cpu"
        Path(dst).write_bytes(b"cut")

    def combine(files, output, log_fn=print):
        assert len(files) >= 1
        Path(output).write_bytes(b"draft")
        return output

    cuts = run_club_montage(
        tmp_path,
        probe=probe,
        peaks=peaks,
        motion=motion,
        transcribe=transcribe,
        cut=cut,
        combine=combine,
    )
    assert "draft.mp4" not in seen
    assert cuts["policy"] == ASSEMBLE_ONLY
    assert cuts["policy"]["generated_broll"] is False
    assert cuts["policy"]["generated_voices"] is False
    assert cuts["policy"]["generated_songs"] is False
    assert cuts["brand"]["requested"] is False
    assert cuts["brand"]["applied"] is False
    assert cuts["captions"]["requested"] is False
    assert cuts["captions"]["cues"] == []
    assert cuts["draft_written"] is True
    assert 6 <= cuts["assembled_seconds"] <= 10
    assert cuts["within_brief"] is True
    assert cuts["cuts"][0]["source"] == "a.mp4"
    assert "ace" in cuts["cuts"][0]["keyword_hits"]
    assert "Prefer: the last shot" in cuts["notes"]

    on_disk = json.loads((tmp_path / "cuts.json").read_text(encoding="utf-8"))
    scores = json.loads((tmp_path / "scores.json").read_text(encoding="utf-8"))
    assert on_disk["output"] == "draft.mp4"
    assert scores["signals"]["whisper"] == "medium"
    assert scores["signals"]["audio_peaks"] is True
    assert scores["signals"]["motion"] is True
    assert scores["signals"]["cut_timeline"] == "timeline.json"
    assert cuts["whisper"] == "medium"
    timeline = json.loads((tmp_path / "timeline.json").read_text(encoding="utf-8"))
    assert timeline["whisper"] == "medium"
    heard = [
        moment["text"]
        for item in timeline["files"]
        for moment in item["moments"]
    ]
    assert "what an ace" in heard
    assert scores["draft_written"] is True
    assert (tmp_path / "draft.mp4").read_bytes() == b"draft"
    assert any(row["selected"] for row in scores["windows"])


def test_dry_run_skips_the_encode(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 4s\nSTYLE: talking\nKEYWORDS:\nNOTES:\n",
        encoding="utf-8",
    )
    (tmp_path / "talk.mp4").write_bytes(b"t")

    def probe(_path):
        return {"duration": 8}

    def cut(*_args, **_kwargs):
        raise AssertionError("dry-run must not cut")

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        dry_run=True,
        probe=probe,
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0, 0, 1, 0, 0, 0, 0, 0],
        cut=cut,
        combine=cut,
    )
    assert cuts["draft_written"] is False
    assert cuts["order"] == "chronological"
    assert not (tmp_path / "draft.mp4").exists()
    assert (tmp_path / "scores.json").is_file()


def test_cli_missing_folder_is_a_brief_error(tmp_path):
    missing = tmp_path / "no-such-clips"
    assert club_main(["--club", str(missing)]) == 2


BRANDED = """\
LENGTH: 6-10s
STYLE: hype
TITLE: Match day
SUBTITLE: Woodinville
COLORS: #1B4D3E, #F4E8C1
FONT: /tmp/Club.ttf
CTA: See you Saturday
KEYWORDS:
NOTES: Keep the picture.
"""


def test_brief_reads_optional_type_and_rejects_a_bad_color():
    brief = parse_brief(BRANDED)
    assert brief.wants_brand() is True
    assert brief.title == "Match day"
    assert brief.subtitle == "Woodinville"
    assert brief.colors == ("#1B4D3E", "#F4E8C1")
    assert brief.font == "/tmp/Club.ttf"
    assert brief.cta == "See you Saturday"
    short = parse_brief("LENGTH: 10s\nSTYLE: talking\nCOLORS: #fff\nTITLE: Hi\n")
    assert short.colors == ("#FFFFFF",)
    with pytest.raises(BriefError):
        parse_brief("LENGTH: 10s\nSTYLE: hype\nCOLORS: green\n")


def test_brand_colors_and_plate_windows():
    from modules.club.brand import brand_colors, plate_windows
    assert brand_colors(()) == ("111111", "FFFFFF")
    assert brand_colors(("#1B4D3E", "#F4E8C1")) == ("1B4D3E", "F4E8C1")
    assert brand_colors(("#FFFFFF",))[1] == "111111"
    assert brand_colors(("#000000",))[1] == "FFFFFF"
    long = plate_windows(20)
    assert long["title"] == pytest.approx((0.0, 3.0))
    assert long["lower"][0] == pytest.approx(3.0)
    assert long["end"][1] == pytest.approx(20.0)
    short = plate_windows(2)
    assert short["lower"] == (0.0, 0.0)
    assert short["title"][1] == pytest.approx(short["end"][0])


def test_brand_filter_draws_type_on_the_cut_and_skips_a_plain_brief():
    from modules.club.brand import brand_filter
    plain = parse_brief(BRIEF)
    assert brand_filter(plain, 20, "/tmp/Club.ttf") == ""
    brief = parse_brief(BRANDED.replace("Match day", "Club: Night"))
    graph = brand_filter(brief, 20, r"C:\Windows\Fonts\arial.ttf", 1920, 1080)
    assert "Club\\: Night" in graph
    assert "Woodinville" in graph
    assert "See you Saturday" in graph
    assert "0x1B4D3E" in graph
    assert "0xF4E8C1" in graph
    assert "drawbox=" in graph
    assert "drawtext=" in graph
    assert r"between(t\," in graph
    assert "C\\:/Windows/Fonts/arial.ttf" in graph
    assert "@0.78" in graph


def test_talking_title_and_cta_sit_on_the_picture():
    from modules.club.brand import brand_filter
    from modules.club.captions import talking_windows
    brief = parse_brief(
        "LENGTH: 12s\nSTYLE: talking\nTITLE: Match day\n"
        "SUBTITLE: Woodinville\nCTA: See you Saturday\n"
        "COLORS: #1B4D3E, #F4E8C1\n"
    )
    windows = talking_windows(12)
    graph = brand_filter(
        brief, 12, "/tmp/Club.ttf", 1920, 1080,
        windows={"title": windows["title"], "lower": windows["lower"], "end": windows["end"]},
        centered=True,
    )
    assert "drawbox=" not in graph
    assert "box=1" not in graph
    assert "box=0" in graph
    assert "borderw=0" in graph
    assert "text_align=center" in graph
    assert "fontcolor=0xFFFFFF" in graph
    assert "bordercolor" not in graph
    assert "0x111111" not in graph
    assert "0x1B4D3E" not in graph
    assert "0xF4E8C1" not in graph
    assert "@0.78" not in graph
    assert "(iw-iw*0.72)/2" not in graph
    assert "(ih-ih*0.28)/2" not in graph
    assert "(w-tw)/2" in graph
    assert "(h-th)/2" in graph
    assert "ih*0.30" not in graph
    assert "y=h*0.05" not in graph
    assert "drawbox=x=0:y=0:" not in graph


def test_run_burns_type_when_the_brief_asks_and_keeps_the_cut_if_it_fails(tmp_path):
    (tmp_path / "brief.md").write_text(BRANDED, encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"a")
    calls = []

    def combine(files, output, log_fn=print):
        Path(output).write_bytes(b"draft")
        return output

    def brand(src, dst, brief, duration, log_fn=print):
        calls.append((Path(src).name, brief.title, duration))
        Path(dst).write_bytes(b"branded")

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [1.0],
        motion=lambda _p, _d: [0, 1, 0, 0, 0, 0, 0, 0],
        transcribe=lambda *_a, **_k: [],
        cut=lambda src, start, end, dst, mode="cpu": Path(dst).write_bytes(b"cut"),
        combine=combine,
        brand=brand,
    )
    assert calls and calls[0][1] == "Match day"
    assert cuts["brand"]["applied"] is True
    assert cuts["brand"]["error"] is None
    assert cuts["policy"]["generated_broll"] is False
    assert (tmp_path / "draft.mp4").read_bytes() == b"branded"

    def fail(*_a, **_k):
        raise RuntimeError("drawtext missing")

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [1.0],
        motion=lambda _p, _d: [0, 1, 0, 0, 0, 0, 0, 0],
        cut=lambda src, start, end, dst, mode="cpu": Path(dst).write_bytes(b"cut"),
        combine=combine,
        brand=fail,
    )
    assert cuts["draft_written"] is True
    assert cuts["brand"]["requested"] is True
    assert cuts["brand"]["applied"] is False
    assert "drawtext missing" in cuts["brand"]["error"]
    assert (tmp_path / "draft.mp4").read_bytes() == b"draft"
    assert list(tmp_path.glob("vh_brand_*.mp4")) == []


def test_dry_run_does_not_draw_type(tmp_path):
    (tmp_path / "brief.md").write_text(BRANDED, encoding="utf-8")
    (tmp_path / "a.mp4").write_bytes(b"a")

    def brand(*_a, **_k):
        raise AssertionError("dry-run must not draw type")

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        dry_run=True,
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [1, 0, 0, 0, 0, 0, 0, 0],
        cut=brand,
        combine=brand,
        brand=brand,
    )
    assert cuts["brand"]["requested"] is True
    assert cuts["brand"]["applied"] is False
    assert not (tmp_path / "draft.mp4").exists()


def test_resolve_font_prefers_an_existing_file(tmp_path):
    from modules.club.brand import resolve_font
    font = tmp_path / "Club.ttf"
    font.write_bytes(b"font")
    assert resolve_font(str(font)) == str(font.resolve())


def test_brand_defaults_to_inter_for_tier1_wsc_and_bsc(tmp_path, monkeypatch):
    from modules.club import brand as brand_mod
    from modules.club.brand import font_for_brand, resolve_font, type_font
    tier1 = parse_brief(
        "LENGTH: 20s\nSTYLE: talking\nBRAND: tier1\n"
        "TITLE: Private lesson with Coach John Wang\n"
    )
    assert tier1.brand == "tier1"
    assert tier1.font == ""
    assert font_for_brand(tier1.brand) == "Inter"
    assert type_font(tier1) == "Inter"
    wsc = parse_brief("LENGTH: 20s\nSTYLE: talking\nBRAND: wsc\nTITLE: Clinic\n")
    bsc = parse_brief("LENGTH: 20s\nSTYLE: talking\nBRAND: BSC\nTITLE: Clinic\n")
    assert type_font(wsc) == "Inter"
    assert type_font(bsc) == "Inter"
    assert "Interwald" not in type_font(tier1)
    explicit = parse_brief(
        "LENGTH: 20s\nSTYLE: talking\nBRAND: tier1\nFONT: /tmp/Club.ttf\nTITLE: Hi\n"
    )
    assert type_font(explicit) == "/tmp/Club.ttf"
    with pytest.raises(BriefError):
        parse_brief("LENGTH: 10s\nSTYLE: talking\nBRAND: nike\n")

    inter = tmp_path / "Inter-Regular.otf"
    inter.write_bytes(b"in")
    assert resolve_font("Inter", search_dirs=[tmp_path]) == str(inter.resolve())

    empty = tmp_path / "no-inter"
    empty.mkdir()
    monkeypatch.setattr(brand_mod, "_fontconfig", lambda _name: "")
    notes = []
    chosen = resolve_font("Inter", log_fn=notes.append, search_dirs=[empty])
    assert chosen
    assert "Interwald" not in Path(chosen).name
    assert any("system sans" in line for line in notes)


TALKING = """\
LENGTH: 6-10s
STYLE: talking
TITLE: Match day
CTA: See you Saturday
FONT: DejaVu Sans
CAPTION_COLOR: #FFFFFF
CAPTION_STROKE: #000000 3
CAPTION_SIZE: 42
CAPTION_POSITION: bottom
KEYWORDS:
NOTES: This note is not a caption.
"""


def test_caption_fields_and_a_bad_position():
    brief = parse_brief(TALKING)
    assert brief.caption_color == "#FFFFFF"
    assert brief.caption_stroke == "#000000"
    assert brief.caption_stroke_width == 3
    assert brief.caption_size == 42
    assert brief.caption_position == "bottom"
    assert brief.wants_brand() is True
    with pytest.raises(BriefError):
        parse_brief("LENGTH: 10s\nSTYLE: talking\nCAPTION_POSITION: karaoke\n")


def test_caption_cues_are_spoken_words_inside_the_body():
    from modules.club.captions import caption_cues, render_ass, talking_windows
    windows = talking_windows(12)
    body_start, body_end = windows["captions"]
    assert windows["title"] == pytest.approx((0.0, 2.5))
    assert windows["lower"] == (0.0, 0.0)
    assert body_start == pytest.approx(2.5)
    assert body_end == pytest.approx(9.5)
    segments = [
        {"start": 0.2, "end": 1.5, "text": "too early for captions"},
        {
            "start": 3.0,
            "end": 6.0,
            "text": "we play at four on saturday morning",
            "words": [
                {"start": 3.0, "end": 3.4, "word": "we"},
                {"start": 3.4, "end": 3.8, "word": "play"},
                {"start": 3.8, "end": 4.1, "word": "at"},
                {"start": 4.1, "end": 4.5, "word": "four"},
                {"start": 4.5, "end": 4.8, "word": "on"},
                {"start": 4.8, "end": 5.3, "word": "saturday"},
                {"start": 5.3, "end": 5.8, "word": "morning"},
            ],
        },
        {"start": 10.0, "end": 11.5, "text": "join us this weekend"},
    ]
    cues = caption_cues(segments, body_start=body_start, body_end=body_end)
    blob = " ".join(cue.text for cue in cues)
    assert blob == "we play at four on saturday morning"
    assert "too early" not in blob
    assert "join us" not in blob
    assert "This note is not a caption" not in blob
    assert cues[0].start >= body_start
    assert cues[-1].end <= body_end + 0.05
    assert all(len(cue.text.split()) <= 4 for cue in cues)
    assert [cue.text for cue in cues] == [
        "we play at four",
        "on saturday morning",
    ]
    brief = parse_brief(TALKING)
    script = render_ass(cues, brief, width=1280, height=720, font_path="")
    assert "Dialogue:" in script
    assert "we play at four" in script or "we play" in script
    assert "Style: Caption,DejaVu Sans,42," in script
    assert "&H00FFFFFF&" in script
    assert "&H00000000&" in script
    assert ",2,40,40,48,1" in script
    assert "join us" not in script


def test_talking_pack_burns_title_captions_and_cta(tmp_path):
    (tmp_path / "brief.md").write_text(TALKING, encoding="utf-8")
    (tmp_path / "talk.mp4").write_bytes(b"t")
    seen = {}

    def caption_transcribe(path, model, log_fn):
        seen["path"] = Path(path).name
        seen["model"] = model
        return {
            "engine": "test",
            "segments": [
                {"start": 0.2, "end": 1.0, "text": "too early"},
                {"start": 3.0, "end": 5.0, "text": "we play at four"},
                {"start": 7.0, "end": 7.8, "text": "saved for the end card"},
            ],
        }

    def caption_burn(src, dst, brief, duration, cues, log_fn=print):
        seen["cues"] = [cue.text for cue in cues]
        seen["title"] = brief.title
        seen["cta"] = brief.cta
        Path(dst).write_bytes(b"talking")

    cuts = run_club_montage(
        tmp_path,
        use_whisper=True,
        whisper_model="tiny",
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0, 0, 1, 0, 0, 0, 0, 0],
        transcribe=lambda *_a, **_k: [],
        cut=lambda src, start, end, dst, mode="cpu": Path(dst).write_bytes(b"cut"),
        combine=lambda files, output, log_fn=print: Path(output).write_bytes(b"draft") or output,
        caption_transcribe=caption_transcribe,
        caption_burn=caption_burn,
    )
    assert seen["path"] == "draft.mp4"
    assert seen["model"] == "tiny"
    assert seen["cues"] == ["we play at four"]
    assert seen["title"] == "Match day"
    assert seen["cta"] == "See you Saturday"
    assert cuts["captions"]["burned"] is True
    assert cuts["captions"]["engine"] == "test"
    assert cuts["captions"]["source"] == "speech"
    assert [row["text"] for row in cuts["captions"]["cues"]] == ["we play at four"]
    assert cuts["brand"]["applied"] is True
    assert cuts["policy"]["generated_voices"] is False
    assert (tmp_path / "draft.mp4").read_bytes() == b"talking"


def test_talking_without_whisper_does_not_invent_captions(tmp_path):
    (tmp_path / "brief.md").write_text(TALKING, encoding="utf-8")
    (tmp_path / "talk.mp4").write_bytes(b"t")

    def caption_transcribe(*_a, **_k):
        raise AssertionError("whisper is off")

    def caption_burn(src, dst, brief, duration, cues, log_fn=print):
        assert cues == []
        Path(dst).write_bytes(b"title-only")

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0, 0, 1, 0, 0, 0, 0, 0],
        cut=lambda src, start, end, dst, mode="cpu": Path(dst).write_bytes(b"cut"),
        combine=lambda files, output, log_fn=print: Path(output).write_bytes(b"draft") or output,
        caption_transcribe=caption_transcribe,
        caption_burn=caption_burn,
    )
    assert cuts["captions"]["engine"] == "skipped"
    assert cuts["captions"]["cues"] == []
    assert cuts["captions"]["burned"] is True
    assert (tmp_path / "draft.mp4").read_bytes() == b"title-only"


def test_thirty_second_talking_brief_uses_type_on_the_picture():
    """LENGTH 30s, no caption size or position, and no plate behind the type."""
    from modules.club.brand import brand_filter
    from modules.club.captions import caption_cues, caption_style, talking_windows
    brief = parse_brief(
        "LENGTH: 30s\nSTYLE: talking\nTITLE: Match day\n"
        "CTA: See you Saturday\nFONT: DejaVu Sans\n"
        "COLORS: #1B4D3E, #F4E8C1\n"
    )
    assert brief.length_min_s == 30
    assert brief.length_max_s == 30
    assert brief.caption_position == "two_fifths"
    assert brief.caption_size == 0
    style = caption_style(brief, 1080, 1920)
    assert style["size"] > 16
    assert style["position"] == "two_fifths"
    assert style["alignment"] == 5
    assert style["margin_v"] == 0
    assert style["anchor_y"] == int(round(1920 * 0.6))
    assert style["anchor_y"] > 1920 / 2
    assert caption_style(brief, 1080, 1920)["color"] == "#FFFFFF"
    assert caption_style(brief, 1080, 1920)["stroke"] == "#000000"
    windows = talking_windows(30)
    graph = brand_filter(
        brief, 30, "/tmp/Club.ttf", 1920, 1080,
        windows={"title": windows["title"], "lower": windows["lower"], "end": windows["end"]},
        centered=True,
    )
    assert "drawbox=" not in graph
    assert "box=1" not in graph
    assert "box=0" in graph
    assert "borderw=0" in graph
    assert "text_align=center" in graph
    assert "fontcolor=0xFFFFFF" in graph
    assert "bordercolor" not in graph
    assert "0x111111" not in graph
    assert "0x1B4D3E" not in graph
    assert "0xF4E8C1" not in graph
    assert "@0.78" not in graph
    assert "(w-tw)/2" in graph
    assert "(h-th)/2" in graph
    assert "y=h*0.05" not in graph
    assert "drawbox=x=0:y=0:" not in graph
    words = "we play at four on saturday".split()
    cues = caption_cues(
        [{
            "start": 4.0,
            "end": 8.0,
            "text": "we play at four on saturday",
            "words": [
                {"start": 4.0 + i * 0.4, "end": 4.3 + i * 0.4, "word": word}
                for i, word in enumerate(words)
            ],
        }],
        body_start=windows["captions"][0],
        body_end=windows["captions"][1],
    )
    assert [cue.text for cue in cues] == ["we play at four", "on saturday"]


def test_talking_title_is_editorial_and_type_stays_white():
    from modules.club.brand import brand_filter, neutral_ink
    from modules.club.captions import CaptionCue, caption_cues, caption_style, render_ass, talking_windows
    brief = parse_brief(
        "LENGTH: 20s\nSTYLE: talking\n"
        "TITLE: Private lesson with Coach John Wang\n"
        "KEYWORDS: finish, lesson\n"
        "NOTES: Prefer the finish cue.\n"
        "COLORS: #1B4D3E, #F4E8C1\n"
        "CAPTION_COLOR: #F4E8C1\n"
    )
    assert brief.title == "Private lesson with Coach John Wang"
    assert brief.cta == ""
    windows = talking_windows(20)
    graph = brand_filter(
        brief, 20, "/tmp/Club.ttf", 1920, 1080,
        windows={"title": windows["title"], "lower": windows["lower"], "end": windows["end"]},
        centered=True,
    )
    assert "Private lesson" in graph
    assert "with Coach" in graph
    assert "John Wang" in graph
    assert "Private lesson with Coach John Wang" not in graph
    assert graph.count("drawtext=") == 3
    assert "Hold your finish" not in graph
    assert "drawbox=" not in graph
    assert "box=1" not in graph
    assert "fontcolor=0xFFFFFF" in graph
    assert "borderw=0" in graph
    assert "text_align=center" in graph
    assert "bordercolor" not in graph
    assert "0x111111" not in graph
    assert "0x1B4D3E" not in graph
    assert "0xF4E8C1" not in graph
    assert neutral_ink("#F4E8C1") == "FFFFFF"
    assert neutral_ink("#1B4D3E") == "000000"
    style = caption_style(brief, 1920, 1080)
    assert style["color"] == "#FFFFFF"
    assert style["stroke"] == "#000000"
    cues = caption_cues(
        [{
            "start": 4.0,
            "end": 6.0,
            "text": "Hold your finish",
            "words": [
                {"start": 4.0, "end": 4.4, "word": "Hold"},
                {"start": 4.4, "end": 4.8, "word": "your"},
                {"start": 4.8, "end": 5.4, "word": "finish"},
            ],
        }],
        body_start=windows["captions"][0],
        body_end=windows["captions"][1],
    )
    assert [cue.text for cue in cues] == ["Hold your finish"]
    black = parse_brief("LENGTH: 20s\nSTYLE: talking\nCAPTION_COLOR: #000000\n")
    assert caption_style(black, 1920, 1080)["color"] == "#000000"
    assert caption_style(black, 1920, 1080)["stroke"] == "#FFFFFF"
    script = render_ass(
        [CaptionCue(4.0, 5.4, "Hold your finish")],
        brief, width=1920, height=1080, font_path="",
    )
    assert "&HFF000000" in script
    assert style["position"] == "two_fifths"
    assert style["alignment"] == 5
    assert style["margin_v"] == 0
    assert style["anchor_y"] == int(round(1080 * 0.6))
    assert style["anchor_y"] > 1080 / 2
    assert (
        f",1,{style['stroke_width']},0,{style['alignment']},"
        f"40,40,{style['margin_v']},1"
    ) in script
    assert f"{{\\an5\\pos({1920 // 2},{style['anchor_y']})}}" in script


def test_a_wide_caption_becomes_the_next_cue_not_a_second_line():
    from modules.club.captions import CaptionCue, render_ass, split_wide_cues
    short = split_wide_cues([CaptionCue(1.0, 2.0, "we play at four")], 1080, 16)
    assert [cue.text for cue in short] == ["we play at four"]
    long = "one two three four five six seven eight nine ten"
    cues = split_wide_cues([CaptionCue(1.0, 3.0, long)], 320, 16)
    assert len(cues) > 1
    assert all("\n" not in cue.text for cue in cues)
    assert " ".join(cue.text for cue in cues) == long
    assert cues[0].start == 1.0
    assert cues[-1].end == 3.0
    assert all(cue.end > cue.start for cue in cues)
    brief = parse_brief("LENGTH: 12s\nSTYLE: talking\nTITLE: Hi\n")
    from modules.club.captions import caption_style
    size = caption_style(brief, 320, 1080)["size"]
    shown = split_wide_cues([CaptionCue(1.0, 3.0, long)], 320, size)
    script = render_ass(
        [CaptionCue(1.0, 3.0, long)],
        brief, width=320, height=1080, font_path="",
    )
    assert r"\N" not in script
    assert script.count("Dialogue:") == len(shown)
    assert script.count(r"\an5\pos(") == len(shown)
    assert len(shown) > 1
    assert "WrapStyle: 2" in script


def test_title_uses_a_bold_file_when_one_is_installed(tmp_path):
    import re

    from modules.club.brand import bold_font, brand_filter
    from modules.club.captions import talking_windows
    regular = tmp_path / "Inter-Regular.otf"
    bold = tmp_path / "Inter-Bold.otf"
    regular.write_bytes(b"r")
    bold.write_bytes(b"b")
    assert bold_font(str(regular)) == str(bold.resolve())
    brief = parse_brief(
        "LENGTH: 12s\nSTYLE: talking\nTITLE: Private lesson\nCTA: Book\n"
    )
    windows = talking_windows(12)
    graph = brand_filter(
        brief, 12, str(regular), 1080, 1920,
        windows={"title": windows["title"], "lower": windows["lower"], "end": windows["end"]},
        centered=True,
    )
    assert "Inter-Bold.otf" in graph
    assert "borderw=0" in graph
    assert "text_align=center" in graph
    assert "(w-tw)/2" in graph
    sizes = [int(n) for n in re.findall(r"fontsize=(\d+)", graph)]
    assert len(sizes) == 2
    assert sizes[0] > sizes[1]


def test_caption_defaults_cover_about_three_quarters_of_the_width():
    from modules.club.captions import (
        CAPTION_EM, CAPTION_TYPICAL_CHARS, CAPTION_WIDTH_SHARE, CAPTION_Y_TWO_FIFTHS,
        CaptionCue, caption_cues, caption_style, default_caption_size, render_ass,
        two_fifths_center_y,
    )
    brief = parse_brief("LENGTH: 12s\nSTYLE: talking\nTITLE: Hi\nCTA: Go\n")
    assert brief.caption_position == "two_fifths"
    assert brief.caption_size == 0
    # 9:16: a short cue covers about 75% of the width and stays under the title.
    portrait = default_caption_size(1080, 1920)
    span = CAPTION_TYPICAL_CHARS * CAPTION_EM * portrait
    assert span == pytest.approx(1080 * CAPTION_WIDTH_SHARE, rel=0.08)
    assert portrait < max(18, int(1920 * 0.055))
    assert caption_style(brief, 1080, 1920)["size"] == portrait
    # Wider frame, larger captions.
    assert default_caption_size(1440, 2560) > portrait
    # Landscape stays under the title even when 75% of the width would not.
    landscape = default_caption_size(1920, 1080)
    assert landscape < max(18, int(1080 * 0.055))
    placed = caption_style(brief, 1920, 1080)
    assert placed["position"] == "two_fifths"
    assert placed["alignment"] == 5
    assert placed["margin_v"] == 0
    assert placed["anchor_y"] == two_fifths_center_y(1080)
    assert placed["anchor_y"] == int(round(1080 * (1 - CAPTION_Y_TWO_FIFTHS)))
    assert placed["anchor_y"] / 1080 == pytest.approx(0.6)
    assert placed["anchor_y"] > 1080 / 2
    middle = parse_brief("LENGTH: 12s\nSTYLE: talking\nCAPTION_POSITION: middle\n")
    assert middle.caption_position == "center"
    words = "one two three four five six".split()
    segments = [{
        "start": 1.0,
        "end": 4.0,
        "text": "one two three four five six",
        "words": [
            {"start": 1.0 + i * 0.4, "end": 1.3 + i * 0.4, "word": word}
            for i, word in enumerate(words)
        ],
    }]
    cues = caption_cues(segments, body_start=0, body_end=10)
    assert [cue.text for cue in cues] == ["one two three four", "five six"]
    plain = [{"start": 1.0, "end": 4.0, "text": "one two three four five six"}]
    wrapped = caption_cues(plain, body_start=0, body_end=10)
    assert [cue.text for cue in wrapped] == ["one two three four", "five six"]
    script = render_ass(
        [CaptionCue(1.0, 2.0, "one two three four")],
        brief, width=1920, height=1080, font_path="",
    )
    assert f"Style: Caption,DejaVu Sans,{caption_style(brief, 1920, 1080)['size']}," in script
    assert ",5,40,40,0,1" in script
    assert f"{{\\an5\\pos({1920 // 2},{placed['anchor_y']})}}" in script


def test_two_fifths_is_the_talking_default_and_other_positions_still_burn():
    from modules.club.captions import (
        CaptionCue, caption_anchor, caption_pos_override, caption_style, render_ass,
        two_fifths_center_y,
    )
    omitted = parse_brief("LENGTH: 12s\nSTYLE: talking\nTITLE: Hi\n")
    assert omitted.caption_position == "two_fifths"
    for spelling in ("two_fifths", "two-fifths", "0.4", "0.40", "2/5"):
        named = parse_brief(
            f"LENGTH: 12s\nSTYLE: talking\nCAPTION_POSITION: {spelling}\n"
        )
        assert named.caption_position == "two_fifths"
    hype = parse_brief("LENGTH: 12s\nSTYLE: hype\n")
    assert hype.caption_position == "center"
    assert caption_anchor("center", 1080) == (5, 0)
    assert caption_anchor("middle", 1080) == (5, 0)
    assert caption_anchor("bottom", 1080) == (2, 48)
    assert caption_anchor("top", 720) == (8, 48)
    for height in (720, 1080, 1920):
        align, margin = caption_anchor("two_fifths", height)
        assert align == 5
        assert margin == 0
        y = two_fifths_center_y(height)
        assert y == int(round(height * 0.6))
        assert y > height / 2
        assert y / height == pytest.approx(0.6, abs=0.002)
        assert caption_pos_override("two_fifths", 1080, height) == (
            f"{{\\an5\\pos({1080 // 2},{y})}}"
        )
    assert caption_pos_override("center", 1920, 1080) == ""
    assert caption_pos_override("bottom", 1920, 1080) == ""
    centered = parse_brief(
        "LENGTH: 12s\nSTYLE: talking\nCAPTION_POSITION: center\n"
    )
    assert caption_style(centered, 1920, 1080)["alignment"] == 5
    assert caption_style(centered, 1920, 1080)["margin_v"] == 0
    assert caption_style(centered, 1920, 1080)["anchor_y"] == 0
    script = render_ass(
        [CaptionCue(1.0, 2.0, "we play at four")],
        centered, width=1920, height=1080, font_path="",
    )
    assert ",5,40,40,0,1" in script
    assert "\\pos" not in script
    assert script.count("Dialogue:") == 1
    low = render_ass(
        [CaptionCue(1.0, 2.0, "we play at four")],
        omitted, width=1920, height=1080, font_path="",
    )
    assert f"{{\\an5\\pos({1920 // 2},{two_fifths_center_y(1080)})}}" in low
    assert ",8,40,40," not in low
    top = parse_brief("LENGTH: 12s\nSTYLE: talking\nCAPTION_POSITION: top\n")
    top_script = render_ass(
        [CaptionCue(1.0, 2.0, "we play at four")],
        top, width=1280, height=720, font_path="",
    )
    assert ",8,40,40,48,1" in top_script
    assert "\\pos" not in top_script
    bottom = parse_brief("LENGTH: 12s\nSTYLE: talking\nCAPTION_POSITION: bottom\n")
    bottom_script = render_ass(
        [CaptionCue(1.0, 2.0, "we play at four")],
        bottom, width=1280, height=720, font_path="",
    )
    assert ",2,40,40,48,1" in bottom_script
    assert "\\pos" not in bottom_script


def test_quiet_clip_names_keep_a_same_session_mic_and_mark_a_soft_extra():
    from modules.club.montage import quiet_clip_names
    assert quiet_clip_names([("lav.mp4", -10.0), ("angle.mp4", -22.0)]) == []
    assert quiet_clip_names([("lav.mp4", -10.0), ("phone.mp4", -28.0)]) == ["phone.mp4"]
    assert quiet_clip_names([
        ("lav.mp4", -10.0), ("angle.mp4", -14.0), ("phone.mp4", -32.0),
    ]) == ["phone.mp4"]
    assert quiet_clip_names([("lav.mp4", -10.0), ("phone.mp4", None)]) == []
    assert quiet_clip_names([("only.mp4", -40.0)]) == []


def test_talking_drops_a_clip_much_quieter_than_the_mic(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 4s\nSTYLE: talking\nTITLE: Hi\n", encoding="utf-8",
    )
    (tmp_path / "lav.mp4").write_bytes(b"a")
    (tmp_path / "phone.mp4").write_bytes(b"b")

    def loudness(path):
        return -10.0 if path.endswith("lav.mp4") else -28.0

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        dry_run=True,
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [1.0],
        motion=lambda _p, _d: [0, 1, 0, 0, 0, 0, 0, 0],
        loudness=loudness,
    )
    assert {row["source"] for row in cuts["cuts"]} == {"lav.mp4"}
    assert cuts["mic_preference"]["mode"] == "mixed"
    assert cuts["mic_preference"]["soft"] == ["phone.mp4"]
    assert cuts["mic_preference"]["dropped"] == []
    assert cuts["mic_preference"]["factor"] == 0.85
    scored = json.loads((tmp_path / "scores.json").read_text(encoding="utf-8"))
    assert any(row["source"] == "phone.mp4" for row in scored["windows"])


def test_hype_keeps_a_quiet_clip(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 16s\nSTYLE: hype\n", encoding="utf-8",
    )
    (tmp_path / "lav.mp4").write_bytes(b"a")
    (tmp_path / "phone.mp4").write_bytes(b"b")

    def loudness(_path):
        raise AssertionError("hype does not meter clips")

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        dry_run=True,
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [1.0],
        motion=lambda _p, _d: [0, 1, 0, 0, 0, 0, 0, 0],
        loudness=loudness,
    )
    assert {row["source"] for row in cuts["cuts"]} == {"lav.mp4", "phone.mp4"}
    assert cuts["mic_preference"]["dropped"] == []
    assert cuts["mic_preference"]["enabled"] is False


def test_no_cta_leaves_the_close_to_captions_and_the_name_is_accented(tmp_path):
    import re

    from modules.club.brand import brand_filter, split_title_accent
    from modules.club.captions import talking_windows
    windows = talking_windows(15, cta=False)
    assert windows["end"] == (0.0, 0.0)
    assert windows["captions"][1] == pytest.approx(15)
    brief = parse_brief(
        "LENGTH: 15s\nSTYLE: talking\n"
        "TITLE: Private lesson with Coach John Wang\n"
        "TITLE_ACCENT: John Wang\n"
        "FONT: Inter\n"
    )
    assert brief.cta == ""
    assert brief.title_accent == "John Wang"
    assert split_title_accent(brief.title, brief.title_accent) == [
        ("Private lesson with Coach ", False),
        ("John Wang", True),
    ]
    body = tmp_path / "Inter-Bold.otf"
    script = tmp_path / "GreatVibes-Regular.ttf"
    body.write_bytes(b"bold")
    script.write_bytes(b"script")
    graph = brand_filter(
        brief, 15, str(body), 1080, 1920,
        windows={"title": (0.0, 2.5), "lower": (0.0, 0.0), "end": (0.0, 0.0)},
        centered=True,
        accent_font=str(script),
    )
    assert "John Wang" in graph
    assert "Private lesson" in graph
    assert "with Coach" in graph
    assert "GreatVibes-Regular.ttf" in graph
    assert "Inter-Bold.otf" in graph
    assert "shadowcolor=black@0.58" in graph
    assert "shadowx=3" in graph
    assert "borderw=0" in graph
    assert "drawbox=" not in graph
    # Three large lines, not one line shrunk to fit the whole sentence.
    assert graph.count("drawtext=") == 3
    sizes = [int(n) for n in re.findall(r"fontsize=(\d+)", graph)]
    assert len(sizes) == 3
    assert min(sizes) >= 72
    plain = parse_brief(
        "LENGTH: 15s\nSTYLE: talking\nTITLE: Private lesson\nFONT: Inter\n"
    )
    plain_graph = brand_filter(
        plain, 15, str(body), 1080, 1920,
        windows={"title": (0.0, 2.5), "lower": (0.0, 0.0), "end": (2.5, 5.0)},
        centered=True,
    )
    assert plain_graph.count("drawtext=") == 1
    assert "shadowcolor=black@0.58" in plain_graph


def test_captions_md_is_written_before_the_burn_and_an_edit_is_what_burns(tmp_path):
    from modules.club.captions import parse_captions_md, render_captions_md, CaptionCue
    cue = CaptionCue(2.5, 4.2, "hold your finish")
    text = render_captions_md([cue])
    assert "hold your finish" in text
    assert parse_captions_md(text)[0].text == "hold your finish"
    assert parse_captions_md(text)[0].start == pytest.approx(2.5)
    edited = "0:03.000–0:05.000\nwe play at four\n"
    assert parse_captions_md(edited)[0].text == "we play at four"

    (tmp_path / "brief.md").write_text(
        "LENGTH: 8s\nSTYLE: talking\nTITLE: Match day\n",
        encoding="utf-8",
    )
    (tmp_path / "talk.mp4").write_bytes(b"t")
    (tmp_path / "captions.md").write_text(edited, encoding="utf-8")

    def caption_transcribe(*_a, **_k):
        raise AssertionError("an edited captions.md is the burn source")

    def caption_burn(src, dst, brief, duration, cues, log_fn=print):
        assert [cue.text for cue in cues] == ["we play at four"]
        assert brief.cta == ""
        Path(dst).write_bytes(b"edited")

    cuts = run_club_montage(
        tmp_path,
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0] * 8,
        transcribe=lambda *_a, **_k: [],
        cut=lambda src, start, end, dst, mode="cpu": Path(dst).write_bytes(b"cut"),
        combine=lambda files, output, log_fn=print: Path(output).write_bytes(b"draft") or output,
        caption_transcribe=caption_transcribe,
        caption_burn=caption_burn,
    )
    assert cuts["captions"]["source"] == "captions.md"
    assert cuts["captions"]["cues"][0]["text"] == "we play at four"
    assert (tmp_path / "captions.md").read_text(encoding="utf-8") == edited

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "brief.md").write_text(
        "LENGTH: 8s\nSTYLE: talking\nTITLE: Match day\nCTA: Book\n",
        encoding="utf-8",
    )
    (fresh / "talk.mp4").write_bytes(b"t")
    seen = {}

    def transcribe_fresh(path, model, log_fn):
        seen["model"] = model
        return {"engine": "test", "segments": [
            {"start": 3.0, "end": 5.0, "text": "hold your finish"},
        ]}

    def burn_fresh(src, dst, brief, duration, cues, log_fn=print):
        seen["cues"] = [cue.text for cue in cues]
        Path(dst).write_bytes(b"fresh")

    run_club_montage(
        fresh,
        whisper_model="medium",
        probe=lambda _p: {"duration": 8.0},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0] * 8,
        transcribe=lambda *_a, **_k: [],
        cut=lambda src, start, end, dst, mode="cpu": Path(dst).write_bytes(b"cut"),
        combine=lambda files, output, log_fn=print: Path(output).write_bytes(b"draft") or output,
        caption_transcribe=transcribe_fresh,
        caption_burn=burn_fresh,
    )
    written = (fresh / "captions.md").read_text(encoding="utf-8")
    assert "hold your finish" in written
    assert seen["model"] == "medium"
    assert seen["cues"] == ["hold your finish"]


def test_main_py_runs_club_before_the_gui_imports():
    text = Path("main.py").read_text(encoding="utf-8")
    club = text.index('if "--club" in sys.argv[1:]:')
    qt = text.index("from PySide6")
    assert club < qt
    assert "from modules.club.montage import main as _club_main" in text


def test_brief_reads_must_include_and_include_windows():
    brief = parse_brief(
        "LENGTH: 20s\nSTYLE: talking\n"
        "MUST_INCLUDE:\n"
        "- lesson.mp4: \"hold your finish\"\n"
        "- we play at four\n"
        "INCLUDE_WINDOWS:\n"
        "lesson.mp4 0:12-0:18\n"
        "rally.mov 12.0-18.5\n"
    )
    assert brief.must_include == (
        "lesson.mp4: hold your finish",
        "we play at four",
    )
    assert brief.include_windows == (
        ("lesson.mp4", 12.0, 18.0),
        ("rally.mov", 12.0, 18.5),
    )
    assert brief.as_dict()["include_windows"][0]["start"] == 12.0
    with pytest.raises(BriefError):
        parse_brief("LENGTH: 10s\nSTYLE: talking\nINCLUDE_WINDOWS: nope\n")


def test_quote_maps_to_word_times_and_segment_times():
    from modules.club.pick import match_quote, pad_quote, render_transcript_md, transcript_payload
    files = [{
        "source": "lesson.mp4",
        "path": "/clips/lesson.mp4",
        "segments": [{
            "start": 1.0,
            "end": 2.4,
            "text": "Hold your finish.",
            "words": [
                {"start": 1.0, "end": 1.3, "text": "Hold"},
                {"start": 1.3, "end": 1.7, "text": "your"},
                {"start": 1.7, "end": 2.4, "text": "finish."},
            ],
        }],
    }, {
        "source": "other.mp4",
        "path": "/clips/other.mp4",
        "segments": [{
            "start": 5.0,
            "end": 8.0,
            "text": "we play at four on saturday",
        }],
    }]
    hit = match_quote("hold your finish", files)
    assert hit["source"] == "lesson.mp4"
    assert hit["start"] == 1.0
    assert hit["end"] == 2.4
    only = match_quote("other.mp4: play at four", files)
    assert only["source"] == "other.mp4"
    assert 5.0 <= only["start"] < only["end"] <= 8.0
    assert match_quote("not spoken", files) is None
    start, end = pad_quote(1.0, 2.4, 30.0)
    assert start < 1.0 and end > 2.4
    md = render_transcript_md(transcript_payload(files, "base"))
    assert "## lesson.mp4" in md
    assert "0:01.000" in md
    assert "hold" in md.casefold()


def test_forced_windows_fill_only_the_remaining_length():
    from modules.club.montage import select_with_forced
    forced = Window("a.mp4", "a.mp4", 10, 14, 0, 0, 1, 0, forced=True, include="quote")
    pool = [
        Window("a.mp4", "a.mp4", 0, 8, 0, 0, 0, 0, score=1),
        Window("a.mp4", "a.mp4", 10, 18, 0, 0, 0, 0, score=9),
        Window("a.mp4", "a.mp4", 20, 28, 0, 0, 0, 0, score=5),
    ]
    picked = select_with_forced(pool, [forced], 20, 20)
    assert picked[0].forced is True
    assert picked[0].include == "quote"
    assert all(not (w.start < 14 and w.end > 10 and not w.forced) for w in picked)
    assert sum(w.duration for w in picked) == pytest.approx(20, abs=0.05)
    assert select_with_forced(pool, [], 6, 10)[0].start == select_windows(pool, 6, 10)[0].start
    over = select_with_forced(pool, [Window("a.mp4", "a.mp4", 0, 12, 0, 0, 1, 0, forced=True)], 6, 8)
    assert len(over) == 1 and over[0].duration == 12


def test_pick_writes_a_transcript_and_does_not_assemble(tmp_path, monkeypatch):
    from modules.club.montage import run_transcript
    (tmp_path / "lesson.mp4").write_bytes(b"x")
    (tmp_path / "draft.mp4").write_bytes(b"stale")

    def transcribe(path, model, log_fn):
        assert model == "tiny"
        return {"segments": [{
            "start": 1.0,
            "end": 2.4,
            "text": "hold your finish",
            "words": [
                {"start": 1.0, "end": 1.3, "text": "hold"},
                {"start": 1.3, "end": 1.7, "text": "your"},
                {"start": 1.7, "end": 2.4, "text": "finish"},
            ],
        }]}

    result = run_transcript(
        tmp_path,
        whisper_model="tiny",
        probe=lambda _p: {"duration": 10},
        transcribe=transcribe,
        motion=lambda _path, duration: [0.0] * int(duration),
    )
    assert result["draft_written"] is False
    assert (tmp_path / "draft.mp4").read_bytes() == b"stale"
    assert not (tmp_path / "cuts.json").exists()
    timeline = json.loads(Path(result["timeline_json"]).read_text(encoding="utf-8"))
    assert timeline["whisper"] == "tiny"
    assert timeline["files"][0]["moments"][0]["text"] == "hold your finish"
    assert timeline["files"][0]["moments"][0]["in"] < 1.0
    assert timeline["files"][0]["moments"][0]["out"] > 2.4
    md = Path(result["transcript_md"]).read_text(encoding="utf-8")
    body = json.loads(Path(result["transcript_json"]).read_text(encoding="utf-8"))
    assert "hold your finish" in md
    assert "0:01.000–0:01.300 hold" in md
    assert body["files"][0]["segments"][0]["words"][2]["text"] == "finish"
    assert body["whisper"] == "tiny"

    seen = {}

    def fake(folder, **kwargs):
        seen["folder"] = str(folder)
        seen.update(kwargs)
        return {"files": [{"source": "lesson.mp4"}]}

    monkeypatch.setattr("modules.club.montage.run_transcript", fake)
    assert club_main(["pick", str(tmp_path), "--whisper-model", "tiny"]) == 0
    assert seen["whisper_model"] == "tiny"
    assert club_main([str(tmp_path), "--transcript-only"]) == 0
    assert club_main(["--club", "pick", str(tmp_path)]) == 0
    monkeypatch.setattr(
        "modules.club.montage.run_transcript",
        lambda *a, **k: {"files": []},
    )
    assert club_main(["--transcript-only", "--club", str(tmp_path)]) == 1


def test_highlights_are_forced_and_the_ranker_fills_the_rest(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 10s\nSTYLE: talking\nTITLE: Private lesson\n"
        "MUST_INCLUDE: \"hold your finish\"\n"
        "INCLUDE_WINDOWS:\n"
        "late.mp4 20-24\n",
        encoding="utf-8",
    )
    (tmp_path / "lesson.mp4").write_bytes(b"a")
    (tmp_path / "late.mp4").write_bytes(b"b")
    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "base",
        "files": [
            {
                "source": "lesson.mp4",
                "duration_s": 30,
                "segments": [
                    {
                        "start": 2.0,
                        "end": 3.2,
                        "text": "hold your finish",
                        "words": [
                            {"start": 2.0, "end": 2.4, "text": "hold"},
                            {"start": 2.4, "end": 2.8, "text": "your"},
                            {"start": 2.8, "end": 3.2, "text": "finish"},
                        ],
                    },
                    {
                        "start": 12.0,
                        "end": 20.0,
                        "text": "keep your head still and watch the ball",
                    },
                ],
            },
            {"source": "late.mp4", "duration_s": 30, "segments": []},
        ],
    }), encoding="utf-8")
    burned = {}

    def transcribe(*_args, **_kwargs):
        raise AssertionError("saved transcript should be reused")

    def cut(src, start, end, dst, mode="cpu"):
        Path(dst).write_bytes(f"{Path(src).name}:{start:.3f}-{end:.3f}".encode())

    def combine(files, output, log_fn=print):
        Path(output).write_bytes(b"joined")

    def caption_burn(src, dst, brief, duration, cues, log_fn):
        burned["title"] = brief.title
        burned["cues"] = list(cues)
        Path(dst).write_bytes(b"branded")

    cuts = run_club_montage(
        tmp_path,
        probe=lambda _p: {"duration": 30},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0.0] * 30,
        transcribe=transcribe,
        cut=cut,
        combine=combine,
        caption_transcribe=lambda *a, **k: {"engine": "test", "segments": []},
        caption_burn=caption_burn,
    )
    forced = [row for row in cuts["cuts"] if row.get("forced")]
    assert {row["source"] for row in forced} == {"lesson.mp4", "late.mp4"}
    late = next(row for row in forced if row["source"] == "late.mp4")
    assert (late["start"], late["end"]) == (20, 24)
    quote = next(row for row in forced if row["source"] == "lesson.mp4")
    assert quote["start"] < 2.0 < 3.2 < quote["end"]
    assert "hold your finish" in quote["include"]
    assert cuts["assembled_seconds"] == pytest.approx(10, abs=0.2)
    fillers = [row for row in cuts["cuts"] if not row.get("forced")]
    assert fillers
    assert all(row["speech"] >= 0.35 for row in fillers)
    assert all(row["source"] != "late.mp4" for row in fillers)
    assert cuts["draft_written"] is True
    assert burned["title"] == "Private lesson"
    assert (tmp_path / "draft.mp4").read_bytes() == b"branded"
    assert all(row["matched"] for row in cuts["includes"])


def test_a_missing_quote_is_not_invented_and_a_missing_file_stops(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 8s\nSTYLE: talking\nMUST_INCLUDE: \"not in the audio\"\n",
        encoding="utf-8",
    )
    (tmp_path / "lesson.mp4").write_bytes(b"a")

    def transcribe(path, model, log_fn):
        return [{"start": 0.0, "end": 2.0, "text": "we play at four"}]

    cuts = run_club_montage(
        tmp_path,
        dry_run=True,
        probe=lambda _p: {"duration": 16},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0.0] * 16,
        transcribe=transcribe,
    )
    assert cuts["includes"][0]["matched"] is False
    assert all(not row.get("forced") for row in cuts["cuts"])
    assert cuts["draft_written"] is False

    (tmp_path / "brief.md").write_text(
        "LENGTH: 8s\nSTYLE: talking\nINCLUDE_WINDOWS: missing.mp4 1-3\n",
        encoding="utf-8",
    )
    with pytest.raises(BriefError, match="missing.mp4"):
        run_club_montage(
            tmp_path,
            dry_run=True,
            probe=lambda _p: {"duration": 16},
            peaks=lambda _p: [],
            motion=lambda _p, _d: [0.0] * 16,
            use_whisper=False,
        )


def test_a_highlight_in_a_quiet_clip_is_kept(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 8s\nSTYLE: talking\nMUST_INCLUDE: \"hold your finish\"\n",
        encoding="utf-8",
    )
    (tmp_path / "lav.mp4").write_bytes(b"a")
    (tmp_path / "phone.mp4").write_bytes(b"b")
    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "base",
        "files": [
            {
                "source": "lav.mp4",
                "duration_s": 20,
                "segments": [{
                    "start": 0.0,
                    "end": 8.0,
                    "text": "keep your eye on the ball",
                }],
            },
            {
                "source": "phone.mp4",
                "duration_s": 20,
                "segments": [{
                    "start": 1.0,
                    "end": 2.0,
                    "text": "hold your finish",
                    "words": [
                        {"start": 1.0, "end": 1.3, "text": "hold"},
                        {"start": 1.3, "end": 1.6, "text": "your"},
                        {"start": 1.6, "end": 2.0, "text": "finish"},
                    ],
                }],
            },
        ],
    }), encoding="utf-8")

    def loudness(path):
        return -10.0 if path.endswith("lav.mp4") else -30.0

    cuts = run_club_montage(
        tmp_path,
        use_whisper=False,
        dry_run=True,
        probe=lambda _p: {"duration": 20},
        peaks=lambda _p: [],
        motion=lambda _p, _d: [0.0] * 20,
        loudness=loudness,
    )
    forced = [row for row in cuts["cuts"] if row.get("forced")]
    assert [row["source"] for row in forced] == ["phone.mp4"]
    fillers = [row for row in cuts["cuts"] if not row.get("forced")]
    assert fillers and {row["source"] for row in fillers} == {"lav.mp4"}
    assert cuts["mic_preference"]["soft"] == ["phone.mp4"]
    assert cuts["mic_preference"]["dropped"] == []


def test_talking_title_is_several_large_lines_and_captions_wait(tmp_path):
    """The name sits on its own line. Captions do not burn during the title."""
    from modules.club.brand import resolve_accent_font, talking_title_lines
    from modules.club.captions import caption_cues, talking_windows

    lines = talking_title_lines(
        "Private lesson with Coach John Wang", "John Wang",
    )
    assert lines == [
        [("Private lesson", False)],
        [("with Coach", False)],
        [("John Wang", True)],
    ]
    assert 2 <= len(lines) <= 3

    pacifico = tmp_path / "Pacifico-Regular.ttf"
    dancing = tmp_path / "DancingScript-Regular.ttf"
    vibes = tmp_path / "GreatVibes-Regular.ttf"
    pacifico.write_bytes(b"p")
    dancing.write_bytes(b"d")
    vibes.write_bytes(b"g")
    assert resolve_accent_font(search_dirs=[tmp_path]) == str(pacifico.resolve())
    dancing_only = tmp_path / "only"
    dancing_only.mkdir()
    (dancing_only / "DancingScript-Regular.ttf").write_bytes(b"d")
    (dancing_only / "GreatVibes-Regular.ttf").write_bytes(b"g")
    assert resolve_accent_font(search_dirs=[dancing_only]).endswith(
        "DancingScript-Regular.ttf"
    )

    windows = talking_windows(15, cta=False)
    title_end = windows["title"][1]
    assert title_end == pytest.approx(2.5)
    assert windows["captions"][0] == pytest.approx(title_end)
    cues = caption_cues(
        [
            {"start": 0.2, "end": 2.2, "text": "under the title"},
            {"start": 2.6, "end": 4.2, "text": "after the title"},
            {
                "start": 1.0,
                "end": 3.5,
                "text": "spans the boundary",
                "words": [
                    {"start": 1.0, "end": 1.8, "text": "spans"},
                    {"start": 2.6, "end": 3.5, "text": "after"},
                ],
            },
        ],
        body_start=windows["captions"][0],
        body_end=windows["captions"][1],
    )
    assert cues
    assert all(cue.start >= title_end - 1e-6 for cue in cues)
    assert all("under the title" not in cue.text for cue in cues)
    assert any(cue.text == "after the title" for cue in cues)
    assert any(cue.text == "after" for cue in cues)


def test_talking_opens_on_a_must_include_not_a_silent_filler():
    """Filename order must not put a still, silent clip under the title."""
    filler = Window(
        "IMG_7227.MOV", "IMG_7227.MOV", 0, 6.34,
        0, 0, 0, 0, score=0.2,
    )
    first_must = Window(
        "IMG_7230.MOV", "IMG_7230.MOV", 1.0, 5.0,
        0.2, 0.1, 1, 0, score=1,
        forced=True, include="hold your finish", kind="must_include",
    )
    later_must = Window(
        "IMG_7244.MOV", "IMG_7244.MOV", 2.0, 6.0,
        0.2, 0.1, 1, 0, score=1,
        forced=True, include="we play at four", kind="must_include",
    )
    ordered = playback_order([filler, later_must, first_must], "talking")
    # The still ranks at the bottom, after both talking must-includes.
    assert [window.source for window in ordered] == [
        "IMG_7230.MOV", "IMG_7244.MOV", "IMG_7227.MOV",
    ]
    assert ordered[0].forced is True
    assert ordered[0].kind == "must_include"

    dead = Window("a.mp4", "a.mp4", 0, 4, 0, 0, 0, 0, score=1)
    spoken = Window("b.mp4", "b.mp4", 0, 4, 0.1, 0.0, 0.8, 0, score=2)
    assert [window.source for window in playback_order([dead, spoken], "talking")] == [
        "b.mp4", "a.mp4",
    ]
    # Two silent stills stay in clip order. There is no talking window to lead.
    other = Window("c.mp4", "c.mp4", 0, 4, 0, 0, 0, 0, score=3)
    assert [window.source for window in playback_order([other, dead], "talking")] == [
        "a.mp4", "c.mp4",
    ]


def test_every_kept_include_is_in_the_final_cut_or_the_run_stops():
    from modules.club.montage import (
        assert_forced_windows_kept,
        assert_includes_in_cuts,
        select_with_forced,
    )

    forced = [
        Window(
            f"clip{index}.mp4", f"clip{index}.mp4", 0, 3,
            0, 0, 1, 0, forced=True, include=f"line {index}", kind="must_include",
        )
        for index in range(4)
    ]
    pool = [Window("filler.mp4", "filler.mp4", 0, 8, 0, 0, 0, 0, score=9)]
    picked = select_with_forced(pool, forced, 8, 8)
    assert [window.include for window in picked if window.forced] == [
        "line 0", "line 1", "line 2", "line 3",
    ]
    assert_forced_windows_kept(forced, picked)
    rows = [
        {
            "kind": "must_include",
            "text": window.include,
            "matched": True,
            "kept": True,
            "source": window.source,
            "start": window.start,
            "end": window.end,
        }
        for window in forced
    ]
    assert_includes_in_cuts(rows, picked)
    # An unmatched quote is listed and does not fail the run.
    assert_includes_in_cuts(
        [{"kind": "must_include", "text": "not spoken", "matched": False, "kept": False}],
        [],
    )
    dropped = [window for window in picked if window.source != "clip2.mp4"]
    with pytest.raises(BriefError, match="missing from the final cut"):
        assert_includes_in_cuts(rows, dropped)
    with pytest.raises(BriefError, match="missing from the final cut"):
        assert_forced_windows_kept(forced, dropped)


def test_assemble_opens_on_the_must_include_and_keeps_every_one(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 12s\nSTYLE: talking\n"
        "TITLE: Private lesson with Coach John Wang\n"
        "TITLE_ACCENT: John Wang\n"
        "FONT: Inter\n"
        "MUST_INCLUDE:\n"
        "- IMG_7230.MOV: \"hold your finish\"\n"
        "- IMG_7244.MOV: \"we play at four\"\n",
        encoding="utf-8",
    )
    for name in ("IMG_7227.MOV", "IMG_7230.MOV", "IMG_7244.MOV"):
        (tmp_path / name).write_bytes(b"x")

    def words(start, tokens):
        return [
            {"start": start + index * 0.4, "end": start + (index + 1) * 0.4, "text": token}
            for index, token in enumerate(tokens)
        ]

    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "small",
        "files": [
            {"source": "IMG_7227.MOV", "duration_s": 8, "segments": []},
            {
                "source": "IMG_7230.MOV",
                "duration_s": 20,
                "segments": [{
                    "start": 1.0,
                    "end": 2.2,
                    "text": "hold your finish",
                    "words": words(1.0, ["hold", "your", "finish"]),
                }],
            },
            {
                "source": "IMG_7244.MOV",
                "duration_s": 20,
                "segments": [{
                    "start": 3.0,
                    "end": 4.6,
                    "text": "we play at four",
                    "words": words(3.0, ["we", "play", "at", "four"]),
                }],
            },
        ],
    }), encoding="utf-8")

    def probe(path):
        return {"duration": 8.0 if str(path).endswith("7227.MOV") else 20.0}

    def motion(path, duration):
        if str(path).endswith("7227.MOV"):
            return [0.0] * max(1, int(duration))
        return [0.2] * max(1, int(duration))

    cuts = run_club_montage(
        tmp_path,
        dry_run=True,
        use_whisper=False,
        probe=probe,
        peaks=lambda _p: [],
        motion=motion,
    )
    assert cuts["cuts"][0]["source"] == "IMG_7230.MOV"
    assert cuts["cuts"][0]["play_index"] == 0
    assert cuts["cuts"][0].get("forced") is True
    assert cuts["cuts"][0].get("kind") == "must_include"
    forced = [row for row in cuts["cuts"] if row.get("forced")]
    assert {row["source"] for row in forced} == {"IMG_7230.MOV", "IMG_7244.MOV"}
    assert all(row["matched"] and row["kept"] for row in cuts["includes"])
    filler = [row for row in cuts["cuts"] if row["source"] == "IMG_7227.MOV"]
    assert not filler or filler[0]["play_index"] > 0
    assert cuts["brand"]["cta"] == ""


def test_brief_reads_title_under_and_open_scene():
    named = parse_brief(
        "LENGTH: 20s\nSTYLE: talking\n"
        "TITLE_UNDER: taller Asian guy pink shirt\n"
    )
    assert named.title_under == "taller Asian guy pink shirt"
    assert named.as_dict()["title_under"] == "taller Asian guy pink shirt"
    alias = parse_brief("LENGTH: 20s\nSTYLE: talking\nOPEN_SCENE: John\n")
    assert alias.title_under == "John"
    both = parse_brief(
        "LENGTH: 20s\nSTYLE: talking\n"
        "TITLE_UNDER: John\n"
        "OPEN_SCENE: someone else\n"
    )
    assert both.title_under == "John"
    absent = parse_brief("LENGTH: 20s\nSTYLE: talking\n")
    assert absent.title_under == ""


def test_talking_gap_fills_with_speech_not_a_silent_pause():
    """Must-includes stay in. The open length is talking, not the still between them."""
    from modules.club.montage import select_with_forced

    forced = [
        Window(
            "a.mp4", "a.mp4", 1.0, 3.0, 0, 0.2, 1, 0,
            forced=True, include="hold your finish", kind="must_include",
        ),
        Window(
            "c.mp4", "c.mp4", 2.0, 4.5, 0, 0.2, 1, 0,
            forced=True, include="we play at four", kind="must_include",
        ),
    ]
    silent = Window("b.mp4", "b.mp4", 0, 8, 0.95, 0.0, 0.0, 0, score=5)
    low_motion = Window("c.mp4", "c.mp4", 12, 20, 0.4, 0.01, 0.05, 0, score=4)
    talking = Window("a.mp4", "a.mp4", 10, 18, 0.1, 0.7, 0.92, 0, score=1)
    picked = select_with_forced(
        [silent, low_motion, talking], forced, 12, 12, style="talking",
    )
    assert [window.include for window in picked if window.forced] == [
        "hold your finish", "we play at four",
    ]
    fillers = [window for window in picked if not window.forced]
    assert fillers
    assert {window.source for window in fillers} == {"a.mp4"}
    assert all(window.speech >= 0.35 for window in fillers)
    assert sum(window.duration for window in picked) == pytest.approx(12, abs=0.05)
    # Hype still may use a loud window. Talking is the path that refuses the pause.
    hype = select_with_forced([silent], forced, 12, 12, style="hype")
    assert any(window.source == "b.mp4" for window in hype)


def test_speech_beside_a_must_include_fills_the_open_length():
    """A highlight that sits inside a long quiet window does not hide the talking next to it."""
    from modules.club.montage import select_with_forced

    forced = [
        Window(
            "lesson.mp4", "lesson.mp4", 0.5, 2.2, 0, 0.1, 1, 0,
            forced=True, include="hold your finish", kind="must_include",
        ),
    ]
    wide = Window("lesson.mp4", "lesson.mp4", 0, 8, 0.0, 0.4, 0.15, 0, score=0.8)
    silent = Window("still.mp4", "still.mp4", 0, 8, 0.8, 0.0, 0.0, 0, score=2)
    segments = {
        "lesson.mp4": [
            {"start": 0.8, "end": 2.0, "text": "hold your finish"},
            {"start": 3.0, "end": 7.5, "text": "watch the ball and finish tall"},
        ],
    }
    picked = select_with_forced(
        [wide, silent], forced, 8, 8,
        style="talking",
        segments_by_source=segments,
        durations={"lesson.mp4": 12, "still.mp4": 8},
    )
    fillers = [window for window in picked if not window.forced]
    assert len(picked) >= 2
    assert picked[0].forced and picked[0].include == "hold your finish"
    assert fillers
    assert all(window.source == "lesson.mp4" for window in fillers)
    assert all(window.start >= 2.2 - 0.05 for window in fillers)
    assert all(window.speech >= 0.35 for window in fillers)
    assert not any(window.source == "still.mp4" for window in picked)


def test_digit_spam_is_not_speech_and_silence_cannot_fill_the_gap():
    """Whisper digit spam must not become speech=1, and room tone must not mid-fill.

    IMG_7227 is the failure: one junk segment covers the file, speech
    coverage becomes 1, motion is high, and the trimmed window lands
    between must-includes. Real speech after a must-include takes that
    time instead.
    """
    from modules.club.montage import is_real_speech_text, is_talking_fill, speech_coverage

    spam = "1,5,5,5,5,5,5,5,5,5"
    assert is_real_speech_text(spam) is False
    assert is_real_speech_text("1, 5, 5, 5, 5, 5") is False
    assert is_real_speech_text("") is False
    assert is_real_speech_text("...") is False
    assert is_real_speech_text("um uh") is False
    assert is_real_speech_text("hold your finish") is True
    assert is_real_speech_text("ok") is True
    assert is_real_speech_text("we play at 4") is True
    segments = [{"start": 0.0, "end": 8.6, "text": spam}]
    assert speech_coverage(segments, 0.0, 2.96) == 0.0
    assert speech_coverage(segments, 0.0, 8.6) == 0.0
    scored = score_windows_for_clip(
        source="IMG_7227.MOV", path="IMG_7227.MOV", duration=8.6, style="talking",
        keywords=(), peak_times=[0.4, 1.2],
        motion_series=[0.2, 0.9, 0.4, 0.8, 0.3, 0.7, 0.5, 0.6, 0.4],
        segments=segments,
    )
    assert scored
    assert all(window.speech == 0.0 for window in scored)
    assert all(window.motion > 0.15 for window in scored)
    assert all(window.score < 1 for window in scored)

    # Loudness gate, even when a caller still stamps the junk window speech=1.
    fake = Window(
        "IMG_7227.MOV", "IMG_7227.MOV", 0, 8, 0.12, 0.66, 1.0, 0,
        score=4.675, audible=False,
    )
    assert is_silent_still(fake)
    assert is_talking_fill(fake) is False
    from modules.club.montage import select_with_forced
    must = Window(
        "IMG_7244.MOV", "IMG_7244.MOV", 0.75, 2.85, 0.2, 0.3, 1, 0,
        forced=True, include="we play at four", kind="must_include",
    )
    other = Window(
        "IMG_7230.MOV", "IMG_7230.MOV", 0.75, 4.2, 0.2, 0.3, 1, 0,
        forced=True, include="hold your finish", kind="must_include",
    )
    picked = select_with_forced(
        [fake], [must, other], 18, 18, style="talking",
        segments_by_source={
            "IMG_7244.MOV": [
                {"start": 1.0, "end": 2.6, "text": "we play at four"},
                {"start": 3.0, "end": 7.5, "text": "keep your eye on the ball John"},
            ],
        },
        durations={"IMG_7244.MOV": 20, "IMG_7227.MOV": 8.6, "IMG_7230.MOV": 20},
    )
    assert all(window.source != "IMG_7227.MOV" for window in picked)
    grown = next(window for window in picked if window.source == "IMG_7244.MOV")
    assert grown.kind == "must_include"
    assert grown.end >= 7.0
    assert any(window.source == "IMG_7230.MOV" and window.forced for window in picked)


def test_title_under_slash_matches_one_alternative():
    """A slash is alternatives. A phrase with no slash stays one phrase."""
    john = Window(
        "b.mp4", "b.mp4", 2, 6, 0.2, 0.2, 1, 0,
        forced=True, include="we play at four", kind="must_include",
        heard="we play at four with John",
    )
    other = Window(
        "a.mp4", "a.mp4", 1, 4, 0.2, 0.2, 1, 0,
        forced=True, include="hold your finish", kind="must_include",
        heard="hold your finish",
    )
    named = playback_order([john, other], "talking", scene="John / pink shirt")
    assert named[0].source == "b.mp4"
    # "John" does not match inside "Johnson".
    johnson = Window(
        "j.mp4", "j.mp4", 0, 4, 0.2, 0.2, 1, 0,
        forced=True, include="Johnson said finish", kind="must_include",
        heard="Johnson said finish",
    )
    assert playback_order(
        [johnson, other], "talking", scene="John / pink shirt",
    )[0].source == "a.mp4"
    phrase = playback_order(
        [
            other,
            Window(
                "c.mp4", "c.mp4", 0, 4, 0.1, 0.2, 0.5, 0,
                heard="the taller Asian guy pink shirt is talking",
            ),
            Window(
                "d.mp4", "d.mp4", 0, 4, 0.1, 0.4, 1.0, 0,
                heard="a pink ball",
            ),
        ],
        "talking",
        scene="taller Asian guy pink shirt",
    )
    assert phrase[0].source == "c.mp4"


def test_title_under_opens_on_that_person_not_a_silent_match():
    other = Window(
        "a.mp4", "a.mp4", 1, 4, 0.2, 0.2, 1, 0,
        forced=True, include="hold your finish", kind="must_include",
        heard="hold your finish",
    )
    john = Window(
        "b.mp4", "b.mp4", 2, 5, 0.2, 0.2, 1, 0,
        forced=True, include="we play at four", kind="must_include",
        heard="we play at four with John",
    )
    silent = Window(
        "john-still.mp4", "john-still.mp4", 0, 4, 0, 0, 0, 0,
        score=9, heard="John is standing still",
    )
    plain = playback_order([john, silent, other], "talking")
    assert plain[0].source == "a.mp4"
    named = playback_order([john, silent, other], "talking", scene="John")
    assert named[0].source == "b.mp4"
    assert named[0].kind == "must_include"
    # The still names John and sorts first. It does not take the title.
    without_john_line = playback_order([silent, other], "talking", scene="John")
    assert without_john_line[0].source == "a.mp4"
    described = playback_order(
        [
            other,
            Window(
                "c.mp4", "c.mp4", 0, 4, 0.1, 0.4, 0.8, 0,
                heard="the taller Asian guy pink shirt is talking",
            ),
        ],
        "talking",
        scene="taller Asian guy pink shirt",
    )
    assert described[0].source == "c.mp4"


def test_assemble_fills_the_gap_and_opens_on_title_under(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 12s\nSTYLE: talking\n"
        "TITLE: Private lesson with Coach John Wang\n"
        "TITLE_ACCENT: John Wang\n"
        "TITLE_UNDER: John\n"
        "MUST_INCLUDE:\n"
        "- a.mp4: \"hold your finish\"\n"
        "- c.mp4: \"we play at four\"\n",
        encoding="utf-8",
    )
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "small",
        "files": [
            {
                "source": "a.mp4",
                "duration_s": 20,
                "segments": [
                    {"start": 1.0, "end": 2.2, "text": "hold your finish"},
                    {"start": 8.0, "end": 16.0, "text": "keep your eye on the ball and finish"},
                ],
            },
            {"source": "b.mp4", "duration_s": 8, "segments": []},
            {
                "source": "c.mp4",
                "duration_s": 20,
                "segments": [{
                    "start": 3.0,
                    "end": 5.0,
                    "text": "we play at four with John",
                }],
            },
        ],
    }), encoding="utf-8")

    def motion(path, duration):
        if str(path).endswith("b.mp4"):
            return [0.0] * max(1, int(duration))
        return [0.6] * max(1, int(duration))

    cuts = run_club_montage(
        tmp_path,
        dry_run=True,
        use_whisper=False,
        probe=lambda _p: {"duration": 8.0 if str(_p).endswith("b.mp4") else 20.0},
        peaks=lambda _p: [1, 2, 3, 4, 5, 6],
        motion=motion,
    )
    assert cuts["title_under"] == "John"
    assert cuts["cuts"][0]["source"] == "c.mp4"
    assert cuts["cuts"][0].get("kind") == "must_include"
    forced = [row for row in cuts["cuts"] if row.get("forced")]
    assert {row["source"] for row in forced} == {"a.mp4", "c.mp4"}
    assert all(row["matched"] and row["kept"] for row in cuts["includes"])
    assert not any(row["source"] == "b.mp4" for row in cuts["cuts"])
    fillers = [row for row in cuts["cuts"] if not row.get("forced")]
    assert fillers
    assert all(row["speech"] >= 0.35 for row in fillers)
    assert all(not is_silent_still(Window(
        row["source"], row["source"], row["start"], row["end"],
        row["audio"], row["motion"], row["speech"], row["keyword"],
    )) for row in fillers)
    # "hold your finish" keeps two seconds after the line. That time stays
    # inside LENGTH: the draft does not run long to make room for the beat.
    assert cuts["assembled_seconds"] >= 11
    assert cuts["assembled_seconds"] <= cuts["length_max_s"] + 0.05
    hold = next(row for row in forced if row["source"] == "a.mp4")
    assert hold["end"] >= 4.0


def test_hallucinated_silence_does_not_fill_after_the_title(tmp_path):
    """IMG_7227 digit spam must not score speech=1 or play between must-includes.

    The file is room tone with motion. Whisper covers it with one junk
    line. LENGTH still needs time, so the must-include that is actually
    talking grows along that speech, and 7227 stays out of the cut.
    """
    (tmp_path / "brief.md").write_text(
        "LENGTH: 18s\nSTYLE: talking\n"
        "TITLE: Private lesson with Coach John Wang\n"
        "TITLE_ACCENT: John Wang\n"
        "TITLE_UNDER: John / pink shirt\n"
        "MUST_INCLUDE:\n"
        "- IMG_7230.MOV: \"hold your finish\"\n"
        "- IMG_7244.MOV: \"we play at four\"\n",
        encoding="utf-8",
    )
    for name in ("IMG_7227.MOV", "IMG_7230.MOV", "IMG_7244.MOV"):
        (tmp_path / name).write_bytes(b"x")

    def words(start, tokens):
        return [
            {"start": start + index * 0.4, "end": start + (index + 1) * 0.4, "text": token}
            for index, token in enumerate(tokens)
        ]

    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "small",
        "files": [
            {
                "source": "IMG_7227.MOV",
                "duration_s": 8.6,
                "segments": [{
                    "start": 0.0,
                    "end": 8.6,
                    "text": "1,5,5,5,5,5,5,5,5,5",
                }],
            },
            {
                "source": "IMG_7230.MOV",
                "duration_s": 20,
                "segments": [{
                    "start": 1.0,
                    "end": 2.2,
                    "text": "hold your finish",
                    "words": words(1.0, ["hold", "your", "finish"]),
                }],
            },
            {
                "source": "IMG_7244.MOV",
                "duration_s": 20,
                "segments": [
                    {
                        "start": 1.0,
                        "end": 2.6,
                        "text": "we play at four",
                        "words": words(1.0, ["we", "play", "at", "four"]),
                    },
                    {
                        "start": 3.0,
                        "end": 7.5,
                        "text": "keep your eye on the ball John",
                    },
                ],
            },
        ],
    }), encoding="utf-8")

    def probe(path):
        return {"duration": 8.6 if str(path).endswith("7227.MOV") else 20.0}

    def motion(path, duration):
        n = max(1, int(duration))
        if str(path).endswith("7227.MOV"):
            return [0.2, 0.9, 0.4, 0.8, 0.3, 0.7, 0.5, 0.6, 0.4][:n] or [0.8]
        return [0.4] * n

    def loudness(path):
        return -56.0 if str(path).endswith("7227.MOV") else -18.0

    cuts = run_club_montage(
        tmp_path,
        dry_run=True,
        use_whisper=False,
        probe=probe,
        peaks=lambda _p: [0.4, 1.2, 2.0],
        motion=motion,
        loudness=loudness,
    )
    assert cuts["title_under"] == "John / pink shirt"
    assert cuts["cuts"][0]["source"] == "IMG_7244.MOV"
    assert cuts["cuts"][0].get("kind") == "must_include"
    assert cuts["cuts"][0]["end"] >= 7.0
    sources = [row["source"] for row in cuts["cuts"]]
    assert "IMG_7227.MOV" not in sources
    assert "IMG_7230.MOV" in sources
    assert sources.index("IMG_7244.MOV") < sources.index("IMG_7230.MOV")
    scored = json.loads((tmp_path / "scores.json").read_text(encoding="utf-8"))
    spam = [row for row in scored["windows"] if row["source"] == "IMG_7227.MOV"]
    assert spam
    assert all(row["speech"] == 0.0 for row in spam)
    assert all(row["score"] < 1 for row in spam)
    assert all(not row["selected"] for row in spam)
    assert all(row["matched"] and row["kept"] for row in cuts["includes"])


def test_silent_stills_do_not_fill_the_middle_when_talking_or_action_exists():
    """A short silent clip must not follow the title just because it fits."""
    from modules.club.montage import _talking_pack_rank

    talking = Window("talk.mp4", "talk.mp4", 0, 6, 0.1, 0.2, 0.9, 0, score=2)
    action = Window("move.mp4", "move.mp4", 0, 8, 0.2, 0.8, 0.1, 0, score=1)
    dead = Window("still.mp4", "still.mp4", 0, 4, 0.95, 0.0, 0.0, 0, score=9)
    picked = select_windows(
        [dead, action, talking], 6, 12, target=12,
        rank=_talking_pack_rank, prefer_alive=True,
    )
    assert [window.source for window in picked][0] == "talk.mp4"
    assert all(window.source != "still.mp4" for window in picked)
    assert any(window.source == "move.mp4" for window in picked)
    # Nothing alive left: the still may fill, and it sorts last.
    only_dead = select_windows(
        [dead, Window("other.mp4", "other.mp4", 0, 4, 0.2, 0.02, 0.0, 0, score=1)],
        6, 8, rank=_talking_pack_rank, prefer_alive=True,
    )
    assert only_dead
    assert all(is_silent_still(window) for window in only_dead)
    ordered = playback_order([dead, talking, action], "talking")
    assert ordered[-1].source == "still.mp4"
    assert [window.source for window in ordered if not is_silent_still(window)] == [
        "move.mp4", "talk.mp4",
    ]


def test_check_your_feet_and_pause_hold_the_beat():
    from modules.club.pick import extend_coaching_window

    feet = [
        {"start": 1.0, "end": 2.4, "text": "check your feet", "words": [
            {"start": 1.0, "end": 1.4, "text": "check"},
            {"start": 1.4, "end": 1.7, "text": "your"},
            {"start": 1.7, "end": 2.4, "text": "feet"},
        ]},
    ]
    start, end = extend_coaching_window(0.75, 2.65, 20.0, feet)
    assert start == pytest.approx(0.75)
    assert end == pytest.approx(4.4, abs=0.05)

    already = extend_coaching_window(0.75, 6.0, 20.0, feet)
    assert already == (0.75, 6.0)

    pause = [{"start": 3.0, "end": 4.2, "text": "pause there"}]
    _start, pause_end = extend_coaching_window(3.0, 4.3, 20.0, pause)
    assert pause_end == pytest.approx(6.2, abs=0.05)

    hold = [{"start": 2.0, "end": 3.2, "text": "hold your finish"}]
    _start, hold_end = extend_coaching_window(1.75, 3.45, 30.0, hold)
    assert hold_end == pytest.approx(5.2, abs=0.05)

    clamped = extend_coaching_window(0.75, 2.65, 3.0, feet)
    assert clamped[1] == pytest.approx(3.0)

    plain = [{"start": 1.0, "end": 2.0, "text": "we play at four"}]
    assert extend_coaching_window(0.75, 2.25, 20.0, plain) == (0.75, 2.25)


def test_setup_lines_hold_the_beat_like_pause_hold_and_feet():
    """Foreshadow gets the same two seconds. An ordinary line does not."""
    from modules.club.pick import extend_coaching_window

    def cue(text, start=1.0, end=2.4):
        return [{"start": start, "end": end, "text": text}]

    for text in (
        "watch this",
        "here we go",
        "show me",
        "get ready",
        "your turn",
        "one more",
        "and finish",
        "let's see",
        "right there",
        "follow through",
        "set up",
        "wait there",
        "stay",
        "freeze",
        "try again",
        "don't move",
        "balance",
    ):
        _start, end = extend_coaching_window(0.75, 2.65, 20.0, cue(text))
        assert end == pytest.approx(4.4, abs=0.05), text

    # The words are not brief KEYWORDS. A line with none of the cues stays put.
    plain = [{"start": 1.0, "end": 2.0, "text": "we play at four on saturday"}]
    assert extend_coaching_window(0.75, 2.25, 20.0, plain) == (0.75, 2.25)


def test_talking_extends_a_setup_line_with_no_keywords(tmp_path):
    """Every talking clip holds the beat. KEYWORDS are not required."""
    (tmp_path / "brief.md").write_text(
        "LENGTH: 8s\nSTYLE: talking\nTITLE: Lesson\n"
        "MUST_INCLUDE: \"watch this\"\n",
        encoding="utf-8",
    )
    (tmp_path / "coach.mp4").write_bytes(b"a")
    (tmp_path / "still.mp4").write_bytes(b"b")
    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "small",
        "files": [
            {
                "source": "coach.mp4",
                "duration_s": 20,
                "segments": [
                    {"start": 1.0, "end": 1.8, "text": "watch this"},
                    {"start": 8.0, "end": 8.8, "text": "here we go"},
                ],
            },
            {
                "source": "still.mp4",
                "duration_s": 8,
                "segments": [{
                    "start": 0.0,
                    "end": 8.0,
                    "text": "1,5,5,5,5,5,5,5,5,5",
                }],
            },
        ],
    }), encoding="utf-8")

    def motion(path, duration):
        n = max(1, int(duration))
        if str(path).endswith("still.mp4"):
            return [0.8] * n
        return [0.4] * n

    def loudness(path):
        return -56.0 if str(path).endswith("still.mp4") else -18.0

    cuts = run_club_montage(
        tmp_path,
        dry_run=True,
        use_whisper=False,
        probe=lambda path: {"duration": 8.0 if str(path).endswith("still.mp4") else 20.0},
        peaks=lambda _p: [1, 2, 3],
        motion=motion,
        loudness=loudness,
    )
    cue = next(row for row in cuts["cuts"] if row.get("forced"))
    assert cue["source"] == "coach.mp4"
    assert "watch this" in cue["include"]
    # Quote ends at 1.8s. The beat holds two seconds past that line.
    assert cue["end"] == pytest.approx(3.8, abs=0.15)
    follow = [
        row for row in cuts["cuts"]
        if row["source"] == "coach.mp4" and 7 <= row["start"] <= 9
    ]
    assert len(follow) == 1
    assert follow[0]["end"] == pytest.approx(10.8, abs=0.35)
    assert all(row["source"] != "still.mp4" for row in cuts["cuts"])
    assert "KEYWORDS" not in (tmp_path / "brief.md").read_text(encoding="utf-8")


def test_assemble_holds_check_your_feet_and_skips_the_silent_follow(tmp_path):
    (tmp_path / "brief.md").write_text(
        "LENGTH: 10s\nSTYLE: talking\n"
        "TITLE: Private lesson\n"
        "MUST_INCLUDE: \"check your feet\"\n",
        encoding="utf-8",
    )
    (tmp_path / "coach.mp4").write_bytes(b"a")
    (tmp_path / "still.mp4").write_bytes(b"b")
    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "small",
        "files": [
            {
                "source": "coach.mp4",
                "duration_s": 20,
                "segments": [
                    {
                        "start": 1.0,
                        "end": 2.4,
                        "text": "check your feet",
                        "words": [
                            {"start": 1.0, "end": 1.4, "text": "check"},
                            {"start": 1.4, "end": 1.7, "text": "your"},
                            {"start": 1.7, "end": 2.4, "text": "feet"},
                        ],
                    },
                    {"start": 8.0, "end": 14.0, "text": "now watch the ball all the way"},
                ],
            },
            {"source": "still.mp4", "duration_s": 8, "segments": []},
        ],
    }), encoding="utf-8")

    def motion(path, duration):
        if str(path).endswith("still.mp4"):
            return [0.0] * max(1, int(duration))
        return [0.5] * max(1, int(duration))

    cuts = run_club_montage(
        tmp_path,
        dry_run=True,
        use_whisper=False,
        probe=lambda path: {"duration": 8.0 if str(path).endswith("still.mp4") else 20.0},
        peaks=lambda _p: [1, 2, 3],
        motion=motion,
    )
    assert not any(row["source"] == "still.mp4" for row in cuts["cuts"])
    cue = next(row for row in cuts["cuts"] if row.get("forced"))
    assert cue["source"] == "coach.mp4"
    assert cue["end"] == pytest.approx(4.4, abs=0.15)
    assert "check your feet" in cue["include"]
    fillers = [row for row in cuts["cuts"] if not row.get("forced")]
    assert fillers
    assert all(row["speech"] >= 0.35 or row["motion"] >= 0.15 for row in fillers)
    assert all(row["matched"] and row["kept"] for row in cuts["includes"])
    assert cuts["assembled_seconds"] <= cuts["length_max_s"] + 0.05


def test_setup_hold_trims_fill_instead_of_passing_length():
    """A pause, hold, check-feet, or foreshadow beat stays inside LENGTH.

    The two seconds are kept when another filler can be tightened to make
    room. When the hold itself is what would run past the brief, it is not
    added on top of a cut that is already at LENGTH.
    """
    from modules.club.montage import extend_coaching_beats

    def cue_window(source, start, end, *, forced=False):
        return Window(
            source, source, start, end, 0.2, 0.3, 0.9, 0,
            forced=forced, kind="must_include" if forced else "",
            include="line" if forced else "",
        )

    watch = [{"start": 3.4, "end": 5.0, "text": "now watch this"}]
    hold = cue_window("a.mp4", 0.0, 5.0, forced=True)
    plain = cue_window("b.mp4", 0.0, 10.0)
    fitted = extend_coaching_beats(
        [hold, plain],
        {"a.mp4": watch, "b.mp4": []},
        {"a.mp4": 30.0, "b.mp4": 30.0},
        log_fn=lambda *_a, **_k: None,
        max_s=15.0,
    )
    by_source = {window.source: window for window in fitted}
    assert by_source["a.mp4"].end == pytest.approx(7.0, abs=0.05)
    assert by_source["b.mp4"].end == pytest.approx(8.0, abs=0.05)
    assert sum(window.duration for window in fitted) == pytest.approx(15.0, abs=0.05)

    # No other filler to tighten: the foreshadow hold is not stacked on LENGTH.
    alone = cue_window("a.mp4", 0.0, 15.0, forced=True)
    finish = [{"start": 13.5, "end": 15.0, "text": "and finish"}]
    capped = extend_coaching_beats(
        [alone],
        {"a.mp4": finish},
        {"a.mp4": 30.0},
        log_fn=lambda *_a, **_k: None,
        max_s=15.0,
    )
    assert len(capped) == 1
    assert capped[0].end == pytest.approx(15.0, abs=0.05)
    assert capped[0].duration == pytest.approx(15.0, abs=0.05)

    # Room under LENGTH: the beat is still the full two seconds.
    short = cue_window("a.mp4", 0.0, 5.0, forced=True)
    room = extend_coaching_beats(
        [short],
        {"a.mp4": watch},
        {"a.mp4": 30.0},
        log_fn=lambda *_a, **_k: None,
        max_s=15.0,
    )
    assert room[0].end == pytest.approx(7.0, abs=0.05)


def test_fifteen_second_talking_cut_stays_on_the_brief(tmp_path):
    """A setup line at the out-point must not turn LENGTH 15s into ~17s."""
    (tmp_path / "brief.md").write_text(
        "LENGTH: 15s\nSTYLE: talking\n"
        "TITLE: Private lesson\n"
        "MUST_INCLUDE: \"we play at four\"\n",
        encoding="utf-8",
    )
    (tmp_path / "coach.mp4").write_bytes(b"a")
    (tmp_path / "transcript.json").write_text(json.dumps({
        "whisper": "small",
        "files": [{
            "source": "coach.mp4",
            "duration_s": 30,
            "segments": [
                {"start": 1.0, "end": 2.2, "text": "we play at four"},
                {
                    "start": 2.5,
                    "end": 16.0,
                    "text": "keep your eye on the ball and watch this",
                },
            ],
        }],
    }), encoding="utf-8")

    cuts = run_club_montage(
        tmp_path,
        dry_run=True,
        use_whisper=False,
        probe=lambda _p: {"duration": 30.0},
        peaks=lambda _p: [1, 2, 3, 4],
        motion=lambda _p, duration: [0.4] * max(1, int(duration)),
    )
    assert cuts["length_max_s"] == 15
    assert cuts["assembled_seconds"] == pytest.approx(15, abs=0.15)
    assert cuts["assembled_seconds"] < 16
    assert cuts["within_brief"] is True
    assert cuts["cuts"]
    assert any(row.get("forced") for row in cuts["cuts"])
    assert all(row["matched"] and row["kept"] for row in cuts["includes"])


def test_captions_start_the_instant_the_title_clears():
    """Opener speech still going at the title clear is captioned from then.

    A line whose middle is still under the plate used to be dropped, so the
    first cue waited for a later word even though someone was talking.
    """
    from modules.club.captions import caption_cues, talking_windows

    windows = talking_windows(15, cta=False)
    title_end = windows["title"][1]
    body_start, body_end = windows["captions"]
    assert title_end == pytest.approx(2.5)
    assert body_start == pytest.approx(title_end)

    # No word times. The middle of the line is under the title, and the
    # line keeps going after the plate.
    segment_cues = caption_cues(
        [{
            "start": 0.2,
            "end": 4.0,
            "text": "keep talking through the title",
        }],
        body_start=body_start,
        body_end=body_end,
    )
    assert segment_cues
    assert segment_cues[0].start == pytest.approx(title_end)
    assert "talking" in segment_cues[0].text
    assert all(cue.start >= title_end - 1e-6 for cue in segment_cues)

    # The word in progress at 2.5s has its middle under the title. It is
    # the first caption, not the word that starts afterwards.
    word_cues = caption_cues(
        [{
            "start": 0.4,
            "end": 3.4,
            "text": "keep your the ball",
            "words": [
                {"start": 0.4, "end": 1.0, "text": "keep"},
                {"start": 1.0, "end": 1.6, "text": "your"},
                {"start": 2.0, "end": 2.8, "text": "the"},
                {"start": 2.8, "end": 3.4, "text": "ball"},
            ],
        }],
        body_start=body_start,
        body_end=body_end,
    )
    assert word_cues
    assert word_cues[0].start == pytest.approx(title_end)
    assert word_cues[0].text.split()[0] == "the"
    spoken = " ".join(cue.text for cue in word_cues)
    assert spoken == "the ball"
    assert "keep" not in spoken
    assert "your" not in spoken

    # A line that finished before the plate stays off the captions.
    early = caption_cues(
        [{"start": 0.2, "end": 2.2, "text": "under the title"}],
        body_start=body_start,
        body_end=body_end,
    )
    assert early == []
