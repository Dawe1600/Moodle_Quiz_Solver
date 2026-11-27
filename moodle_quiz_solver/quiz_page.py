"""Playwright-backed Moodle quiz page extraction, answering, and navigation."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal, Protocol, runtime_checkable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Locator, Page, sync_playwright

from moodle_quiz_solver.domain import Answer, Question
from moodle_quiz_solver.exceptions import CancellationError, QuizPageError

NavigationOutcome = Literal["next", "finished", "missing"]


class QuizPage(Protocol):
    """Seam between Auto Mode and browser-based Moodle quiz interaction."""

    def extract_questions(self) -> list[Question]:
        """Extract all normalized questions currently rendered on the page."""
        ...

    def apply_answer(self, question: Question, answer: Answer) -> None:
        """Apply a structured answer to the corresponding question controls."""
        ...

    def advance(self) -> NavigationOutcome:
        """Advance to the next quiz page if available, or report completion/missing."""
        ...

    def navigate(self, url: str) -> None:
        """Navigate the quiz page to a new URL."""
        ...

    def is_open(self) -> bool:
        """Return whether the underlying page and browser are still open."""
        ...


@runtime_checkable
class QuestionWaitingQuizPage(QuizPage, Protocol):
    """Quiz Page that can wait for Questions, find the quiz tab and enter an attempt."""

    def wait_for_questions(
        self,
        *,
        is_cancelled: Callable[[], bool],
        confirm_start: Callable[[str], bool],
    ) -> list[Question]:
        """Wait for Questions; ``confirm_start`` must approve starting a new attempt."""
        ...

    def describe_location(self) -> str:
        """Return the current page URL and title for diagnostics."""
        ...


_QUESTION_SELECTOR = ".que"
_POLL_SLICE_MS = 250
_CONTINUE_ATTEMPT_LABELS = ("Kontynuuj ostatnie podejście", "Continue your attempt")
_START_ATTEMPT_LABELS = (
    "Rozpocznij nowe podejście",
    "Rozpocznij podejście",
    "Re-attempt quiz",
    "Attempt quiz",
)
_PREFLIGHT_SUBMIT_SELECTOR = (
    "#mod_quiz_preflight_form #id_submitbutton, "
    "#mod_quiz_preflight_form input[type='submit'], "
    "#mod_quiz_preflight_form button[type='submit']"
)


def _attempt_button_selector(label: str) -> str:
    return (
        f"button:has-text('{label}'), input[type='submit'][value*='{label}'], "
        f"a:has-text('{label}')"
    )


class PlaywrightQuizPage:
    """Deep Quiz Page adapter hiding Playwright locators and Moodle DOM details."""

    def __init__(self, page: Page, *, question_wait_s: float = 10.0) -> None:
        self._page = page
        self._question_wait_s = question_wait_s

    def wait_for_questions(
        self,
        *,
        is_cancelled: Callable[[], bool],
        confirm_start: Callable[[str], bool],
    ) -> list[Question]:
        """Wait for Questions, switch to the quiz tab, or enter an attempt, then extract.

        "Kontynuuj ostatnie podejście" is clicked automatically; starting a new attempt
        is irreversible, so it is clicked only when ``confirm_start(label)`` approves.
        Raises CancellationError as soon as ``is_cancelled`` reports True.
        """
        with _translate_playwright_errors("Błąd oczekiwania na pytania quizu"):
            if self._poll_for_questions(is_cancelled):
                return self.extract_questions()
            if self._switch_to_tab_with_questions():
                return self.extract_questions()
            if self._enter_attempt(confirm_start) and self._poll_for_questions(
                is_cancelled, confirm_preflight=True
            ):
                return self.extract_questions()
            return []

    def describe_location(self) -> str:
        """Return the current page URL and title for diagnostics."""
        try:
            title = self._page.title()
        except PlaywrightError:
            title = "?"
        return f"URL: {self._page.url}, tytuł: {title}"

    def _poll_for_questions(
        self, is_cancelled: Callable[[], bool], *, confirm_preflight: bool = False
    ) -> bool:
        deadline = time.monotonic() + self._question_wait_s
        while True:
            if is_cancelled():
                raise CancellationError("Przerwano oczekiwanie na pytania quizu.")
            if confirm_preflight:
                submit = self._first_visible_match(_PREFLIGHT_SUBMIT_SELECTOR)
                if submit is not None:
                    submit.click()
                    confirm_preflight = False
            remaining_ms = (deadline - time.monotonic()) * 1000
            if remaining_ms <= 0:
                return False
            try:
                self._page.wait_for_selector(
                    _QUESTION_SELECTOR,
                    state="attached",
                    timeout=max(1.0, min(_POLL_SLICE_MS, remaining_ms)),
                )
                return True
            except PlaywrightTimeoutError:
                continue

    def _switch_to_tab_with_questions(self) -> bool:
        for candidate in self._page.context.pages:
            if candidate is self._page or candidate.is_closed():
                continue
            if candidate.locator(_QUESTION_SELECTOR).count() > 0:
                self._page = candidate
                candidate.bring_to_front()
                return True
        return False

    def _enter_attempt(self, confirm_start: Callable[[str], bool]) -> bool:
        for label in _CONTINUE_ATTEMPT_LABELS:
            button = self._first_visible_match(_attempt_button_selector(label))
            if button is not None:
                button.click()
                return True
        for label in _START_ATTEMPT_LABELS:
            button = self._first_visible_match(_attempt_button_selector(label))
            if button is not None:
                if not confirm_start(label):
                    return False
                button.click()
                return True
        return False

    def is_open(self) -> bool:
        """Return whether the underlying page and browser are still open."""
        try:
            if self._page.is_closed():
                return False
            browser = self._page.context.browser
            if browser is not None and not browser.is_connected():
                return False
            return True
        except Exception:
            return False

    def navigate(self, url: str) -> None:
        """Navigate the quiz page to a new URL."""
        if not self.is_open():
            raise QuizPageError("Przeglądarka jest zamknięta.")
        with _translate_playwright_errors(f"Nie udało się otworzyć adresu URL '{url}'"):
            self._page.goto(url)

    def extract_questions(self) -> list[Question]:
        """Extract all normalized questions currently rendered on the page."""
        with _translate_playwright_errors("Błąd odczytu strony quizu"):
            containers = self._page.locator(".que")
            questions: list[Question] = []
            for idx in range(containers.count()):
                questions.append(self._extract_question(idx, containers.nth(idx)))
            return questions

    def apply_answer(self, question: Question, answer: Answer) -> None:
        """Apply a structured answer to the corresponding question controls."""
        with _translate_playwright_errors("Błąd zaznaczania odpowiedzi na stronie quizu"):
            self._apply_answer_inner(question, answer)

    def _apply_answer_inner(self, question: Question, answer: Answer) -> None:
        container = self._page.locator(".que").nth(question.index)
        if answer.answer_type in ("radio", "checkbox"):
            choice_pairs = self._find_choice_pairs(container)
            for idx in answer.selected_indices:
                if not (0 <= idx < len(choice_pairs)):
                    raise QuizPageError(
                        f"Choice index {idx} out of range for question {question.index}."
                    )
                input_loc, label_loc = choice_pairs[idx]
                self._select_choice(input_loc, label_loc)
            return

        if answer.answer_type == "select":
            selects = container.locator("select")
            select_count = selects.count()
            if select_count == 0:
                raise QuizPageError(f"No select controls found for question {question.index}.")
            if len(answer.selected_options) > select_count:
                raise QuizPageError(
                    f"Too many selected_options ({len(answer.selected_options)}) "
                    f"for {select_count} select controls on question {question.index}."
                )
            for idx, option_label in enumerate(answer.selected_options):
                try:
                    selects.nth(idx).select_option(label=option_label)
                except Exception as exc:
                    raise QuizPageError(
                        f"Failed to select '{option_label}' in dropdown {idx} "
                        f"of question {question.index}: {exc}"
                    ) from exc
            return

        if answer.answer_type == "text":
            text_inputs = self._find_visible_text_inputs(container)
            if not text_inputs:
                raise QuizPageError(f"No visible text inputs found for question {question.index}.")
            if len(answer.text_answers) > len(text_inputs):
                raise QuizPageError(
                    f"Too many text_answers ({len(answer.text_answers)}) "
                    f"for {len(text_inputs)} text inputs on question {question.index}."
                )
            for idx, text_value in enumerate(answer.text_answers):
                try:
                    text_inputs[idx].fill(text_value)
                except Exception as exc:
                    raise QuizPageError(
                        f"Failed to fill text input {idx} of question {question.index}: {exc}"
                    ) from exc
            return

        raise QuizPageError(f"Unsupported answer type: {answer.answer_type}")

    def advance(self) -> NavigationOutcome:
        """Advance to the next quiz page if available, or report completion/missing."""
        with _translate_playwright_errors("Błąd nawigacji strony quizu"):
            return self._advance_inner()

    def _advance_inner(self) -> NavigationOutcome:
        if self._has_visible_match(
            "form#responseform input[name='finishattempt']:not([type='hidden']), "
            "form#responseform button[name='finishattempt'], "
            ".submitbtns input[name='finishattempt']:not([type='hidden']), "
            ".submitbtns button[name='finishattempt'], "
            "form#responseform [data-quiz-action='finish'], "
            "form#responseform [data-action='finish-attempt'], "
            ".submitbtns [data-quiz-action='finish'], "
            ".submitbtns [data-action='finish-attempt'], "
            "#mod_quiz-next-nav[data-finish='true']"
        ):
            return "finished"

        if self._has_visible_match(
            "#mod_quiz-next-nav[value*='Zapisz podejście'], "
            "#mod_quiz-next-nav[value*='Zakończ podejście'], "
            "#mod_quiz-next-nav[value*='Finish attempt'], "
            ".submitbtns input[type='submit'][value*='Zapisz podejście'], "
            ".submitbtns input[type='submit'][value*='Zakończ podejście'], "
            ".submitbtns input[type='submit'][value*='Finish attempt'], "
            ".submitbtns button:has-text('Zapisz podejście'), "
            ".submitbtns button:has-text('Zakończ podejście'), "
            ".submitbtns button:has-text('Finish attempt')"
        ):
            return "finished"

        next_btn = self._first_visible_match(
            "#mod_quiz-next-nav:not([type='hidden']), "
            "form#responseform input[name='next']:not([type='hidden']), "
            "form#responseform button[name='next'], "
            "input[name='next']:not([type='hidden']), "
            "button[name='next']"
        )
        if next_btn is not None:
            next_btn.click()
            return "next"

        polish_next = self._first_visible_match(
            ".submitbtns input[type='submit'][value*='Następna'], "
            "input[type='submit'][value*='Następna'], "
            ".submitbtns button:has-text('Następna')"
        )
        if polish_next is not None:
            polish_next.click()
            return "next"

        return "missing"

    def _has_visible_match(self, selector: str) -> bool:
        return self._first_visible_match(selector) is not None

    def _first_visible_match(self, selector: str) -> Locator | None:
        locator = self._page.locator(selector)
        for i in range(locator.count()):
            candidate = locator.nth(i)
            if candidate.is_visible():
                return candidate
        return None

    def _extract_question(self, index: int, container: Locator) -> Question:
        qtext_loc = container.locator(".qtext")
        prompt = qtext_loc.inner_text().strip() if qtext_loc.count() > 0 else ""
        images: list[bytes] = []
        if qtext_loc.count() > 0:
            images.extend(self._capture_images(qtext_loc.locator("img")))

        choice_pairs = self._find_choice_pairs(container)
        if choice_pairs:
            option_texts: list[str] = []
            for _, label_loc in choice_pairs:
                if label_loc is None:
                    option_texts.append("")
                    continue
                option_texts.append(self._capture_tagged_text(label_loc, images))

            first_type = choice_pairs[0][0].get_attribute("type")
            q_type: Literal["radio", "checkbox"] = (
                "checkbox" if first_type == "checkbox" else "radio"
            )
            return Question(
                index=index,
                prompt=prompt,
                question_type=q_type,
                options=tuple(option_texts),
                image_bytes=tuple(images),
                slot_count=1,
            )

        selects = container.locator("select")
        if selects.count() > 0:
            return self._extract_select_question(index, prompt, selects, images)

        text_inputs = self._find_visible_text_inputs(container)
        if text_inputs:
            return Question(
                index=index,
                prompt=prompt,
                question_type="text",
                image_bytes=tuple(images),
                slot_count=len(text_inputs),
            )

        return Question(
            index=index,
            prompt=prompt,
            question_type=None,
            image_bytes=tuple(images),
            slot_count=0,
        )

    def _capture_tagged_text(self, target: Locator, images: list[bytes]) -> str:
        text = target.inner_text().strip()
        captured = self._capture_images(target.locator("img"))
        if not captured:
            return text
        start_num = len(images) + 1
        images.extend(captured)
        tags = " ".join(f"[Image #{start_num + offset}]" for offset in range(len(captured)))
        return f"{text} {tags}".strip()

    def _capture_images(self, img_locator: Locator) -> list[bytes]:
        captured: list[bytes] = []
        for i in range(img_locator.count()):
            img = img_locator.nth(i)
            try:
                img.scroll_into_view_if_needed()
                captured.append(img.screenshot())
            except Exception as exc:
                raise QuizPageError(f"Failed to capture question image #{i}: {exc}") from exc
        return captured

    def _find_visible_text_inputs(self, container: Locator) -> list[Locator]:
        candidates = container.locator(
            "input:not([type='hidden']):not([type='radio']):not([type='checkbox'])"
            ":not([type='submit']):not([type='button']):not([type='reset']):not([type='image']), "
            "textarea"
        )
        visible: list[Locator] = []
        for i in range(candidates.count()):
            loc = candidates.nth(i)
            if loc.is_visible():
                visible.append(loc)
        return visible

    def _extract_select_question(
        self, index: int, base_prompt: str, selects: Locator, images: list[bytes]
    ) -> Question:
        row_lines: list[str] = []
        unique_options: list[str] = []
        seen_options: set[str] = set()
        select_count = selects.count()

        for j in range(select_count):
            sel = selects.nth(j)
            row = sel.locator("xpath=./ancestor::tr").first
            context_text = f"Select {j + 1}"
            if row.count() > 0:
                text_td = row.locator("td.text")
                target = text_td.first if text_td.count() > 0 else row
                row_text = self._capture_tagged_text(target, images)
                if row_text:
                    context_text = row_text

            opts = self._extract_select_options(sel)
            for opt in opts:
                if opt not in seen_options:
                    seen_options.add(opt)
                    unique_options.append(opt)
            row_lines.append(f"{context_text}: {', '.join(opts)}")

        full_prompt = base_prompt
        if row_lines:
            joined_rows = "\n".join(row_lines)
            full_prompt = f"{base_prompt}\n{joined_rows}".strip()

        return Question(
            index=index,
            prompt=full_prompt,
            question_type="select",
            options=tuple(unique_options),
            image_bytes=tuple(images),
            slot_count=select_count,
        )

    def _extract_select_options(self, select_loc: Locator) -> list[str]:
        options = select_loc.locator("option")
        result: list[str] = []
        for i in range(options.count()):
            opt = options.nth(i)
            text = opt.inner_text().strip()
            if not text:
                continue
            # Structural placeholder checks first: disabled or empty value attribute.
            if opt.get_attribute("disabled") is not None:
                continue
            value_attr = opt.get_attribute("value")
            if value_attr is not None and value_attr == "":
                continue
            # Polish fallback for Moodle placeholder labels.
            if "wybierz" in text.lower():
                continue
            result.append(text)
        return result

    def _find_choice_pairs(self, container: Locator) -> list[tuple[Locator, Locator | None]]:
        inputs = container.locator(".answer input[type='radio'], .answer input[type='checkbox']")
        pairs: list[tuple[Locator, Locator | None]] = []
        for i in range(inputs.count()):
            inp = inputs.nth(i)
            label = self._find_label_for_input(container, inp)
            pairs.append((inp, label))
        return pairs

    def _find_label_for_input(self, container: Locator, inp: Locator) -> Locator | None:
        labelledby = inp.get_attribute("aria-labelledby")
        if labelledby:
            for ref_id in labelledby.split():
                loc = container.locator(f"[id='{ref_id}']")
                if loc.count() > 0:
                    return loc.first

        inp_id = inp.get_attribute("id")
        if inp_id:
            for_label = container.locator(f"label[for='{inp_id}']")
            if for_label.count() > 0:
                return for_label.first

        ancestor_label = inp.locator("xpath=ancestor::label[1]")
        if ancestor_label.count() > 0:
            return ancestor_label.first

        sibling_label = inp.locator("xpath=..").locator("[data-region='answer-label'], label")
        if sibling_label.count() > 0:
            return sibling_label.first

        return None

    def _select_choice(self, input_loc: Locator, label_loc: Locator | None) -> None:
        if input_loc.is_checked():
            return
        try:
            input_loc.check(force=True, timeout=2000)
            return
        except Exception as exc:
            if label_loc is not None:
                try:
                    label_loc.click(position={"x": 5, "y": 5}, timeout=2000)
                    if input_loc.is_checked():
                        return
                except Exception:
                    pass
            try:
                input_loc.dispatch_event("click")
            except Exception as dispatch_exc:
                raise QuizPageError(
                    f"Failed to select choice input: {dispatch_exc}"
                ) from exc
            if not input_loc.is_checked():
                raise QuizPageError("Failed to select choice input.") from exc


@contextmanager
def _translate_playwright_errors(action: str) -> Iterator[None]:
    """Translate Playwright driver errors into domain QuizPageError."""
    try:
        yield
    except QuizPageError:
        raise
    except PlaywrightError as exc:
        raise QuizPageError(f"{action}: {exc}") from exc


@contextmanager
def open_quiz_page(
    start_url: str | None = None,
    *,
    headless: bool = False,
) -> Iterator[PlaywrightQuizPage]:
    """Launch Playwright Chromium, optionally navigate to start_url, and guarantee teardown."""
    launch_args = [] if headless else ["--start-maximized"]
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless, args=launch_args)
            context = browser.new_context(no_viewport=not headless)
            try:
                page = context.new_page()
                if start_url is not None:
                    page.goto(start_url)
                yield PlaywrightQuizPage(page)
            finally:
                for closer in (context.close, browser.close):
                    try:
                        closer()
                    except Exception:
                        pass
    except PlaywrightError as exc:
        raise QuizPageError(f"Błąd przeglądarki Playwright: {exc}") from exc
