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
        "title_accent": brief.title_accent,
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


def split_title_accent(title: str, accent: str) -> list[tuple[str, bool]]:
    """``(text, is_accent)`` pieces. The accent is the first matching span.

    Matching is case-sensitive first, then case-insensitive, and the span
    keeps the spelling already in the title. No accent means one plain piece.
    """
    text = title or ""
    needle = (accent or "").strip()
    if not text:
        return []
    if not needle:
        return [(text, False)]
    index = text.find(needle)
    if index < 0:
        folded = text.casefold()
        index = folded.find(needle.casefold())
        if index < 0:
            return [(text, False)]
        needle = text[index:index + len(needle)]
    parts: list[tuple[str, bool]] = []
    if index:
        parts.append((text[:index], False))
    parts.append((needle, True))
    rest = text[index + len(needle):]
    if rest:
        parts.append((rest, False))
    return parts


# Script faces tried, in order, when TITLE_ACCENT is set. The rest of the
# title stays Inter Bold. Pacifico is a heavy script that stays readable
# on a phone; Dancing Script is the next, still less flourished than a
# thin calligraphy face. None of these are bundled; the first file found
# on the machine is used.
_SCRIPT_FAMILIES = (
    "Pacifico",
    "Dancing Script",
    "Segoe Script",
    "Brush Script MT",
    "Snell Roundhand",
    "Apple Chancery",
    "Lucida Calligraphy",
    "Great Vibes",
    "Allura",
)
_SCRIPT_FILES = (
    "Pacifico-Regular.ttf",
    "Pacifico-Regular.otf",
    "Pacifico.ttf",
    "DancingScript-Regular.ttf",
    "DancingScript-Bold.ttf",
    "DancingScript-Regular.otf",
    "DancingScript-VariableFont_wght.ttf",
    "Segoe Script.ttf",
    "GreatVibes-Regular.ttf",
    "GreatVibes-Regular.otf",
    "Allura-Regular.ttf",
    "Allura-Regular.otf",
)


def resolve_accent_font(log_fn=print, search_dirs=None) -> str:
    """A script or calligraphy file for ``TITLE_ACCENT``, or ``""``.

    Searches the same folders as Inter, then fontconfig. A missing face
    is logged; the title still draws, in Inter Bold, with no second face.
    """
    directories = list(search_dirs) if search_dirs is not None else club_font_dirs()
    for folder in directories:
        for name in _SCRIPT_FILES:
            candidate = Path(folder) / name
            if candidate.is_file():
                return str(candidate.resolve())
    for family in _SCRIPT_FAMILIES:
        found = _fontconfig(family)
        if found:
            return found
    log_fn(
        "No script face for TITLE_ACCENT. Install Pacifico or Dancing Script "
        "next to Inter. The name stays in Inter Bold."
    )
    return ""


def _balanced_words(words: list[str], n_lines: int) -> list[str]:
    """Split ``words`` into ``n_lines`` lines of nearly equal word counts."""
    if not words:
        return []
    n_lines = max(1, min(int(n_lines), len(words)))
    if n_lines == 1:
        return [" ".join(words)]
    counts = [len(words) // n_lines] * n_lines
    for index in range(len(words) % n_lines):
        counts[index] += 1
    lines = []
    cursor = 0
    for count in counts:
        lines.append(" ".join(words[cursor:cursor + count]))
        cursor += count
    return [line for line in lines if line]


def _word_lines(words: list[str]) -> list[str]:
    """One line for a short title, two or three when there are more words."""
    if not words:
        return []
    if len(words) <= 3:
        return [" ".join(words)]
    if len(words) <= 5:
        return _balanced_words(words, 2)
    return _balanced_words(words, 3)


def talking_title_lines(title: str, accent: str) -> list[list[tuple[str, bool]]]:
    """Lines for a talking title. Each line is ``(text, is_accent)`` pieces.

    A long title is two or three lines at a large size. The accent phrase
    stays together on its own line so the name is not shrunk onto one
    tiny row with the rest of the sentence. A short title stays one line.
    """
    text = " ".join((title or "").split())
    if not text:
        return []
    words: list[tuple[str, bool]] = []
    for piece, is_accent in split_title_accent(text, accent):
        for word in piece.split():
            words.append((word, is_accent))
    if not words:
        return []
    if not any(flag for _word, flag in words):
        return [[(line, False)] for line in _word_lines([word for word, _flag in words])]

    first = next(index for index, (_word, flag) in enumerate(words) if flag)
    last = len(words) - 1 - next(
        index for index, (_word, flag) in enumerate(reversed(words)) if flag
    )
    before = [word for word, _flag in words[:first]]
    accent_words = [word for word, _flag in words[first:last + 1]]
    after = [word for word, _flag in words[last + 1:]]
    before_n = 2 if len(before) >= 4 else (1 if before else 0)
    after_n = 2 if len(after) >= 4 else (1 if after else 0)
    while before_n + after_n + 1 > 3:
        if before_n >= after_n and before_n > 1:
            before_n -= 1
        elif after_n > 1:
            after_n -= 1
        else:
            break
    lines: list[list[tuple[str, bool]]] = []
    for line in _balanced_words(before, before_n):
        lines.append([(line, False)])
    lines.append([(" ".join(accent_words), True)])
    for line in _balanced_words(after, after_n):
        lines.append([(line, False)])
    return lines


def fit_talking_title_size(
    lines: list[list[tuple[str, bool]]],
    width: int,
    height: int,
    body_font: str,
    accent_font: str = "",
) -> int:
    """Point size for a multi-line talking title. Large, then shrunk to fit.

    The long edge sets the start so a portrait frame does not inherit a
    landscape-small size. Shrink only until every line fits the frame
    width. The floor stays well above a one-line shrink of a long sentence.
    """
    from modules.media.transitions import TEXT_WIDTH, _measurer

    if not lines:
        return 0
    measure_body = _measurer(body_font)
    measure_accent = _measurer(accent_font) if accent_font else measure_body
    limit = max(1.0, width * TEXT_WIDTH)
    size = max(64, int(round(max(int(width), int(height)) * 0.062)))

    def line_width(line, point: int) -> float:
        total = 0.0
        for text, is_accent in line:
            measure = measure_accent if is_accent else measure_body
            total += measure(text, point)
        return total

    while size > 48:
        gap = max(10, size // 5)
        block = size * len(lines) + gap * max(0, len(lines) - 1)
        fits = all(line_width(line, size) <= limit for line in lines)
        if fits and block <= height * 0.62:
            return size
        nxt = int(size * 0.94)
        if nxt >= size:
            break
        size = nxt
    return max(48, size)


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
    accent_font: str = "",
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
    # Talking title and CTA are white with no stroke. Captions keep their
    # own black outline. Brief COLORS still paint a hype card.
    if centered:
        ink, _stroke = talking_type_colors()
        bar = ""
    else:
        bar, ink = brand_colors(brief.colors)
    font = _escape_path(font_path) if font_path else ""
    if not font:
        return ""
    width = max(2, int(width))
    height = max(2, int(height))

    # Talking titles wrap to two or three large lines. The accent phrase
    # stays on its own line. Hype cards keep the single fitted block.
    title_lines: list[list[tuple[str, bool]]] = []
    title_font_path = bold_font(font_path) if centered else font_path
    title_font = _escape_path(title_font_path)
    if centered and (brief.title_accent or "").strip() and not accent_font:
        accent_font = resolve_accent_font()
    accent_face = _escape_path(accent_font) if accent_font else ""
    if centered and (brief.title or "").strip():
        title_lines = talking_title_lines(brief.title, brief.title_accent)
        title_text = brief.title
        title_size = fit_talking_title_size(
            title_lines, width, height, title_font_path, accent_font,
        )
    else:
        title_text, title_size = _fitted(brief.title, width, height, font_path)
    sub_text, sub_size = _fitted(brief.subtitle, width, height, font_path)
    if title_text and sub_text:
        sub_size = min(sub_size, max(16, int(title_size * 0.7)))
    cta_text, cta_size = _fitted(brief.cta, width, height, font_path)
    # Same face and treatment as the title, a step smaller so the close
    # does not read as a second title.
    if centered and cta_text:
        reference = title_size or cta_size
        smaller = max(16, int(round(reference * 0.82)))
        if title_size and smaller >= title_size and title_size > 16:
            smaller = title_size - 2
        cta_size = min(cta_size, smaller) if cta_size else smaller
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

    # Talking title and CTA: bold white, no stroke, centered on the picture.
    # The shadow is a little stronger than a 2px 45% drop. It is still not
    # a plate and not an outline.
    shadow = ":shadowcolor=black@0.58:shadowx=3:shadowy=4"

    def words(text: str, size: int, x: str, y: str, start: float, end: float, *,
              clean: bool = False, face: str = "", align: bool = True) -> None:
        # ``box=0`` and ``borderw=0`` keep a filled plate and a stroke off
        # the talking title. The shadow is the only thing behind the words.
        chosen = face or (title_font if clean else font)
        extra = ""
        if clean:
            extra = ":box=0:borderw=0" + shadow
            if align:
                extra += ":text_align=center"
        parts.append(
            f"drawtext=fontfile='{chosen}':text='{_escape_text(text)}'"
            f":fontcolor=0x{ink}:fontsize={size}:x={x}:y={y}"
            f":line_spacing={max(4, size // 6)}{extra}:{_enable(start, end)}"
        )

    def accented_line(text: str, size: int, y: str, start: float, end: float) -> None:
        """One centered line. The accent span uses the script face when set."""
        parts_of = split_title_accent(text, brief.title_accent if centered else "")
        accented = [flag for _piece, flag in parts_of]
        if not accent_face or not any(accented) or len(parts_of) <= 1 and not any(accented):
            words(text, size, "(w-tw)/2", y, start, end, clean=True)
            return
        from modules.media.transitions import _measurer
        measure_body = _measurer(title_font_path)
        measure_accent = _measurer(accent_font)
        widths = []
        for piece, is_accent in parts_of:
            measure = measure_accent if is_accent else measure_body
            widths.append(max(1, int(round(measure(piece, size)))))
        total = sum(widths)
        cursor = 0
        for (piece, is_accent), piece_w in zip(parts_of, widths):
            words(
                piece, size, f"(w-{total})/2+{cursor}", y, start, end,
                clean=True, face=accent_face if is_accent else "", align=False,
            )
            cursor += piece_w

    def centered_block(primary: str, primary_size: int, secondary: str,
                       secondary_size: int, start: float, end: float) -> None:
        if primary and secondary:
            gap = max(8, primary_size // 6)
            block = primary_size + gap + secondary_size
            accented_line(primary, primary_size, f"(h-{block})/2", start, end)
            words(secondary, secondary_size, "(w-tw)/2",
                  f"(h-{block})/2+{primary_size + gap}", start, end, clean=True)
        else:
            accented_line(
                primary or secondary, primary_size or secondary_size,
                "(h-th)/2", start, end,
            )

    def draw_pieces(pieces, size: int, y: str, start: float, end: float) -> None:
        if not pieces or size <= 0:
            return
        if len(pieces) == 1:
            text, is_accent = pieces[0]
            face = accent_face if is_accent and accent_face else ""
            words(text, size, "(w-tw)/2", y, start, end, clean=True, face=face)
            return
        from modules.media.transitions import _measurer
        measure_body = _measurer(title_font_path)
        measure_accent = _measurer(accent_font) if accent_font else measure_body
        widths = []
        for text, is_accent in pieces:
            measure = measure_accent if is_accent else measure_body
            widths.append(max(1, int(round(measure(text, size)))))
        total = sum(widths)
        cursor = 0
        for (text, is_accent), piece_w in zip(pieces, widths):
            words(
                text, size, f"(w-{total})/2+{cursor}", y, start, end,
                clean=True, face=accent_face if is_accent and accent_face else "",
                align=False,
            )
            cursor += piece_w

    def draw_talking_title(start: float, end: float) -> None:
        rows = [(pieces, title_size) for pieces in title_lines if pieces and title_size]
        if sub_text and sub_size:
            rows.append(([(sub_text, False)], sub_size))
        if not rows:
            return
        gap = max(10, (rows[0][1] or 16) // 5)
        block = sum(size for _pieces, size in rows) + gap * (len(rows) - 1)
        y = 0
        for pieces, size in rows:
            draw_pieces(pieces, size, f"(h-{block})/2+{y}", start, end)
            y += size + gap

    title_on, title_off = windows["title"]
    if title_off > title_on and (title_text or sub_text):
        if centered and title_lines:
            draw_talking_title(title_on, title_off)
        elif centered:
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

_BOLD_FILES = (
    "Inter-Bold.otf",
    "Inter-Bold.ttf",
    "InterBold.otf",
    "InterBold.ttf",
)


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


def bold_font(path: str) -> str:
    """A bold file for ``path``, or ``path`` when none is installed.

    Title and the call to action use this. Captions stay on the regular face.
    """
    if not path:
        return path
    source = Path(path)
    names: list[str] = []
    if "Regular" in source.stem:
        names.append(source.stem.replace("Regular", "Bold") + source.suffix)
    names.extend(_BOLD_FILES)
    folders = [source.parent, *club_font_dirs()]
    seen: set[str] = set()
    for folder in folders:
        for name in names:
            candidate = Path(folder) / name
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return str(candidate.resolve())
    found = _fontconfig_style("Inter", "Bold")
    if found:
        return found
    return path


def _fontconfig_style(family: str, style: str) -> str:
    """A font file whose family and style match, or ``""``."""
    if not family or shutil.which("fc-match") is None:
        return ""
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{family}|%{style}|%{file}", f"{family}:style={style}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    parts = (result.stdout or "").strip().split("|")
    if result.returncode != 0 or len(parts) != 3:
        return ""
    got_family, got_style, file = parts
    if not os.path.isfile(file):
        return ""
    if family.casefold() not in got_family.casefold():
        return ""
    if style.casefold() not in got_style.casefold():
        return ""
    return file


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
