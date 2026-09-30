"""Action executor tests: the eight hands, refusals, validation."""

from pathlib import Path

import pytest

from lakra.control.policy import Action
from lakra.control.tasks import Task
from lakra.execution.browser.actions import BrowserActions
from lakra.execution.browser.sessions import BrowserSessions

FIXTURE = (Path(__file__).parent / "fixtures" / "courses.html").as_uri()


@pytest.fixture
def hands(tmp_path):
    s = BrowserSessions(tmp_path / "p")
    s.launch()
    h = BrowserActions(s)
    h.open(FIXTURE)
    yield h
    h.close_page()
    s.close()


def test_click_changes_state(hands):
    assert hands.page.locator("#status").inner_text() == "not clicked"
    r = hands.click("#toggle")
    assert r.ok
    assert hands.page.locator("#status").inner_text() == "clicked"


def test_type_fills_and_echoes(hands):
    r = hands.type("#echo\n---\nhello")
    assert r.ok
    assert hands.page.locator("#echo").input_value() == "hello"
    assert hands.page.locator("#echo-out").inner_text() == "hello"


def test_type_into_password_refused(hands):
    r = hands.type("#pwd\n---\nattack")
    assert not r.ok and "password" in r.error
    assert hands.page.locator("#pwd").input_value() == "s3cr3t-nope"


def test_press_enter_in_field(hands):
    hands.type("#echo\n---\nabc")
    r = hands.press("#echo\n---\nBackspace")
    assert r.ok
    assert hands.page.locator("#echo").input_value() == "ab"


def test_press_unknown_key_refused(hands):
    r = hands.press("#echo\n---\nFrobnicator")
    assert not r.ok and "allowlist" in r.error


def test_scroll_down_and_into_view(hands):
    r = hands.scroll("down:800")
    assert r.ok
    r = hands.scroll("into-view:#bottom-marker")
    assert r.ok
    assert hands.page.locator("#bottom-marker").is_visible()


def test_scroll_bad_form_refused(hands):
    assert not hands.scroll("sideways:100").ok


def test_wait_ms_and_selector(hands):
    assert hands.wait("ms:200").ok
    assert not hands.wait("ms:99999").ok
    assert hands.wait("selector:#delayed").ok  # appears after ~800 ms
    assert not hands.wait("selector:#never-here").ok


def test_missing_selector_fails_without_touching(hands):
    r = hands.click("#no-such-element")
    assert not r.ok and "no element matches" in r.error


def test_empty_selector_rejected(hands):
    assert not hands.click("").ok
    assert not hands.type("\n---\nx").ok


def test_submit_activates_control(hands):
    assert hands.page.locator("#status").inner_text() == "not clicked"
    r = hands.submit("#toggle")
    assert r.ok and "submitted #toggle" in r.output
    assert hands.page.locator("#status").inner_text() == "clicked"


def test_submit_missing_selector_fails_without_touching(hands):
    r = hands.submit("#no-such-element")
    assert not r.ok and "no element matches" in r.error
    assert hands.page.locator("#status").inner_text() == "not clicked"


def test_submit_empty_selector_rejected(hands):
    assert not hands.submit("").ok


def test_submit_dispatched_via_execute(hands):
    r = hands.execute(Action(kind="browser.submit", target="#toggle",
                             effect="consequential", task_id="t"),
                      Task.create("g"))
    assert r.ok
    assert hands.page.locator("#status").inner_text() == "clicked"


def test_unknown_kind_rejected(hands):
    r = hands.execute(Action(kind="browser.dance", target="x",
                             effect="reversible", task_id="t"),
                      Task.create("g"))
    assert not r.ok


def test_css_hit_records_no_fallback(hands):
    r = hands.click("#toggle")
    assert r.ok and "[fallback" not in r.output


def test_click_falls_back_to_text_rung(hands):
    # "Toggle status" matches no CSS rule but is the button's text.
    r = hands.click("Toggle status")
    assert r.ok and "[fallback: text]" in r.output
    assert hands.page.locator("#status").inner_text() == "clicked"


def test_click_falls_back_to_role_rung(hands):
    # "navigation" is neither CSS nor visible text here, but IS the
    # <nav> landmark's ARIA role.
    r = hands.click("navigation")
    assert r.ok and "[fallback: role]" in r.output


def test_total_miss_fails_exactly_as_before(hands):
    r = hands.click("#no-such-element")
    assert not r.ok and r.error == "no element matches '#no-such-element'"


def test_submit_via_text_fallback(hands):
    r = hands.submit("Send")  # CSS miss; the button reads "Send"
    assert r.ok and "[fallback: text]" in r.output
    assert hands.page.locator("#m-status").inner_text() == "submitted"


def test_check_ensures_state(hands):
    assert not hands.page.locator("#p-news").is_checked()
    r = hands.check("#p-news\n---\nchecked")
    assert r.ok and r.output == "checked #p-news"
    assert hands.page.locator("#p-news").is_checked()


def test_check_already_correct_is_noop_success(hands):
    hands.page.locator("#p-terms").check()
    r = hands.check("#p-terms\n---\nchecked")
    assert r.ok and r.output == "already checked #p-terms"
    assert hands.page.locator("#p-terms").is_checked()  # still checked


def test_uncheck_path(hands):
    hands.page.locator("#p-news").check()
    r = hands.check("#p-news\n---\nunchecked")
    assert r.ok and r.output == "unchecked #p-news"
    assert not hands.page.locator("#p-news").is_checked()


def test_check_radio_selects(hands):
    r = hands.check("#p-plan-pro\n---\nchecked")
    assert r.ok
    assert hands.page.locator("#p-plan-pro").is_checked()


def test_check_refusals(hands):
    assert not hands.check("#p-news").ok  # missing --- separator
    assert not hands.check("#p-news\n---\nyes").ok  # strict states only
    assert not hands.check("#p-news\n---\nChecked").ok
    r = hands.check("#no-such-box\n---\nchecked")
    assert not r.ok and "no element matches" in r.error
    before = hands.page.locator("#echo").input_value()
    r = hands.check("#echo\n---\nchecked")  # text input: uncheckable
    assert not r.ok and "not a checkable control" in r.error
    assert hands.page.locator("#echo").input_value() == before


def test_check_dispatched_via_execute(hands):
    r = hands.execute(Action(kind="browser.check",
                             target="#p-news\n---\nchecked",
                             effect="bounded-mutation", task_id="t"),
                      Task.create("g"))
    assert r.ok
    assert hands.page.locator("#p-news").is_checked()


def test_select_by_value(hands):
    r = hands.select("#p-country\n---\nng")
    assert r.ok and r.output == "selected ng in #p-country"
    assert hands.page.locator("#p-country").input_value() == "ng"


def test_select_by_label(hands):
    r = hands.select("#p-country\n---\nKenya")
    assert r.ok
    assert hands.page.locator("#p-country").input_value() == "ke"


def test_select_value_preferred_on_tie(hands):
    # "same" is both option A's value and option B's label: value wins,
    # so the selected label reads "First", not "same".
    r = hands.select("#p-tie\n---\nsame")
    assert r.ok
    assert hands.page.locator("#p-tie").input_value() == "same"
    assert hands.page.locator("#p-tie option:checked").inner_text() == \
        "First"


def test_select_refusals(hands):
    assert not hands.select("#p-country").ok  # missing --- separator
    assert not hands.select("#p-country\n---\n   ").ok  # empty option
    r = hands.select("#p-country\n---\nAtlantis")
    assert not r.ok and "no option matches" in r.error
    r = hands.select("#p-lang\n---\nEnglish")  # duplicated label
    assert not r.ok and "ambiguous option" in r.error
    before = hands.page.locator("#p-country").input_value()
    assert before == ""  # refusals above touched nothing
    r = hands.select("#echo\n---\nng")  # text input, not a select
    assert not r.ok and "not a select element" in r.error
    r = hands.select("#p-frozen\n---\nx")
    assert not r.ok and "disabled" in r.error
    r = hands.select("#p-multi\n---\na")
    assert not r.ok and "multi-select" in r.error
    r = hands.select("#no-such-select\n---\nng")
    assert not r.ok and "no element matches" in r.error
    assert hands.page.locator("#echo").input_value() == ""


def test_select_dispatched_via_execute(hands):
    r = hands.execute(Action(kind="browser.select",
                             target="#p-country\n---\nke",
                             effect="bounded-mutation", task_id="t"),
                      Task.create("g"))
    assert r.ok
    assert hands.page.locator("#p-country").input_value() == "ke"
