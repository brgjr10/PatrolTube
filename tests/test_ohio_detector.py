"""Ohio attribution false positives (PATROLTUBE-014).

`is_ohio_location` returned True for "Madison County, Wisconsin" and for a bare
"oh", so Wisconsin and Massachusetts footage reached the dashboard tagged
"Ohio PD (30%)" with a percentage the UI presents as authoritative.
"""

import pytest

from ohio_detector import (
    calculate_ohio_confidence,
    find_matched_ohio_cities,
    is_ohio_location,
    is_ohio_police_entity,
)

# Every one of these was measured as a false positive in the QA report.
FALSE_POSITIVES = [
    "Madison County, Wisconsin",
    "Warren County, Michigan",
    "butler county missouri",
    "Franklin County, Massachusetts",
    "oh",
    "Madison County Wisconsin school body cam footage",
    "Springfield, Illinois police bodycam",
    "Dayton, Tennessee dash cam pursuit",
]

TRUE_POSITIVES = [
    "Columbus police body cam footage",
    "Ohio State Highway Patrol dash cam",
    "Cleveland police chase bodycam",
    "Cuyahoga County Sheriff bodycam",
    "Springfield, OH police bodycam",
    "Franklin County, Ohio",
    "Madison County Ohio bodycam",
    "Union County Ohio bodycam",
    "OH DUI Dashcam",
    "Ohio update on the I-71 pursuit",
]


@pytest.mark.parametrize("text", FALSE_POSITIVES)
def test_other_states_are_not_attributed_to_ohio(text):
    assert is_ohio_location(text) is False
    assert find_matched_ohio_cities(text) == []


@pytest.mark.parametrize("text", TRUE_POSITIVES)
def test_real_ohio_footage_still_scores(text):
    assert is_ohio_location(text) is True
    ohio_score, cam_score, _reason, _cities = calculate_ohio_confidence(
        {"title": text, "description": "police body cam dash cam footage", "channel": "Ohio News"}
    )
    assert ohio_score > 0, f"{text!r} lost its Ohio attribution"
    assert cam_score == 100.0


@pytest.mark.parametrize("text", FALSE_POSITIVES)
def test_out_of_state_footage_scores_zero_ohio(text):
    ohio_score, _cam, _reason, _cities = calculate_ohio_confidence(
        {"title": text, "description": "bodycam", "channel": "Local News"}
    )
    assert ohio_score == 0.0, f"{text!r} still scores an Ohio match"


def test_a_mention_of_ohio_always_wins_over_a_neighbouring_state():
    # "Ohio" and "Kentucky" in the same blob is common in local news copy, so the
    # guard must not fire on a mere mention elsewhere in the description.
    text = "Trooper stops semi on I-71 in Ohio near the Kentucky line, bodycam released"
    assert is_ohio_location(text) is True
    assert calculate_ohio_confidence({"title": text, "description": "bodycam", "channel": "x"})[0] > 0


def test_county_entity_pattern_respects_the_state_attribution():
    assert is_ohio_police_entity("Montgomery County Sheriff bodycam") is True
    assert is_ohio_police_entity("Montgomery County, Maryland Sheriff bodycam") is False


def test_pd_is_matched_as_a_word_not_a_substring():
    # "Ohio" + any of police/sheriff/department/pd used to be a bare substring
    # test, so "updated" and "rapid" scored +70.
    assert is_ohio_police_entity("Ohio channel was updated with a rapid follow-up") is False
    assert is_ohio_police_entity("Ohio department of public safety") is True


def test_scoring_shape_is_unchanged():
    result = calculate_ohio_confidence(
        {"title": "Columbus police body cam", "description": "", "channel": "Ohio News"}
    )
    assert len(result) == 4
    ohio_score, cam_score, reason, cities = result
    assert isinstance(ohio_score, float) and isinstance(cam_score, float)
    assert isinstance(reason, str) and isinstance(cities, list)
