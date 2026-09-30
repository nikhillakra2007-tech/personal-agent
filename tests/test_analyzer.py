"""Analyzer tests: extraction, refusal, determinism, no model use."""

import pytest

from lakra.control.analyzer import (
    analyze,
    ground_fields,
    ground_link,
    ground_match,
    grounded_follow,
)
from lakra.control.planner import UnknownGoalError


RECORDS = [("Alpha record", "loop-alpha.html"),
           ("Beta record", "loop-beta.html"),
           ("Gamma record", "loop-gamma.html")]


def test_url_and_expect_text_extraction():
    hints = analyze("List my pending courses at file:///c.html")
    assert hints["url"] == "file:///c.html"
    assert hints["expect_text"] == "pending"
    assert hints["intent"] == "observe"


def test_https_url_extraction_and_trailing_punctuation():
    hints = analyze("Check https://example.com/docs (now).")
    assert hints["url"] == "https://example.com/docs"


def test_earliest_longest_content_word():
    assert analyze("List my pending courses")["expect_text"] == "pending"
    assert analyze("Show thermodynamics notes")["expect_text"] == \
        "thermodynamics"


def test_click_and_submit_intents():
    assert analyze("Click the toggle button on the courses page")[
        "intent"] == "click"
    assert analyze("Submit the assignment form")["intent"] == "fill-submit"
    # A bare click names no target the planner could use: refusal is honest.
    with pytest.raises(UnknownGoalError):
        analyze("Click the toggle")


def test_empty_and_unparseable_refused():
    with pytest.raises(UnknownGoalError):
        analyze("")
    with pytest.raises(UnknownGoalError):
        analyze("  ")
    with pytest.raises(UnknownGoalError):
        analyze("Um")
    with pytest.raises(UnknownGoalError):
        analyze("the and or")


def test_credential_laden_goals_refused():
    with pytest.raises(UnknownGoalError):
        analyze("Type my password into the form")
    with pytest.raises(UnknownGoalError):
        analyze("Enter the api_key here")


def test_secretary_passes_word_boundary():
    hints = analyze("List the secretary pool directory")
    assert hints["expect_text"] == "secretary"


def test_deterministic():
    g = "List my pending courses at file:///c.html please"
    assert analyze(g) == analyze(g)


def test_no_model_calls_in_analyzer():
    import lakra.control.analyzer as mod
    src = open(mod.__file__).read()
    assert "models." not in src and "complete(" not in src
    assert "ollama" not in src.lower()


def test_ground_link_unique_winner():
    assert ground_link("Follow the Beta record", RECORDS) == "Beta record"
    assert ground_link("Follow the Beta record", RECORDS) == \
        ground_link("Follow the Beta record", RECORDS)  # deterministic


def test_ground_link_tie_refused():
    with pytest.raises(UnknownGoalError):
        ground_link("Show record details", RECORDS)  # all score 1
    dupes = [("English", "a.html"), ("English", "b.html")]
    with pytest.raises(UnknownGoalError):
        ground_link("Follow English", dupes)


def test_ground_link_zero_overlap_refused():
    with pytest.raises(UnknownGoalError):
        ground_link("Show syllabus", RECORDS)


def test_ground_link_empty_and_malformed_refused():
    with pytest.raises(UnknownGoalError):
        ground_link("Follow Beta record", [])
    with pytest.raises(UnknownGoalError):
        ground_link("Follow Beta record", [("Beta record",)])
    with pytest.raises(UnknownGoalError):
        ground_link("Follow Beta record", [(None, "b.html")])


def test_ground_link_stopword_only_and_credentials_refused():
    with pytest.raises(UnknownGoalError):
        ground_link("Show the", RECORDS)
    with pytest.raises(UnknownGoalError):
        ground_link("", RECORDS)
    with pytest.raises(UnknownGoalError):
        ground_link("Type my password into Beta record", RECORDS)


def test_grounded_follow_returns_validated_hints():
    hints = grounded_follow("Follow the Beta record", "file:///l.html",
                            RECORDS, "detail record: Beta")
    assert hints == {"url": "file:///l.html", "link_text": "Beta record",
                     "expect_text": "detail record: Beta"}


def test_grounded_follow_validates_body_expect():
    for bad in ("", "   ", None, 42, "my password is ng"):
        with pytest.raises(UnknownGoalError):
            grounded_follow("Follow the Beta record", "file:///l.html",
                            RECORDS, bad)
    with pytest.raises(UnknownGoalError):  # tie propagates
        grounded_follow("Show record details", "file:///l.html",
                        RECORDS, "detail record")


CONTROLS = [("Full name", "text", "#m-name"),
            ("City", "text", "#m-city"),
            ("ZIP", "text", "#m-zip"),
            ("News", "check", "#p-news"),
            ("Country", "select", "#p-country"),
            ("Home address", "text", "#p-home"),
            ("Work address", "text", "#p-work")]


def test_ground_fields_typed_bindings():
    fields = ground_fields({"name": {"text": "Ada"},
                            "news": {"checked": True},
                            "country": {"select": "ng"}}, CONTROLS)
    assert fields == [{"selector": "#m-name", "text": "Ada"},
                      {"selector": "#p-news", "checked": True},
                      {"selector": "#p-country", "select": "ng"}]


def test_ground_fields_values_travel_untouched():
    fields = ground_fields({"zip": {"text": "10001"}}, CONTROLS)
    assert fields == [{"selector": "#m-zip", "text": "10001"}]


def test_ground_fields_ambiguity_refused():
    with pytest.raises(UnknownGoalError):
        ground_fields({"address": {"text": "x"}}, CONTROLS)


def test_ground_fields_zero_and_kind_mismatch_refused():
    with pytest.raises(UnknownGoalError):
        ground_fields({"phone": {"text": "x"}}, CONTROLS)
    no_checks = [c for c in CONTROLS if c[1] != "check"]
    with pytest.raises(UnknownGoalError):
        ground_fields({"news": {"checked": True}}, no_checks)
    with pytest.raises(UnknownGoalError):
        ground_fields({"name": {"checked": True}}, CONTROLS)


def test_ground_fields_strict_shapes_and_secrets():
    for bad_slots in ({"name": "Ada"},
                      {"name": {"text": ""}},
                      {"name": {"checked": 1}},
                      {"name": {"text": "x", "checked": True}},
                      {"name": {"select": "my password is ng"}},
                      {"": {"text": "x"}},
                      {"my password": {"text": "x"}}):
        with pytest.raises(UnknownGoalError):
            ground_fields(bad_slots, CONTROLS)
    with pytest.raises(UnknownGoalError):
        ground_fields({"name": {"text": "Ada"}}, [])
    with pytest.raises(UnknownGoalError):
        ground_fields({"name": {"text": "Ada"}},
                      [("Full name", "bogus", "#m-name")])
    with pytest.raises(UnknownGoalError):
        ground_fields({}, CONTROLS)


def test_ground_match_plurality_winner():
    links = ["Alpha record", "Beta record", "Gamma record"]
    assert ground_match("Follow every batch record", links) == "record"
    assert ground_match("Follow every batch records", links) == "record"
    assert ground_match("Follow every batch record", links) == \
        ground_match("Follow every batch record", links)


def test_ground_match_single_target_refused():
    # "Beta" names one link; "record" spans three — plurality wins, so
    # this still grounds (single-target refusal needs NO plural word).
    assert ground_match("Follow the Beta record",
                        ["Alpha record", "Beta record",
                         "Gamma record"]) == "record"
    with pytest.raises(UnknownGoalError):
        ground_match("Follow the Only record", ["Only record"])


def test_ground_match_tie_refused():
    links = ["red apple", "red pear", "green apple"]
    with pytest.raises(UnknownGoalError):
        ground_match("Follow red apple green pear", links)


def test_ground_match_empty_malformed_stopword_refused():
    links = ["Alpha record", "Beta record"]
    with pytest.raises(UnknownGoalError):
        ground_match("Follow batch records", [])
    with pytest.raises(UnknownGoalError):
        ground_match("Follow batch records", ["Alpha record", None])
    with pytest.raises(UnknownGoalError):
        ground_match("Show the", links)
    with pytest.raises(UnknownGoalError):
        ground_match("", links)
    with pytest.raises(UnknownGoalError):
        ground_match("Type my password into batch records", links)
    with pytest.raises(UnknownGoalError):
        ground_match("Show syllabus", links)  # zero span
