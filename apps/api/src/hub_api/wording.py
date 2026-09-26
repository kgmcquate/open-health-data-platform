"""Keeping obscenities and slurs off the public Dashboards page.

A published dashboard's text is the one part of it nothing else validates
(ADR-0028): the numbers are Cube's, but the `name`, `title`, `description`,
`caption` and every string in the Vega-Lite spec — axis titles, `text` marks,
tooltip titles — were typed by a model or a person. `check` runs on every save
(`hub_api.library.save`), so the chat agent and the builder are held to the
same rule.

**Why a word list and not a classifier.** This is a health-data platform, and
the vocabulary a generic profanity filter trips on is its subject matter:
"anal cancer", "sexually transmitted", "breast", "rectal", "cocaine",
"suicide". So the list is deliberately narrow — obscenities and slurs with no
clinical sense — and it is matched on whole tokens, never on raw substrings of
the text. Adding a word here means checking it cannot appear in a real health
dashboard title; `tests/test_wording.py` holds the phrases that must pass.

Two tiers, and the allowlist that makes the second one workable:

  - `DENIED_WORDS` match a whole token only. These are short words that are
    innocent inside longer ones ("ass" in "assessment", "tits" in "Titus").
  - `DENIED_ROOTS` match anywhere inside a token, so "motherfucker" and
    "dickhead" are caught without listing every compound.
  - `ALLOWED` exempts a token from both. It exists because roots hit real
    place names that health data is full of — Hancock, Hitchcock and Cocke
    counties, Dickinson, Dickson and Dickey counties, Scunthorpe. When a
    legitimate dashboard is refused, the fix is to add the word here.

Tokens are lowercased and, if they hold a letter at all, common character
swaps are undone (`0`→`o`, `@`→`a`, `$`→`s`, ...) — a bare number is left
alone, or "455" would read as "ass". Roots are also tried with repeated letters
collapsed, so "sh1t" and "fuuuck" do not slip through. Spaced-out letters
("f u c k") do; a determined author can always get past a word list, which is
what votes (`HIDE_AT_SCORE`) are still for.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ohdp_agent.dashboard import DashboardSpec

DENIED_WORDS: frozenset[str] = frozenset(
    {
        "arse",
        "ass",
        "fag",
        "fags",
        "kike",
        "kikes",
        "retard",
        "retards",
        "spic",
        "spics",
        "tits",
        "tranny",
        "trannies",
        # A root would also hit "eventwatcher" and the like.
        "twat",
        "twats",
        "wetback",
        "wetbacks",
    }
)

DENIED_ROOTS: tuple[str, ...] = (
    "asshole",
    "bastard",
    "bitch",
    "cock",
    "cunt",
    "dick",
    "faggot",
    "fuck",
    "nigga",
    "nigger",
    "shit",
    "whore",
)

ALLOWED: frozenset[str] = frozenset(
    {
        # Places — county and town names that turn up in health data.
        "babcock",
        "cocke",
        "cockrell",
        "dickens",
        "dickenson",
        "dickey",
        "dickinson",
        "dickson",
        "hancock",
        "hitchcock",
        "scunthorpe",
        "washita",
        "woodcock",
        # Ordinary words.
        "cockpit",
        "cockroach",
        "cockroaches",
        "cocktail",
        "cocktails",
        "peacock",
        "shiitake",
        "shitake",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9@$]+")
_SWAPS = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_REPEATS_RE = re.compile(r"(.)\1+")
_LETTER_RE = re.compile(r"[a-z]")


class BlockedWording(ValueError):
    """A dashboard's text holds a word that may not go on the public page.
    Carries which field and which word, for the message the caller shows."""

    def __init__(self, field: str, word: str) -> None:
        super().__init__(f"{field} contains {word!r}")
        self.field = field
        self.word = word


def blocked_word(text: str) -> str | None:
    """The first token of `text` that is denied, as it was written, or `None`."""
    for match in _TOKEN_RE.finditer(text.lower()):
        written = match.group()
        token = written.translate(_SWAPS) if _LETTER_RE.search(written) else written
        if token in ALLOWED:
            continue
        if token in DENIED_WORDS:
            return written
        collapsed = _REPEATS_RE.sub(r"\1", token)
        if any(root in token or root in collapsed for root in DENIED_ROOTS):
            return written
    return None


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in _strings(value)]
    if isinstance(node, list):
        return [text for item in node for text in _strings(item)]
    return []


def check(spec: DashboardSpec) -> None:
    """Raise `BlockedWording` if any text a visitor would read is denied."""
    fields: list[tuple[str, str]] = [
        ("name", spec.name),
        ("title", spec.title),
        ("description", spec.description or ""),
        ("caption", spec.caption or ""),
    ]
    fields += [("vega_lite", text) for text in _strings(spec.vega_lite)]
    for field, text in fields:
        word = blocked_word(text)
        if word is not None:
            raise BlockedWording(field, word)
