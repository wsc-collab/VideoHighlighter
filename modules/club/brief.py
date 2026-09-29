"""Parse a club ``brief.md``.

The file is a short operator note, not a detector preset. Required fields:

    LENGTH: 20-35s
    STYLE: hype
    KEYWORDS: ace, rally
    NOTES: Prefer the last shot of each point.

``STYLE: talking`` is the club pack: an editorial title for the opening
seconds, burned-in captions of what is said, and an optional call to
action at the end. ``TITLE`` names who is on camera and what the video
is (``Private lesson with Coach John Wang``). It is not a line from the
transcript. Caption words come from the recording. They are not written
in the brief, and they are not copied up into the title.

    BRAND: tier1
    TITLE: Private lesson with Coach John Wang
    FONT: Interwald
    KEYWORDS: lesson, finish
    NOTES: Prefer windows where the coach is speaking.

Leave ``CTA`` out for no end card. A soft line such as ``CTA: Book a
lesson`` is optional. Leave ``COLORS`` out on a talking brief. Title,
captions, and the call to action sit on the picture: white type, black
stroke, no filled plate. ``COLORS`` still paints a hype card, not this pack.

Leave ``CAPTION_SIZE`` and ``CAPTION_POSITION`` out to use the talking
defaults: captions in the center of the frame, about four words a line,
and a point size that follows the frame height with a floor of 64.
``CAPTION_POSITION: bottom`` or ``top`` still works. ``middle`` is the
same place as ``center``.

``LENGTH`` is the finished draft's duration. A single number (``30s``) means
that exact length. ``STYLE`` is ``hype`` or ``talking``. ``KEYWORDS`` and
``NOTES`` may be empty. ``NOTES`` is stored for the editor. It is not a
prompt and it is not caption text. Without a title, subtitle, or call to
action, a non-talking brief stays a highlights cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class BriefError(ValueError):
    """The brief is missing a required field or a field cannot be read."""


_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$")
_HEADING = re.compile(r"^#{1,6}\s+([A-Za-z][A-Za-z0-9_]*)\s*$")
_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?\s*(?:-|–|—|to)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")
_HEX = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$")

STYLES = ("hype", "talking")
REQUIRED = ("LENGTH", "STYLE")
# Only these names start a field. A colon inside NOTES ("Prefer: the last
# point") would otherwise be read as a new key.
KNOWN_FIELDS = {
    "LENGTH", "STYLE", "KEYWORDS", "NOTES",
    "TITLE", "SUBTITLE", "COLORS", "FONT", "CTA", "BRAND",
    "CAPTION_COLOR", "CAPTION_STROKE", "CAPTION_SIZE",
    "CAPTION_POSITION", "CAPTION_FONT",
}
CAPTION_POSITIONS = ("center", "middle", "bottom", "top")


@dataclass(frozen=True)
class Brief:
    length_min_s: float
    length_max_s: float
    style: str
    keywords: tuple[str, ...]
    notes: str
    source: str = ""
    title: str = ""
    subtitle: str = ""
    colors: tuple[str, ...] = ()
    font: str = ""
    brand: str = ""
    cta: str = ""
    caption_color: str = ""
    caption_stroke: str = ""
    caption_stroke_width: int = 0
    caption_size: int = 0
    caption_position: str = "center"
    caption_font: str = ""

    def wants_brand(self) -> bool:
        """True when the brief asks for type on the cut.

        Colors and a font alone do not start a pass: there is nothing to draw.
        """
        return bool(self.title or self.subtitle or self.cta)

    def as_dict(self) -> dict:
        return {
            "length_min_s": self.length_min_s,
            "length_max_s": self.length_max_s,
            "style": self.style,
            "keywords": list(self.keywords),
            "notes": self.notes,
            "title": self.title,
            "subtitle": self.subtitle,
            "colors": list(self.colors),
            "font": self.font,
            "brand": self.brand,
            "cta": self.cta,
            "caption_color": self.caption_color,
            "caption_stroke": self.caption_stroke,
            "caption_stroke_width": self.caption_stroke_width,
            "caption_size": self.caption_size,
            "caption_position": self.caption_position,
            "caption_font": self.caption_font,
        }


def parse_length(text: str) -> tuple[float, float]:
    """Return ``(min_seconds, max_seconds)`` from a LENGTH value."""
    raw = " ".join(text.split())
    if not raw:
        raise BriefError("LENGTH is empty. Example: LENGTH: 20-35s")
    match = _RANGE.search(raw)
    if match:
        lo, hi = float(match.group(1)), float(match.group(2))
    else:
        found = _NUMBER.findall(raw)
        if not found:
            raise BriefError(
                f"LENGTH {text!r} needs a duration. Example: LENGTH: 20-35s"
            )
        lo = hi = float(found[0])
    if lo > hi:
        lo, hi = hi, lo
    if hi <= 0:
        raise BriefError("LENGTH must be greater than zero.")
    return lo, hi


def parse_style(text: str) -> str:
    style = text.strip().lower()
    if style not in STYLES:
        raise BriefError(
            f"STYLE must be hype or talking, got {text!r}."
        )
    return style


def _one_line(text: str) -> str:
    """Join wrapped brief lines into the single string that gets drawn."""
    return " ".join(part.strip() for part in text.splitlines() if part.strip())


def parse_brand(text: str) -> str:
    """``tier1``, ``wsc``, or ``bsc``. Empty means no brand default."""
    raw = " ".join(text.strip().lower().replace("_", " ").replace("-", " ").split())
    if not raw:
        return ""
    if raw in {"tier1", "tier 1"}:
        return "tier1"
    if raw in {"wsc", "bsc"}:
        return raw
    raise BriefError(
        f"BRAND must be tier1, wsc, or bsc, got {text!r}."
    )


def parse_colors(text: str) -> tuple[str, ...]:
    """``#RGB`` or ``#RRGGBB`` values, comma or whitespace separated.

    Stored as ``#RRGGBB``. An empty field is no palette. A bad token is a
    brief error, caught before any clip is cut.
    """
    raw = text.strip()
    if not raw:
        return ()
    colors: list[str] = []
    for part in re.split(r"[\s,;]+", raw):
        part = part.strip().strip("\"'")
        if not part:
            continue
        match = _HEX.match(part)
        if not match:
            raise BriefError(
                f"COLORS entry {part!r} is not a hex color. "
                "Example: COLORS: #112233, #FFFFFF"
            )
        hexes = match.group(1)
        if len(hexes) == 3:
            hexes = "".join(ch * 2 for ch in hexes)
        colors.append("#" + hexes.upper())
    return tuple(colors)


def parse_caption_position(text: str) -> str:
    raw = text.strip().lower()
    if not raw:
        return "center"
    if raw not in CAPTION_POSITIONS:
        raise BriefError(
            f"CAPTION_POSITION must be center, bottom, or top, got {text!r}."
        )
    if raw == "middle":
        return "center"
    return raw


def parse_caption_size(text: str) -> int:
    raw = text.strip()
    if not raw:
        return 0
    try:
        size = int(float(raw))
    except ValueError:
        raise BriefError(
            f"CAPTION_SIZE must be a point size, got {text!r}."
        ) from None
    if size <= 0:
        raise BriefError("CAPTION_SIZE must be greater than zero.")
    return size


def parse_caption_stroke(text: str) -> tuple[str, int]:
    """``#000000`` or ``#000000 3``. Empty means the burn-in default."""
    raw = text.strip()
    if not raw:
        return "", 0
    color = ""
    width = 0
    for part in re.split(r"[\s,]+", raw):
        if not part:
            continue
        if _HEX.match(part):
            color = parse_colors(part)[0]
            continue
        if re.fullmatch(r"\d+", part):
            width = int(part)
            continue
        raise BriefError(
            f"CAPTION_STROKE entry {part!r} is not a hex color or a width. "
            "Example: CAPTION_STROKE: #000000 3"
        )
    return color, width


def parse_caption_color(text: str) -> str:
    colors = parse_colors(text)
    if len(colors) > 1:
        raise BriefError(
            "CAPTION_COLOR takes one hex color. Example: CAPTION_COLOR: #FFFFFF"
        )
    return colors[0] if colors else ""


def parse_keywords(text: str) -> tuple[str, ...]:
    """Split on commas, semicolons, and newlines. Leading dashes are bullets."""
    parts = re.split(r"[\n,;]+", text)
    seen: set[str] = set()
    keywords: list[str] = []
    for part in parts:
        item = part.strip().lstrip("-").strip().strip("\"'")
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(item)
    return tuple(keywords)


def _split_fields(text: str) -> dict[str, str]:
    """Map FIELD names to their raw text.

    A line ``KEY: value`` or a markdown heading ``# KEY`` starts a field when
    KEY is one of the known brief fields. Following lines belong to that
    field until the next known key. Anything else — a prose heading, a
    sentence with a colon — stays in the current field.
    """
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        heading = _HEADING.match(stripped)
        keyed = _FIELD.match(stripped)
        name = ""
        first = ""
        if heading:
            name = heading.group(1).upper()
        elif keyed:
            name = keyed.group(1).upper()
            first = keyed.group(2).strip()
        if name in KNOWN_FIELDS:
            current = name
            fields.setdefault(current, [])
            if first:
                fields[current].append(first)
            continue
        if current is None:
            continue
        fields[current].append(line.rstrip())
    return {name: "\n".join(lines).strip() for name, lines in fields.items()}


def parse_brief(text: str, source: str = "") -> Brief:
    """Parse brief text. Raises ``BriefError`` when LENGTH or STYLE is unusable."""
    fields = _split_fields(text.lstrip("\ufeff"))
    missing = [name for name in REQUIRED if not fields.get(name, "").strip()]
    if missing:
        raise BriefError(
            "brief.md is missing "
            + ", ".join(missing)
            + ". Required lines: LENGTH: 20-35s and STYLE: hype or STYLE: talking."
        )
    lo, hi = parse_length(fields["LENGTH"])
    style = parse_style(fields["STYLE"])
    stroke_color, stroke_width = parse_caption_stroke(fields.get("CAPTION_STROKE", ""))
    return Brief(
        length_min_s=lo,
        length_max_s=hi,
        style=style,
        keywords=parse_keywords(fields.get("KEYWORDS", "")),
        notes=fields.get("NOTES", "").strip(),
        source=source,
        title=_one_line(fields.get("TITLE", "")),
        subtitle=_one_line(fields.get("SUBTITLE", "")),
        colors=parse_colors(fields.get("COLORS", "")),
        font=_one_line(fields.get("FONT", "")),
        brand=parse_brand(fields.get("BRAND", "")),
        cta=_one_line(fields.get("CTA", "")),
        caption_color=parse_caption_color(fields.get("CAPTION_COLOR", "")),
        caption_stroke=stroke_color,
        caption_stroke_width=stroke_width,
        caption_size=parse_caption_size(fields.get("CAPTION_SIZE", "")),
        caption_position=parse_caption_position(fields.get("CAPTION_POSITION", "")),
        caption_font=_one_line(fields.get("CAPTION_FONT", "")),
    )


def load_brief(path: str | Path) -> Brief:
    file = Path(path)
    if not file.is_file():
        raise BriefError(f"No brief at {file}. Expected brief.md next to the clips.")
    return parse_brief(file.read_text(encoding="utf-8"), source=str(file))
