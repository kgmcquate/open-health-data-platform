"""The word list that keeps obscenities and slurs off published dashboards.

The half of this file that matters most is the first: health data's own
vocabulary and place names must pass. A filter that refuses "anal cancer
incidence" or "Hancock County" is worse than none, because the fix people reach
for is turning it off.
"""

from __future__ import annotations

from typing import Any

import pytest

from hub_api import wording
from ohdp_agent.dashboard import DashboardSpec

MUST_PASS = [
    "Anal cancer incidence by county",
    "Sexually transmitted infections, 2020-2025",
    "Breast and cervical cancer screening",
    "Rectal cancer survival",
    "Cocaine and methamphetamine overdose deaths",
    "Suicide rate per 100,000",
    "Assessment of hospital readmissions",
    "Classic assisted-living capacity",
    "Titus County, Texas",
    "Hancock, Hitchcock and Cocke counties",
    "Dickinson, Dickson and Dickey counties",
    "Cumulative COVID-19 cases",
    "Middlesex and Essex hospitals",
    "Therapist workforce",
    "Shiitake mushroom recalls",
    "Scunthorpe",
    "Cocktail of contraindicated drugs",
    "Class of 2014",
    "H1N1 and H5N1 activity",
    "455 page views",
    "Washita County, Oklahoma",
    "Event watcher",
]

MUST_BLOCK = [
    ("This data is shit", "shit"),
    ("Fuuuck these numbers", "fuuuck"),
    ("sh1t chart", "sh1t"),
    ("What a motherfucker", "motherfucker"),
    ("Kiss my ass", "ass"),
    ("Dickhead county", "dickhead"),
    ("Hancocks", "hancocks"),
    ("a$$ chart", "a$$"),
    ("Twat", "twat"),
]


@pytest.mark.parametrize("text", MUST_PASS)
def test_health_vocabulary_and_place_names_pass(text: str) -> None:
    assert wording.blocked_word(text) is None


@pytest.mark.parametrize(("text", "word"), MUST_BLOCK)
def test_obscenities_are_caught_as_written(text: str, word: str) -> None:
    assert wording.blocked_word(text) == word


def test_every_allowed_word_overrides_a_denial() -> None:
    """An allowlist entry exists to rescue a word the deny side would refuse.
    One that rescues nothing is dead weight, or a typo of the word meant."""
    original = wording.ALLOWED
    try:
        wording.ALLOWED = frozenset()
        unneeded = sorted(word for word in original if wording.blocked_word(word) is None)
    finally:
        wording.ALLOWED = original
    assert unneeded == []


SPEC: dict[str, Any] = {
    "name": "ed-visits",
    "title": "ED visit share",
    "query": {"measures": ["ed_visits.avg_percent"]},
    "vega_lite": {
        "mark": "bar",
        "encoding": {"y": {"field": "ed_visits.avg_percent", "type": "quantitative"}},
    },
}


def test_a_clean_spec_passes() -> None:
    wording.check(DashboardSpec.model_validate(SPEC))


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"name": "shit-chart"}, "name"),
        ({"title": "Bullshit"}, "title"),
        ({"description": "A fucking chart"}, "description"),
        ({"caption": "bitches"}, "caption"),
        (
            {
                "vega_lite": {
                    "mark": "bar",
                    "encoding": {
                        "y": {
                            "field": "ed_visits.avg_percent",
                            "type": "quantitative",
                            "axis": {"title": "Shitty percent"},
                        }
                    },
                }
            },
            "vega_lite",
        ),
    ],
)
def test_every_field_a_visitor_reads_is_checked(overrides: dict[str, Any], field: str) -> None:
    spec = DashboardSpec.model_validate({**SPEC, **overrides})

    with pytest.raises(wording.BlockedWording) as raised:
        wording.check(spec)

    assert raised.value.field == field
