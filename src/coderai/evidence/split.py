"""Split a broad request into children that can each be proved on their own.

A 41 000-line change cannot be verified as one thing. When work crosses surfaces —
schema, API, UI, translations, docs — each surface becomes a child with its own
acceptance items and its own evidence. A green API child says nothing about the
UI child, which is exactly the failure this prevents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coderai.evidence.acceptance import Acceptance, Item
from coderai.evidence.companions import Companion

# Ordered by dependency: the schema before the code that reads it, the API before
# the UI that consumes it, the strings last.
SURFACES = ("schema", "api", "ui", "i18n", "docs")
# Matched most-specific first: "ja and en strings for the card" is i18n work, even
# though it mentions a card.
MATCH_ORDER = ("i18n", "schema", "docs", "ui", "api")
SPLIT_THRESHOLD = 6          # items in one surface before size alone justifies splitting

_PATH_HINTS = {
    "schema": ("migration", "migrations", "alembic", "schema", "models", "prisma", ".sql"),
    "api": ("api", "server", "backend", "handler", "route", "routes", "service", "usecase",
            "controller"),
    "ui": (".tsx", ".jsx", ".vue", ".svelte", "web", "frontend", "components", "pages", "app"),
    "i18n": ("locale", "locales", "i18n", "lang", "translations", "messages"),
    "docs": (".md", ".rst", "docs"),
}
_TEXT_HINTS = {
    "schema": ("migration", "schema", "column", "table", "index"),
    "api": ("api", "endpoint", "route", "request", "response", "http", "returns 2", "payload"),
    "ui": ("ui", "screen", "page", "card", "button", "layout", "mobile", "responsive",
           "viewport", "px", "design", "component"),
    "i18n": ("i18n", "translation", "locale", "japanese", "ja ", "english", "strings"),
    "docs": ("doc", "docs", "readme", "changelog"),
}


def surface_of_path(path: str) -> str:
    lowered = path.lower()
    segments = set(lowered.split("/"))
    for surface in MATCH_ORDER:
        hints = _PATH_HINTS[surface]
        if segments & set(hints) or any(lowered.endswith(hint) for hint in hints
                                        if hint.startswith(".")):
            return surface
    return "api"


def surface_of_text(text: str) -> str:
    lowered = text.lower()
    for surface in MATCH_ORDER:
        if any(hint in lowered for hint in _TEXT_HINTS[surface]):
            return surface
    return "api"


@dataclass
class Child:
    surface: str
    items: list[Item] = field(default_factory=list)
    companions: list[Companion] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.surface

    @property
    def empty(self) -> bool:
        return not self.items and not self.companions

    def describe(self) -> str:
        parts = []
        if self.items:
            parts.append(f"{len(self.items)} item(s)")
        if self.companions:
            parts.append(f"{len(self.companions)} inferred")
        return ", ".join(parts)


@dataclass
class Plan:
    children: list[Child] = field(default_factory=list)
    reason: str = ""

    @property
    def split(self) -> bool:
        return len(self.children) > 1

    def order(self) -> list[Child]:
        return sorted(self.children, key=lambda child: SURFACES.index(child.surface))


def plan(acceptance: Acceptance | None, companions: list[Companion] | None = None) -> Plan:
    """Group the work by surface. One surface, or nothing to group, means no split."""
    items = list(acceptance.items) if acceptance else []
    companions = list(companions or [])
    if not items and not companions:
        return Plan(reason="nothing to plan")

    buckets: dict[str, Child] = {}
    for item in items:
        surface = surface_of_text(item.text)
        buckets.setdefault(surface, Child(surface=surface)).items.append(item)
    for companion in companions:
        surface = surface_of_path(companion.expected)
        buckets.setdefault(surface, Child(surface=surface)).companions.append(companion)

    children = [child for child in buckets.values() if not child.empty]
    if len(children) <= 1:
        largest = max((len(child.items) for child in children), default=0)
        if largest <= SPLIT_THRESHOLD:
            return Plan(children=children,
                        reason="single surface — no split needed")
        return Plan(children=children,
                    reason=f"one surface but {largest} items — consider splitting by outcome")
    return Plan(children=children,
                reason=f"work crosses {len(children)} surfaces: "
                       + ", ".join(child.surface for child in
                                   sorted(children, key=lambda c: SURFACES.index(c.surface))))
