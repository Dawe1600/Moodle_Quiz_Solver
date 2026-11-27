# Textual TUI for interactive TTY sessions, single-thread browser worker, plain-text fallback

Interactive sessions use a full-screen `Textual` TUI (status sidebar plus a scrollable **Auto Mode** activity stream) whenever stdin and stdout are both an interactive TTY; otherwise the existing `Rich` + `input()` CLI loop runs unchanged, so scripts, pipes and the `bot.main(argv, input_fn=..., output_fn=...)` test seam keep working. Because Playwright's synchronous API is bound to the thread that started it, every browser and **Quiz Page** operation (open, navigate, extract **Questions**, apply an **Answer**, advance, close) runs on one dedicated worker thread that the `Textual` event loop talks to through async-safe calls; never on arbitrary thread-pool workers. The TUI renders **Auto Mode** progress from typed events passed to `run_auto_mode(event_fn=...)` (`PageAnalysisStarted`, `QuestionSolvingStarted`, `QuestionAnswered`, `AutoModeStopped`) instead of parsing `output_fn` strings, and cancellation goes through the shared `Cancellation` (ESC / Stop button) instead of raw terminal polling while `Textual` owns the terminal.

## Considered Options

- **Keep the line-based `Rich` CLI only**: rejected; each screen clear loses earlier **Question**/**Answer** output and every action needs typing plus Enter.
- **Switch the **Quiz Page** to Playwright's async API**: rejected for now; it would rewrite all of `QuizPage` and its tests for no user-visible gain over a dedicated worker thread.
- **Parse `output_fn` text in the TUI**: rejected; it couples the UI to Polish log wording. Typed events are additive and leave `output_fn`/`status_fn` intact for the fallback.

## Consequences

- Two front-ends share one session layer (configuration, browser opener, **Solver** builder, `Cancellation`); domain code must stay free of `Textual` imports.
- Code touching the browser from the TUI must hop to the browser worker thread; calling `QuizPage` from the event loop or a generic worker is a bug.
