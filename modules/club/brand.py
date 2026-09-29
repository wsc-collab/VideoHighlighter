"""Draw title, lower-third, and end-card type onto a finished cut.

The pictures stay the assembled clips. This pass only burns text the brief
asked for: a title and subtitle over the opening, a lower third through the
middle, and a call to action over the close. No plate is appended, and
nothing is synthesized.

A brief with none of TITLE, SUBTITLE, or CTA skips this pass. The highlights
cut is the whole result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from modules.club.brief import Brief


def brand_record(brief: Brief, *, applied: bool = False, error: str | None = None) -> dict:
    """What the JSON should say about on-screen type for this run."""
    return {
        "requested": brief.wants_brand(),
        "applied": applied,
        "error": error,
        "title": brief.title,
        "subtitle": brief.subtitle,
        "colors": list(brief.colors),
        "font": brief.font,
        "cta": brief.cta,
    }


def plate_windows(duration: float) -> dict[str, tuple[float, float]]:
    """Start and end, in seconds, for the title, lower third, and end card.

    Each plate is a span of the real cut. A ``(0, 0)`` window is not drawn.
    On a cut too short for three bands, the lower third is dropped so the
    title and the call to action do not sit on top of each other.
    """
    duration = max(0.0, float(duration))
    empty = {"title": (0.0, 0.0), "lower": (0.0, 0.0), "end": (0.0, 0.0)}
    if duration <= 0:
        return empty
    plate = min(3.0, max(1.0, duration * 0.22))
    if duration < plate * 2 + 0.5:
        half = duration / 2.0
        return {"title": (0.0, half), "lower": (0.0, 0.0), "end": (half, duration)}
    end_start = duration - plate
    return {
        "title": (0.0, plate),
        "lower": (plate, end_start),
        "end": (end_start, duration),
    }


def _luminance(rrggbb: str) -> float:
    r = int(rrggbb[0:2], 16) / 255.0
    g = int(rrggbb[2:4], 16) / 255.0
    b = int(rrggbb[4:6], 16) / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def brand_colors(colors: tuple[str, ...]) -> tuple[str, str]:
    """``(bar, type)`` as ``RRGGBB`` for a hype highlights card.

    The first brief color is the bar. The second is the type. One color
    keeps that bar and picks black or white type so the words stay readable.
    No colors means a dark bar and white type.

    Talking packs do not use this pair. See ``talking_type_colors``.
    """
    if not colors:
        return "111111", "FFFFFF"
    bar = colors[0].lstrip("#").upper()
    if len(colors) > 1:
        ink = colors[1].lstrip("#").upper()
    else:
        ink = "111111" if _luminance(bar) > 0.55 else "FFFFFF"
    return bar, ink


def neutral_ink(hex_color: str) -> str:
    """``FFFFFF`` or ``000000``.

    A light request, including a cream or orange accent, becomes white.
    A dark request becomes black. Talking type stays on one of those two.
    """
    raw = (hex_color or "").lstrip("#").upper()
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return "FFFFFF"
    return "000000" if _luminance(raw) < 0.5 else "FFFFFF"


def talking_type_colors() -> tuple[str, str]:
    """``(type, stroke)`` as ``RRGGBB`` for a talking title or call to action.

    White words with a black stroke, drawn on the picture. No plate, no
    filled box, and ``COLORS`` does not recolor the words.
    """
    return "FFFFFF", "000000"


def _enable(start: float, end: float) -> str:
    """``enable`` with the commas escaped so they do not split the filtergraph."""
    return f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"


def _fitted(text: str, width: int, height: int, font_path: str) -> tuple[str, int]:
    from modules.media.transitions import fit_caption
    lines, size = fit_caption(text, width, height, font_path)
    if not lines or size <= 0:
        return "", 0
    return "\n".join(lines), size


def brand_filter(
    brief: Brief,
    duration: float,
    font_path: str,
    width: int = 1920,
    height: int = 1080,
    windows: dict | None = None,
    centered: bool = False,
) -> str:
    """The ffmpeg ``-vf`` graph, or ``""`` when there is nothing to draw.

    ``font_path`` is a filesystem path. It is escaped for drawtext here.
    ``windows`` overrides the default title / lower-third / end timing.
    A talking pack passes windows whose lower third is empty so captions
    own the middle of the cut, and ``centered=True`` so the title and the
    call to action sit in the middle of the frame on the picture itself.
    That path draws no box. The hype path leaves ``centered`` off: those
    cards stay full-width bands with a translucent fill.
    """
    if not brief.wants_brand():
        return ""
    from modules.media.transitions import _escape_path, _escape_text

    windows = windows if windows is not None else plate_windows(duration)
    # Talking type is white with a black stroke and no filled box.
    # Brief COLORS still paint a hype card; they do not recolor this path.
    stroke = ""
    if centered:
        ink, stroke = talking_type_colors()
        bar = ""
    else:
        bar, ink = brand_colors(brief.colors)
    font = _escape_path(font_path) if font_path else ""
    if not font:
        return ""
    width = max(2, int(width))
    height = max(2, int(height))

    title_text, title_size = _fitted(brief.title, width, height, font_path)
    sub_text, sub_size = _fitted(brief.subtitle, width, height, font_path)
    if title_text and sub_text:
        sub_size = min(sub_size, max(16, int(title_size * 0.7)))
    cta_text, cta_size = _fitted(brief.cta, width, height, font_path)
    lower_src = brief.subtitle or brief.title
    lower_text, lower_size = _fitted(lower_src, width, height, font_path)
    if lower_text:
        lower_size = max(16, int(lower_size * 0.75))

    parts: list[str] = []

    def box(x, y, w, h, start, end, opacity: str = "0.78") -> None:
        parts.append(
            f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x{bar}@{opacity}:t=fill"
            f":{_enable(start, end)}"
        )

    def words(text: str, size: int, x: str, y: str, start: float, end: float, *,
              outlined: bool = False) -> None:
        # ``box=0`` keeps drawtext from painting its own background. The
        # border is a stroke. A two-pixel shadow is the same ink, offset,
        # not a bar behind the line.
        outline = ""
        if outlined and stroke:
            outline = (
                f":box=0:borderw={max(3, size // 10)}:bordercolor=0x{stroke}"
                f":shadowcolor=0x{stroke}:shadowx=2:shadowy=2"
            )
        parts.append(
            f"drawtext=fontfile='{font}':text='{_escape_text(text)}'"
            f":fontcolor=0x{ink}:fontsize={size}:x={x}:y={y}"
            f":line_spacing={max(4, size // 6)}{outline}:{_enable(start, end)}"
        )

    def centered_block(primary: str, primary_size: int, secondary: str,
                       secondary_size: int, start: float, end: float) -> None:
        if primary and secondary:
            gap = max(8, primary_size // 6)
            block = primary_size + gap + secondary_size
            words(primary, primary_size, "(w-tw)/2", f"(h-{block})/2", start, end, outlined=True)
            words(secondary, secondary_size, "(w-tw)/2",
                  f"(h-{block})/2+{primary_size + gap}", start, end, outlined=True)
        else:
            words(primary or secondary, primary_size or secondary_size,
                  "(w-tw)/2", "(h-th)/2", start, end, outlined=True)

    title_on, title_off = windows["title"]
    if title_off > title_on and (title_text or sub_text):
        if centered:
            centered_block(title_text, title_size, sub_text, sub_size, title_on, title_off)
        else:
            box(0, 0, "iw", "ih*0.30", title_on, title_off)
            if title_text and sub_text:
                words(title_text, title_size, "(w-tw)/2", "h*0.05", title_on, title_off)
                words(sub_text, sub_size, "(w-tw)/2", "h*0.16", title_on, title_off)
            else:
                words(title_text or sub_text, title_size or sub_size,
                      "(w-tw)/2", "(h*0.30-th)/2", title_on, title_off)

    lower_on, lower_off = windows["lower"]
    if lower_off > lower_on and lower_text:
        box(0, "ih*0.78", "iw", "ih*0.22", lower_on, lower_off)
        words(lower_text, lower_size, "(w-tw)/2", "h*0.84", lower_on, lower_off)

    end_on, end_off = windows["end"]
    if end_off > end_on and cta_text:
        if centered:
            centered_block(cta_text, cta_size, "", 0, end_on, end_off)
        else:
            box(0, "ih*0.33", "iw", "ih*0.34", end_on, end_off)
            words(cta_text, cta_size, "(w-tw)/2", "(h-th)/2", end_on, end_off)

    return ",".join(parts)


def _fontconfig(name: str) -> str:
    """A font file whose family matches ``name``, or ``""``.

    ``fc-match`` always answers with some font, including when the name is
    unknown. A match counts only when the family or the file name contains
    the request.
    """
    if not name or shutil.which("fc-match") is None:
        return ""
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{family}|%{file}", name],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    family, sep, file = (result.stdout or "").strip().partition("|")
    if result.returncode != 0 or not sep or not os.path.isfile(file):
        return ""
    needle = name.casefold()
    if needle in family.casefold() or needle in Path(file).stem.casefold():
        return file
    return ""


def _fallback_font() -> str:
    from modules.media.transitions import _font_path
    return _font_path()


# Drop Inter in either place. The Mac user font folder is the usual
# install. ``fonts/`` next to this repo is the copy the club clone can carry.
INTER_INSTALL = "~/Library/Fonts/Inter-Regular.otf"
CLUB_FONTS_DIR = "fonts"

_FONT_FILES = {
    "inter": (
        "Inter-Regular.otf",
        "Inter-Regular.ttf",
        "Inter.otf",
        "Inter.ttf",
    ),
}


def font_for_brand(brand: str) -> str:
    """Default face when ``FONT`` is left blank.

    WSC and BSC use Inter. Tier 1 uses Inter as well until a display face
    is named. Interwald is not that face.
    """
    if brand in {"tier1", "wsc", "bsc"}:
        return "Inter"
    return ""


def type_font(brief: Brief) -> str:
    """Face for the title, the captions, and the call to action.

    ``CAPTION_FONT`` wins, then ``FONT``, then the brand default.
    """
    return (brief.caption_font or brief.font or font_for_brand(brief.brand)).strip()


def club_font_dirs() -> list[Path]:
    """Folders checked for Inter before fontconfig."""
    home = Path.home()
    repo = Path(__file__).resolve().parents[2]
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return [
        repo / CLUB_FONTS_DIR,
        home / "Library" / "Fonts",
        Path("/Library/Fonts"),
        home / ".local" / "share" / "fonts",
        home / "Desktop" / "Marketing" / "Grok Bot Work" / "VideoHighlighter" / CLUB_FONTS_DIR,
        windir / "Fonts",
    ]


def _file_for_name(name: str, directories) -> str:
    filenames = _FONT_FILES.get(name.casefold(), ())
    if not filenames:
        return ""
    for folder in directories:
        for filename in filenames:
            candidate = Path(folder) / filename
            if candidate.is_file():
                return str(candidate.resolve())
    return ""


def _lookup_font(spec: str, directories) -> str:
    candidate = Path(spec).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    found = _file_for_name(spec, directories)
    if found:
        return found
    return _fontconfig(spec)


def resolve_font(spec: str, log_fn=print, search_dirs=None) -> str:
    """A ``.ttf``/``.otf``/``.ttc`` path for ``FONT``.

    A path that exists is used as given. Otherwise the string is a font
    name. Inter is looked up under ``~/Library/Fonts`` and ``fonts/`` in
    the clone, then by family name. If that file is missing, a bold sans
    already on the machine is used, and the substitution is logged.
    """
    spec = (spec or "").strip().strip("\"'")
    directories = list(search_dirs) if search_dirs is not None else club_font_dirs()
    if spec:
        found = _lookup_font(spec, directories)
        if found:
            return found
        if spec.casefold() == "inter":
            log_fn(
                "FONT 'Inter' was not found. Install it at "
                f"{INTER_INSTALL} or as {CLUB_FONTS_DIR}/Inter-Regular.otf "
                "next to the app. Using a system sans."
            )
        else:
            log_fn(f"FONT {spec!r} was not found; using a system sans.")
    return _fallback_font()


def apply_brand(src: str, dst: str, brief: Brief, duration: float, log_fn=print) -> str:
    """Burn the brief's type onto ``src`` and write ``dst``.

    Raises ``RuntimeError`` when there is no font or ffmpeg rejects the
    graph. The caller keeps the unbranded cut in that case. Audio is
    copied from the assembled clips.
    """
    from modules.system.app_paths import ffmpeg_exe

    font = resolve_font(type_font(brief), log_fn=log_fn)
    if not font:
        raise RuntimeError(
            "No font for on-screen type. Set FONT to a .ttf path or a font name."
        )
    width, height = 1920, 1080
    try:
        from modules.media.video_probe import probe_video
        info = probe_video(src)
        width = int(info.get("width") or width) or width
        height = int(info.get("height") or height) or height
    except Exception as exc:
        log_fn(f"Using 1920x1080 for type size ({exc}).")
    graph = brand_filter(brief, duration, font, width, height)
    if not graph:
        raise RuntimeError("Brief has no on-screen type to draw.")
    log_fn("Drawing title, lower third, and end card on the assembled cut.")
    result = subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", src, "-vf", graph,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "copy", dst],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "ffmpeg failed"
        raise RuntimeError(f"On-screen type failed: {detail}")
    return dst
