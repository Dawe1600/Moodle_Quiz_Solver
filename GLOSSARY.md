# Moodle LLM Harness

Domain language for automating Moodle quiz attempts with subject-specific reference materials and AI-assisted answering.

## Language

**Harness**:
The layer wrapped around the LLM — Subject Profiles, system prompt, structured JSON Answers, conversation context, and browser control — that turns a model into a Moodle quiz-solving tool.
_Avoid_: Wrapper, bot framework

**Subject Profile**:
A named subject configuration specifying model preferences, system instructions, and Reference Materials for a quiz session.
_Avoid_: Preset, subject config

**Reference Material**:
A local source document (such as lecture slides or notes) treated as primary ground truth when solving Questions.
_Avoid_: Attachment, knowledge file

**Quiz Page**:
A single page of an active Moodle quiz attempt containing one or more Questions and attempt navigation controls.
_Avoid_: Screen, DOM view

**Question**:
An extracted Moodle quiz item consisting of prompt text, optional images, answer type, and available options or text slots.
_Avoid_: Task, prompt item

**Answer**:
A structured solution for a Question containing the answer type, selected option indices or labels or text response(s), and brief reasoning.
_Avoid_: Result, completion

**Solver**:
The capability that resolves a Question into a validated Answer using a Subject Profile while preserving quiz-attempt context.
_Avoid_: AI handler, backend

**Auto Mode**:
The continuous solving loop that resolves every Question on the current Quiz Page and advances until the final submission screen or user interruption.
_Avoid_: Batch mode, daemon
