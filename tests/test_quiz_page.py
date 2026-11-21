from __future__ import annotations

from collections.abc import Iterator
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
            '<div class="que"><div class="qtext">No nav here</div></div>',
            "missing",
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
