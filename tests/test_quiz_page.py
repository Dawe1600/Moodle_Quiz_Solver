from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

from moodle_quiz_solver.domain import Answer, Question
from moodle_quiz_solver.exceptions import QuizPageError
from moodle_quiz_solver.quiz_page import PlaywrightQuizPage, open_quiz_page


@pytest.fixture(scope="module")
def browser_page() -> Iterator[Page]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        yield page
        context.close()
        browser.close()


def test_extract_and_apply_radio_with_mathjax_overlay(browser_page: Page) -> None:
    browser_page.set_content(
        """
        <div class="que multichoice">
          <div class="qtext">What is 2 + 2?</div>
          <div class="answer">
            <div class="r0">
              <input type="radio" name="q1" id="q1_a0" value="0" />
              <label for="q1_a0">
                <span class="MathJax" style="position:relative; z-index:10; display:inline-block; padding:8px;">3</span>
              </label>
            </div>
            <div class="r1">
              <input type="radio" name="q1" id="q1_a1" value="1" />
              <label for="q1_a1">
                <span class="MathJax" style="position:relative; z-index:10; display:inline-block; padding:8px;">4</span>
              </label>
            </div>
          </div>
        </div>
        """
    )
    quiz_page = PlaywrightQuizPage(browser_page)

    questions = quiz_page.extract_questions()

    assert len(questions) == 1
    question = questions[0]
    assert isinstance(question, Question)
    assert question.index == 0
    assert "What is 2 + 2?" in question.prompt
    assert question.question_type == "radio"
    assert question.options == ("3", "4")
    assert question.slot_count == 1

    quiz_page.apply_answer(
        question,
        Answer(answer_type="radio", reasoning="2+2=4", selected_indices=(1,)),
    )

    assert not browser_page.locator("#q1_a0").is_checked()
    assert browser_page.locator("#q1_a1").is_checked()


def test_extract_and_apply_checkbox_keeps_input_label_indices_aligned(
    browser_page: Page,
) -> None:
    browser_page.set_content(
        """
        <div class="que multichoice">
          <div class="qtext">Select prime numbers:</div>
          <div class="answer">
            <label class="feedback-only">Instructional note</label>
            <div class="r0">
              <input type="checkbox" id="q2_a0" aria-labelledby="q2_lbl0" />
              <div data-region="answer-label" id="q2_lbl0">2</div>
            </div>
            <div class="r1">
              <input type="checkbox" id="q2_a1" aria-labelledby="q2_lbl1" />
              <div data-region="answer-label" id="q2_lbl1">4</div>
            </div>
            <div class="r2">
              <input type="checkbox" id="q2_a2" aria-labelledby="q2_lbl2" />
              <div data-region="answer-label" id="q2_lbl2">5</div>
            </div>
          </div>
        </div>
        """
    )
    quiz_page = PlaywrightQuizPage(browser_page)

    questions = quiz_page.extract_questions()

    assert len(questions) == 1
    question = questions[0]
    assert question.question_type == "checkbox"
    assert question.options == ("2", "4", "5")

    quiz_page.apply_answer(
        question,
        Answer(answer_type="checkbox", reasoning="2 and 5 are prime", selected_indices=(0, 2)),
    )

    assert browser_page.locator("#q2_a0").is_checked()
    assert not browser_page.locator("#q2_a1").is_checked()
    assert browser_page.locator("#q2_a2").is_checked()


def test_extract_and_apply_select_filters_placeholders_structurally_first(
    browser_page: Page,
) -> None:
    browser_page.set_content(
        """
        <div class="que match">
          <div class="qtext">Match each term:</div>
          <table class="answer">
            <tr>
              <td class="text">First item</td>
              <td class="control">
                <select id="sel1">
                  <option value="">Choose...</option>
                  <option value="p" disabled>Disabled placeholder</option>
                  <option value="x">Wybierz...</option>
                  <option value="0">Zero</option>
                  <option value="1">One</option>
                </select>
              </td>
            </tr>
            <tr>
              <td class="text">Second item</td>
              <td class="control">
                <select id="sel2">
                  <option value="">Choose...</option>
                  <option value="0">Zero</option>
                  <option value="1">One</option>
                </select>
              </td>
            </tr>
          </table>
        </div>
        """
    )
    quiz_page = PlaywrightQuizPage(browser_page)

    questions = quiz_page.extract_questions()

    assert len(questions) == 1
    question = questions[0]
    assert question.question_type == "select"
    assert question.slot_count == 2
    assert "First item" in question.prompt
    assert "Second item" in question.prompt
    assert question.options == ("Zero", "One")

    quiz_page.apply_answer(
        question,
        Answer(answer_type="select", reasoning="match", selected_options=("One", "Zero")),
    )

    assert browser_page.locator("#sel1").input_value() == "1"
    assert browser_page.locator("#sel2").input_value() == "0"


def test_extract_and_apply_multi_slot_text_ignores_hidden_inputs(
    browser_page: Page,
) -> None:
    browser_page.set_content(
        """
        <div class="que shortanswer">
          <div class="qtext">Fill in both blanks:</div>
          <div class="answer">
            <input type="hidden" name="secret" value="ignored" />
            <input type="text" id="hidden_css" style="display:none" />
            <input type="text" id="slot1" />
            <textarea id="slot2"></textarea>
            <input type="submit" value="Check" />
          </div>
        </div>
        """
    )
    quiz_page = PlaywrightQuizPage(browser_page)

    questions = quiz_page.extract_questions()

    assert len(questions) == 1
    question = questions[0]
    assert question.question_type == "text"
    assert question.slot_count == 2

    quiz_page.apply_answer(
        question,
        Answer(answer_type="text", reasoning="two blanks", text_answers=("alpha", "beta")),
    )

    assert browser_page.locator("#slot1").input_value() == "alpha"
    assert browser_page.locator("#slot2").input_value() == "beta"
    assert browser_page.locator("#hidden_css").input_value() == ""


_TINY_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_extract_captures_question_option_and_matching_row_images_in_memory(
    browser_page: Page, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    browser_page.set_content(
        f"""
        <div class="que multichoice">
          <div class="qtext">
            Identify this diagram:
            <img src="{_TINY_PNG_DATA_URI}" width="16" height="16" />
          </div>
          <div class="answer">
            <div class="r0">
              <input type="radio" name="qimg" id="qimg_0" />
              <label for="qimg_0">Option A <img src="{_TINY_PNG_DATA_URI}" width="16" height="16" /></label>
            </div>
          </div>
        </div>
        <div class="que match">
          <div class="qtext">Match the icon:</div>
          <table class="answer">
            <tr>
              <td class="text">Row 1 <img src="{_TINY_PNG_DATA_URI}" width="16" height="16" /></td>
              <td class="control">
                <select>
                  <option value="">Choose...</option>
                  <option value="1">Icon X</option>
                </select>
              </td>
            </tr>
          </table>
        </div>
        """
    )
    quiz_page = PlaywrightQuizPage(browser_page)

    questions = quiz_page.extract_questions()

    assert len(questions) == 2
    choice_q, match_q = questions

    assert len(choice_q.image_bytes) == 2
    assert all(img.startswith(b"\x89PNG\r\n\x1a\n") for img in choice_q.image_bytes)

    assert len(match_q.image_bytes) == 1
    assert match_q.image_bytes[0].startswith(b"\x89PNG\r\n\x1a\n")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("html", "expected_outcome", "expect_clicked"),
    [
        (
            '<form id="responseform"><input type="submit" id="mod_quiz-next-nav" name="next" '
            'value="Next page" onclick="window.__clicked=true; return false;" /></form>',
            "next",
            True,
        ),
        (
            '<div class="submitbtns"><input type="submit" value="Następna strona" '
            'onclick="window.__clicked=true; return false;" /></div>',
            "next",
            True,
        ),
        (
            '<form id="responseform"><input type="submit" name="finishattempt" '
            'value="Finish attempt" onclick="window.__clicked=true; return false;" /></form>',
            "finished",
            False,
        ),
        (
            '<form id="responseform"><input type="submit" id="mod_quiz-next-nav" name="next" '
            'value="Zapisz podejście" onclick="window.__clicked=true; return false;" /></form>',
            "finished",
            False,
        ),
        (
            '<form id="responseform"><input type="submit" id="mod_quiz-next-nav" name="next" '
            'value="Finish attempt ..." onclick="window.__clicked=true; return false;" /></form>',
            "finished",
            False,
        ),
        (
            '<form id="responseform"><input type="hidden" name="next" value="1" /></form>',
            "missing",
            False,
        ),
        (
            '<div class="que"><div class="qtext">No nav here</div></div>',
            "missing",
            False,
        ),
        (
            '<aside class="block_quiz_navblock">'
            '<a class="endtestlink aalink" href="#summary">Zapisz podejście ...</a>'
            "</aside>"
            '<form id="responseform"><div class="submitbtns">'
            '<input type="submit" name="previous" value="Poprzednia strona" '
            'class="mod_quiz-prev-nav btn btn-secondary" id="mod_quiz-prev-nav" />'
            '<input type="submit" name="next" value="Następna strona" '
            'class="mod_quiz-next-nav btn btn-primary" id="mod_quiz-next-nav" '
            'onclick="window.__clicked=true; return false;" />'
            "</div></form>",
            "next",
            True,
        ),
        (
            '<aside><button name="finishattempt">Zapisz podejście ...</button></aside>'
            '<form id="responseform"><div class="submitbtns">'
            '<input type="submit" name="next" value="Następna strona" '
            'class="mod_quiz-next-nav btn btn-primary" id="mod_quiz-next-nav" '
            'onclick="window.__clicked=true; return false;" />'
            "</div></form>",
            "next",
            True,
        ),
        (
            '<aside class="block_quiz_navblock">'
            '<a class="endtestlink aalink" href="#summary">Zapisz podejście ...</a>'
            "</aside>"
            '<form id="responseform"><div class="submitbtns">'
            '<input type="submit" name="previous" value="Poprzednia strona" '
            'class="mod_quiz-prev-nav btn btn-secondary" id="mod_quiz-prev-nav" />'
            '<input type="submit" name="next" value="Zapisz podejście ..." '
            'class="mod_quiz-next-nav btn btn-primary" id="mod_quiz-next-nav" '
            'onclick="window.__clicked=true; return false;" />'
            "</div></form>",
            "finished",
            False,
        ),
    ],
)
def test_advance_checks_structural_selectors_before_polish_fallback(
    browser_page: Page,
    html: str,
    expected_outcome: str,
    expect_clicked: bool,
) -> None:
    browser_page.set_content(f"<script>window.__clicked = false;</script>{html}")
    quiz_page = PlaywrightQuizPage(browser_page)

    outcome = quiz_page.advance()

    assert outcome == expected_outcome
    assert browser_page.evaluate("window.__clicked") is expect_clicked


def test_apply_answer_raises_quiz_page_error_on_out_of_range_choice(
    browser_page: Page,
) -> None:
    browser_page.set_content(
        """
        <div class="que multichoice">
          <div class="qtext">Pick one</div>
          <div class="answer">
            <input type="radio" id="r0" /><label for="r0">Only</label>
          </div>
        </div>
        """
    )
    quiz_page = PlaywrightQuizPage(browser_page)
    question = quiz_page.extract_questions()[0]

    with pytest.raises(QuizPageError, match="out of range"):
        quiz_page.apply_answer(
            question,
            Answer(answer_type="radio", reasoning="invalid", selected_indices=(3,)),
        )


def test_open_quiz_page_launches_navigates_and_cleans_up() -> None:
    data_url = (
        "data:text/html,"
        "<div class='que'><div class='qtext'>Hello Moodle</div>"
        "<div class='answer'><input type='text' id='t0'/></div></div>"
    )

    def _run() -> None:
        with open_quiz_page(start_url=data_url, headless=True) as quiz_page:
            questions = quiz_page.extract_questions()
            assert len(questions) == 1
            assert questions[0].prompt == "Hello Moodle"
            assert questions[0].question_type == "text"
            assert questions[0].slot_count == 1

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()


def test_apply_answer_succeeds_when_mathjax_overlay_covers_input_and_preserves_checked_box(
    browser_page: Page,
) -> None:
    browser_page.set_content(
        """
        <div class="que multichoice">
          <div class="qtext">Pick covered options:</div>
          <div class="answer" style="position:relative;">
            <div class="r0" style="position:relative;">
              <input type="checkbox" id="cov_0" checked />
              <label for="cov_0">Already checked</label>
            </div>
            <div class="r1" style="position:relative;">
              <input type="checkbox" id="cov_1" />
              <div class="MathJax" style="position:absolute; inset:0; z-index:999;"></div>
              <label for="cov_1">Covered by MathJax</label>
            </div>
          </div>
        </div>
        """
    )
    quiz_page = PlaywrightQuizPage(browser_page)
    question = quiz_page.extract_questions()[0]

    quiz_page.apply_answer(
        question,
        Answer(answer_type="checkbox", reasoning="both", selected_indices=(0, 1)),
    )

    assert browser_page.locator("#cov_0").is_checked()
    assert browser_page.locator("#cov_1").is_checked()


def test_playwright_quiz_page_navigate_and_is_open() -> None:
    first_url = "data:text/html,<div class='que'><div class='qtext'>Page 1</div></div>"
    second_url = "data:text/html,<div class='que'><div class='qtext'>Page 2</div></div>"

    def _run() -> None:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            quiz_page = PlaywrightQuizPage(page)

            assert quiz_page.is_open() is True
            quiz_page.navigate(first_url)
            assert quiz_page.extract_questions()[0].prompt == "Page 1"

            quiz_page.navigate(second_url)
            assert quiz_page.extract_questions()[0].prompt == "Page 2"

            page.close()
            assert quiz_page.is_open() is False
            with pytest.raises(QuizPageError):
                quiz_page.navigate(first_url)

            context.close()
            browser.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()


def test_open_quiz_page_handles_user_closed_browser_on_exit() -> None:
    def _run() -> None:
        with open_quiz_page(start_url="data:text/html,<p>Hi</p>", headless=True) as quiz_page:
            assert quiz_page.is_open() is True
            quiz_page._page.context.browser.close()  # type: ignore[union-attr]
            assert quiz_page.is_open() is False

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()


def test_playwright_quiz_page_translates_closed_page_errors_to_quiz_page_error() -> None:
    def _run() -> None:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            quiz_page = PlaywrightQuizPage(page)
            page.close()

            with pytest.raises(QuizPageError):
                quiz_page.extract_questions()
            with pytest.raises(QuizPageError):
                quiz_page.apply_answer(
                    Question(index=0, prompt="Q", question_type="text", slot_count=1),
                    Answer(answer_type="text", reasoning="r", text_answers=("a",)),
                )
            with pytest.raises(QuizPageError):
                quiz_page.advance()

            context.close()
            browser.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()



# --- Waiting for Questions, tab detection and attempt start pages (#19) ---

_QUE_HTML = "<div class='que'><div class='qtext'>Q?</div><div class='answer'><input type='text' name='a'/></div></div>"


def _never_cancelled() -> bool:
    return False


def _recorder(asked: list[str], answer: bool) -> Callable[[str], bool]:
    def _confirm(label: str) -> bool:
        asked.append(label)
        return answer

    return _confirm


def _fresh_page(browser_page: Page) -> Page:
    browser = browser_page.context.browser
    assert browser is not None
    return browser.new_context().new_page()


def test_wait_for_questions_finds_delayed_questions(browser_page: Page) -> None:
    page = _fresh_page(browser_page)
    page.set_content(
        "<div id='root'></div><script>setTimeout(() => {"
        f"document.getElementById('root').innerHTML = \"{_QUE_HTML}\";"
        "}, 400);</script>"
    )
    quiz_page = PlaywrightQuizPage(page, question_wait_s=5.0)

    assert quiz_page.extract_questions() == []
    questions = quiz_page.wait_for_questions(
        is_cancelled=_never_cancelled, confirm_start=lambda _label: False
    )

    assert len(questions) == 1
    page.context.close()


def test_wait_for_questions_switches_to_tab_with_quiz(browser_page: Page) -> None:
    page = _fresh_page(browser_page)
    page.set_content("<p>Kurs</p>")
    quiz_tab = page.context.new_page()
    quiz_tab.set_content(_QUE_HTML)
    quiz_page = PlaywrightQuizPage(page, question_wait_s=0.3)

    questions = quiz_page.wait_for_questions(
        is_cancelled=_never_cancelled, confirm_start=lambda _label: False
    )

    assert len(questions) == 1
    quiz_page.apply_answer(questions[0], Answer(answer_type="text", reasoning="r", text_answers=("x",)))
    assert quiz_tab.locator("input[name='a']").input_value() == "x"
    page.context.close()


def _start_page_html(label: str) -> str:
    return (
        f"<button id='start' onclick=\"document.body.innerHTML = &quot;{_QUE_HTML}&quot;\">"
        f"{label}</button>"
    )


def test_wait_for_questions_clicks_continue_attempt_without_asking(browser_page: Page) -> None:
    page = _fresh_page(browser_page)
    page.set_content(_start_page_html("Kontynuuj ostatnie podejście"))
    asked: list[str] = []
    quiz_page = PlaywrightQuizPage(page, question_wait_s=0.3)

    questions = quiz_page.wait_for_questions(
        is_cancelled=_never_cancelled, confirm_start=_recorder(asked, False)
    )

    assert len(questions) == 1
    assert asked == []
    page.context.close()


@pytest.mark.parametrize("label", ["Rozpocznij podejście", "Rozpocznij nowe podejście"])
def test_wait_for_questions_starts_attempt_only_after_confirmation(
    browser_page: Page, label: str
) -> None:
    page = _fresh_page(browser_page)
    page.set_content(_start_page_html(label))
    asked: list[str] = []
    quiz_page = PlaywrightQuizPage(page, question_wait_s=0.3)

    questions = quiz_page.wait_for_questions(
        is_cancelled=_never_cancelled, confirm_start=_recorder(asked, True)
    )

    assert len(questions) == 1
    assert asked == [label]
    page.context.close()


def test_wait_for_questions_does_not_start_attempt_when_declined(browser_page: Page) -> None:
    page = _fresh_page(browser_page)
    page.set_content(_start_page_html("Rozpocznij podejście"))
    quiz_page = PlaywrightQuizPage(page, question_wait_s=0.3)

    questions = quiz_page.wait_for_questions(
        is_cancelled=_never_cancelled, confirm_start=lambda _label: False
    )

    assert questions == []
    assert page.locator("#start").count() == 1
    page.context.close()


def test_wait_for_questions_confirms_start_attempt_preflight_dialog(browser_page: Page) -> None:
    page = _fresh_page(browser_page)
    page.set_content(
        "<button id='start' onclick=\"document.getElementById('mod_quiz_preflight_form')"
        ".style.display='block'\">Rozpocznij podejście</button>"
        "<form id='mod_quiz_preflight_form' style='display:none'>"
        "<input type='button' id='id_submitbutton' value='Rozpocznij podejście' "
        f"onclick=\"document.body.innerHTML = &quot;{_QUE_HTML}&quot;\"/></form>"
    )
    asked: list[str] = []
    quiz_page = PlaywrightQuizPage(page, question_wait_s=2.0)

    questions = quiz_page.wait_for_questions(
        is_cancelled=_never_cancelled, confirm_start=_recorder(asked, True)
    )

    assert len(questions) == 1
    assert asked == ["Rozpocznij podejście"]
    page.context.close()


def test_wait_for_questions_is_cancellable(browser_page: Page) -> None:
    page = _fresh_page(browser_page)
    page.set_content("<p>pusto</p>")
    quiz_page = PlaywrightQuizPage(page, question_wait_s=30.0)

    # Match by name: test_package_import purges sys.modules, so the class object may differ.
    with pytest.raises(Exception) as info:
        quiz_page.wait_for_questions(is_cancelled=lambda: True, confirm_start=lambda _l: False)
    assert type(info.value).__name__ == "CancellationError"
    page.context.close()


def test_describe_location_reports_url_and_title(browser_page: Page) -> None:
    page = _fresh_page(browser_page)
    page.set_content("<title>Mój kurs</title><p>x</p>")
    quiz_page = PlaywrightQuizPage(page)

    description = quiz_page.describe_location()

    assert "about:blank" in description
    assert "Mój kurs" in description
    page.context.close()
