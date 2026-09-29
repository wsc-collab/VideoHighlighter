"""Parse a club ``brief.md``.

The file is a short operator note, not a detector preset. Four fields:

    LENGTH: 20-35s
    STYLE: hype
    KEYWORDS: ace, rally
    NOTES: Prefer the last shot of each point.

``LENGTH`` is the finished draft's duration. A single number (``30s``) means
that exact length. ``STYLE`` is ``hype`` or ``talking``. ``KEYWORDS`` and
``NOTES`` may be empty. ``NOTES`` is stored with the run for the editor; it
is not sent to a generator.
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

STYLES = ("hype", "talking")
REQUIRED = ("LENGTH", "STYLE")
# Only these names start a field. A colon inside NOTES ("Prefer: the last
# point") would otherwise be read as a new key.
KNOWN_FIELDS = {"LENGTH", "STYLE", "KEYWORDS", "NOTES"}


@dataclass(frozen=True)
class Brief:
    length_min_s: float
    length_max_s: float
    style: str
    keywords: tuple[str, ...]
    notes: str
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "length_min_s": self.length_min_s,
            "length_max_s": self.length_max_s,
            "style": self.style,
            "keywords": list(self.keywords),
            "notes": self.notes,
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
    KEY is LENGTH, STYLE, KEYWORDS, or NOTES. Following lines belong to that
    field until the next known key. Anything else — a title, a sentence with
    a colon — stays in the current field.
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
    return Brief(
        length_min_s=lo,
        length_max_s=hi,
        style=style,
        keywords=parse_keywords(fields.get("KEYWORDS", "")),
        notes=fields.get("NOTES", "").strip(),
        source=source,
    )


def load_brief(path: str | Path) -> Brief:
    file = Path(path)
    if not file.is_file():
        raise BriefError(f"No brief at {file}. Expected brief.md next to the clips.")
    return parse_brief(file.read_text(encoding="utf-8"), source=str(file))
