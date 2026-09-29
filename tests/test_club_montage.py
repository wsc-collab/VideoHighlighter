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
        assert model == "base"
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
    assert scores["signals"]["whisper"] == "base"
    assert scores["signals"]["audio_peaks"] is True
    assert scores["signals"]["motion"] is True
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
    assert brief.caption_position == "center"
    assert brief.caption_size == 0
    assert caption_style(brief, 1080)["size"] == 16
    assert caption_style(brief, 1080)["position"] == "center"
    assert caption_style(brief, 1080)["color"] == "#FFFFFF"
    assert caption_style(brief, 1080)["stroke"] == "#000000"
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
    assert "Private lesson with Coach John Wang" in graph
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
    style = caption_style(brief, 1080)
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
    assert caption_style(black, 1080)["color"] == "#000000"
    assert caption_style(black, 1080)["stroke"] == "#FFFFFF"
    script = render_ass(
        [CaptionCue(4.0, 5.4, "Hold your finish")],
        brief, width=1920, height=1080, font_path="",
    )
    assert "&HFF000000" in script
    assert ",1,2,0,5," in script


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
    script = render_ass(
        [CaptionCue(1.0, 3.0, long)],
        brief, width=320, height=1080, font_path="",
    )
    assert r"\N" not in script
    assert script.count("Dialogue:") == len(cues)
    assert "WrapStyle: 2" in script


def test_title_uses_a_bold_file_when_one_is_installed(tmp_path):
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


def test_caption_defaults_are_center_four_words_and_larger_type():
    from modules.club.captions import CaptionCue, caption_cues, caption_style, render_ass
    brief = parse_brief("LENGTH: 12s\nSTYLE: talking\nTITLE: Hi\nCTA: Go\n")
    assert brief.caption_position == "center"
    assert brief.caption_size == 0
    assert caption_style(brief, 720)["size"] == 16
    assert caption_style(brief, 1080)["size"] == 16
    assert caption_style(brief, 1920)["size"] == 22
    assert caption_style(brief, 1080)["position"] == "center"
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
    assert f"Style: Caption,DejaVu Sans,{caption_style(brief, 1080)['size']}," in script
    assert ",5,40,40,0,1" in script


def test_quiet_clip_names_keep_the_loudest_and_close_levels():
    from modules.club.montage import quiet_clip_names
    assert quiet_clip_names([("lav.mp4", -10.0), ("phone.mp4", -22.0)]) == ["phone.mp4"]
    assert quiet_clip_names([("lav.mp4", -10.0), ("phone.mp4", -21.0)]) == []
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
    assert cuts["mic_preference"]["dropped"] == ["phone.mp4"]
    assert cuts["mic_preference"]["enabled"] is True


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


def test_main_py_runs_club_before_the_gui_imports():
    text = Path("main.py").read_text(encoding="utf-8")
    club = text.index('if "--club" in sys.argv[1:]:')
    qt = text.index("from PySide6")
    assert club < qt
    assert "from modules.club.montage import main as _club_main" in text
