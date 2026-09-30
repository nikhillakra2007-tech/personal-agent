"""Planner tests: template dispatch, refusal, shapes, determinism."""

import pytest

from lakra.control.planner import Plan, Planner, UnknownGoalError
from lakra.control.tasks import Task
from lakra.execution.browser.verification import PREDICATES


def task(goal):
    return Task.create(goal, allowed_tools=["browser"],
                       allowed_domains=["x.example"])


URL = "file:///courses.html"


def test_list_template():
    plan = Planner().plan(task("List the pending courses"),
                          {"url": URL, "expect_text": "pending"})
    assert isinstance(plan, Plan) and len(plan.steps) == 2
    assert plan.steps[0].action.kind == "browser.navigate"
    assert plan.steps[1].action.kind == "browser.snapshot"
    assert all(s.rationale for s in plan.steps)


def test_click_template():
    plan = Planner().plan(task("Click the toggle button"),
                          {"selector": "#toggle", "expect_text": "clicked"})
    assert len(plan.steps) == 1
    assert plan.steps[0].action.target == "#toggle"


def test_fill_submit_ends_at_l3_gate():
    plan = Planner().plan(
        task("Submit the assignment form"),
        {"url": URL, "selector": "#name", "text": "Ann",
         "submit_selector": "#send"})
    kinds = [s.action.kind for s in plan.steps]
    assert kinds[-1] == "browser.submit"
    assert "approval gate" in plan.steps[-1].rationale
    assert plan.steps[-1].max_retries == 0


def test_unknown_goal_refused():
    with pytest.raises(UnknownGoalError):
        Planner().plan(task("Transcribe this meeting"), {})


def test_missing_hints_refused():
    with pytest.raises(UnknownGoalError):
        Planner().plan(task("List the courses"), {})  # no url/text


def test_predicates_well_formed():
    plan = Planner().plan(task("List the courses"),
                          {"url": URL, "expect_text": "x"})
    for step in plan.steps:
        assert step.expect.kind in PREDICATES
        assert step.expect.target


def test_deterministic_including_rationales():
    kw = {"url": URL, "expect_text": "x"}
    a = Planner().plan(task("List the courses"), dict(kw))
    b = Planner().plan(task("List the courses"), dict(kw))
    assert [(s.action.kind, s.action.target, s.rationale) for s in a.steps] == \
           [(s.action.kind, s.action.target, s.rationale) for s in b.steps]


def test_no_model_calls_in_planner():
    # Amendment: v0 is fully deterministic — the planner source must not
    # reference the model layer at all (checked textually, not via imports
    # which other tests legitimately populate).
    import lakra.control.planner as mod
    src = open(mod.__file__).read()
    assert "models." not in src and "complete(" not in src
    assert "ollama" not in src.lower()


MULTI = {"url": URL, "submit_selector": "#m-submit", "fields": [
    {"selector": "#m-name", "text": "Ada"},
    {"selector": "#m-city", "text": "Lagos"},
    {"selector": "#m-zip", "text": "10001"},
]}


def test_fill_multi_template_shape_and_gate():
    plan = Planner().plan(task("Submit the address form"), dict(MULTI))
    kinds = [s.action.kind for s in plan.steps]
    assert kinds == ["browser.navigate", "browser.type", "browser.type",
                     "browser.type", "browser.submit"]
    assert plan.steps[1].action.target == "#m-name\n---\nAda"
    assert plan.steps[3].action.target == "#m-zip\n---\n10001"
    assert all(s.expect.kind == "element_visible"
               for s in plan.steps[1:4])
    assert "approval gate" in plan.steps[-1].rationale
    assert plan.steps[-1].max_retries == 0


def test_fields_present_selects_multi_over_single():
    hints = dict(MULTI)
    hints.update({"selector": "#name", "text": "Ann"})
    plan = Planner().plan(task("Submit the address form"), hints)
    assert len(plan.steps) == 5  # multi wins (documented tie-break)
    assert plan.steps[1].action.target == "#m-name\n---\nAda"


def test_single_field_byte_identical_without_fields():
    a = Planner().plan(
        task("Submit the assignment form"),
        {"url": URL, "selector": "#name", "text": "Ann",
         "submit_selector": "#send"})
    assert [s.action.kind for s in a.steps] == \
        ["browser.navigate", "browser.type", "browser.submit"]


def test_fields_cap_and_shape_refused():
    big = {"url": URL, "submit_selector": "#s",
           "fields": [{"selector": f"#f{i}", "text": "x"}
                      for i in range(9)]}
    with pytest.raises(UnknownGoalError):
        Planner().plan(task("Submit the big form"), big)
    for bad in ([],
                [{"selector": "#a"}],
                [{"selector": "", "text": "x"}],
                [{"selector": "#a", "text": ""}],
                [{"selector": "#a", "text": "x", "extra": 1}],
                [{"selector": "#a", "text": "my password is x"}],
                "not-a-list"):
        with pytest.raises(UnknownGoalError):
            Planner().plan(task("Submit the bad form"),
                           {"url": URL, "submit_selector": "#s",
                            "fields": bad})


MIXED = {"url": URL, "submit_selector": "#m-submit", "fields": [
    {"selector": "#m-name", "text": "Ada"},
    {"selector": "#p-news", "checked": True},
    {"selector": "#p-terms", "checked": False},
]}


def test_fill_multi_mixed_text_and_checks():
    plan = Planner().plan(task("Submit the preferences form"), dict(MIXED))
    kinds = [s.action.kind for s in plan.steps]
    assert kinds == ["browser.navigate", "browser.type", "browser.check",
                     "browser.check", "browser.submit"]
    assert plan.steps[1].action.target == "#m-name\n---\nAda"
    assert plan.steps[2].action.target == "#p-news\n---\nchecked"
    assert plan.steps[2].expect.kind == "element_checked"
    assert plan.steps[3].action.target == "#p-terms\n---\nunchecked"
    assert plan.steps[3].expect.kind == "element_unchecked"
    assert "approval gate" in plan.steps[-1].rationale
    assert plan.steps[-1].max_retries == 0


def test_checked_is_strict_boolean():
    for bad_checked in (1, 0, "true", "false", "checked", None, ["x"]):
        with pytest.raises(UnknownGoalError):
            Planner().plan(
                task("Submit the preferences form"),
                {"url": URL, "submit_selector": "#s", "fields": [
                    {"selector": "#p-news", "checked": bad_checked}]})
    for bad_keys in ([{"selector": "#p-news", "checked": True,
                       "text": "x"}],
                     [{"selector": "#p-news"}],
                     [{"checked": True}]):
        with pytest.raises(UnknownGoalError):
            Planner().plan(
                task("Submit the preferences form"),
                {"url": URL, "submit_selector": "#s",
                 "fields": bad_keys})


FULL = {"url": URL, "submit_selector": "#m-submit", "fields": [
    {"selector": "#m-name", "text": "Ada"},
    {"selector": "#p-news", "checked": True},
    {"selector": "#p-country", "select": "ng"},
]}


def test_fill_multi_mixed_all_three_entry_shapes():
    plan = Planner().plan(task("Submit the full form"), dict(FULL))
    kinds = [s.action.kind for s in plan.steps]
    assert kinds == ["browser.navigate", "browser.type", "browser.check",
                     "browser.select", "browser.submit"]
    sel = plan.steps[3]
    assert sel.action.target == "#p-country\n---\nng"
    assert sel.expect.kind == "element_visible"
    assert sel.expect.target == "#p-country"
    assert "exact option match" in sel.rationale
    assert "approval gate" in plan.steps[-1].rationale


def test_select_entry_strictness():
    for bad in ("", "   ", 0, None, ["ng"], "my password is ng"):
        with pytest.raises(UnknownGoalError):
            Planner().plan(
                task("Submit the full form"),
                {"url": URL, "submit_selector": "#s", "fields": [
                    {"selector": "#p-country", "select": bad}]})
    for bad_keys in ([{"selector": "#p-country", "select": "ng",
                       "text": "x"}],
                     [{"selector": "#p-country"}],
                     [{"select": "ng"}]):
        with pytest.raises(UnknownGoalError):
            Planner().plan(
                task("Submit the full form"),
                {"url": URL, "submit_selector": "#s",
                 "fields": bad_keys})


FOLLOW = {"url": URL, "link_text": "details", "expect_text": "eigenvalues"}


def test_follow_link_template_shape():
    plan = Planner().plan(task("Show details of Linear Algebra"),
                          dict(FOLLOW))
    kinds = [s.action.kind for s in plan.steps]
    assert kinds == ["browser.navigate", "browser.snapshot",
                     "browser.click", "browser.snapshot"]
    assert plan.steps[0].expect.kind == "url_is"
    assert plan.steps[1].action.kind == "browser.snapshot"
    assert plan.steps[1].expect.kind == "text_contains"
    assert plan.steps[1].expect.target == "details"
    assert plan.steps[2].action.target == "details"
    assert plan.steps[2].expect.kind == "text_contains"
    assert plan.steps[2].expect.target == "eigenvalues"
    assert plan.steps[3].expect.target == "eigenvalues"
    assert all(s.rationale for s in plan.steps)


def test_follow_link_needs_all_hints():
    for hints in ({"url": URL, "link_text": "details"},
                  {"url": URL, "expect_text": "x"},
                  {"link_text": "details", "expect_text": "x"},
                  {}):
        with pytest.raises(UnknownGoalError):
            Planner().plan(task("Show details of Linear Algebra"), hints)


def test_existing_goals_do_not_reroute_to_follow():
    observe = Planner().plan(task("List the pending courses"),
                             {"url": URL, "expect_text": "pending"})
    assert [s.action.kind for s in observe.steps] == \
        ["browser.navigate", "browser.snapshot"]
    click = Planner().plan(task("Click the toggle button"),
                           {"selector": "#toggle",
                            "expect_text": "clicked"})
    assert [s.action.kind for s in click.steps] == ["browser.click"]
    fill = Planner().plan(
        task("Submit the assignment form"),
        {"url": URL, "selector": "#name", "text": "Ann",
         "submit_selector": "#send"})
    assert [s.action.kind for s in fill.steps] == \
        ["browser.navigate", "browser.type", "browser.submit"]
