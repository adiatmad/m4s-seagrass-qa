# Agent instructions

Inspect the README, configuration, pipeline, Streamlit front end, tests, and current repository state before editing.

## Product and data constraints

- This is a configurable KoboToolbox survey QA pipeline and Streamlit dashboard. Preserve the separation between generic pipeline behavior and the M4S-specific values in `config.yaml`.
- Treat automated findings as review candidates, not proof that a field response is wrong. Scientific and GPS values must be flagged rather than auto-corrected unless an explicit specification authorizes otherwise.
- Preserve source rows and columns, correction logs, severity distinctions, and the documented output files. Never silently drop, reorder, or reinterpret data.
- Auto-corrections to enumerator names/geography must remain conservative, configurable, and fully logged. Keep thresholds and project-specific geography/species values in configuration rather than hard-coding them in the pipeline.
- QA history may contain aggregate statistics only; do not add raw survey rows or personal data to the history database.
- Respect the current local-first workflow: uploaded data is not persisted server-side; the user carries the SQLite history file between sessions.

## Required engineering workflow

1. Inspect current pipeline stages, configuration, outputs, tests, and README before changing behavior.
2. For non-trivial changes, define user outcome, scope, non-goals, constraints, risks, and testable acceptance criteria.
3. Use vertical red-green-refactor slices with representative survey fixtures. Test through public pipeline/CLI/UI behavior rather than private implementation details.
4. For data bugs, minimise the failing workbook/row/field case and add a regression test before changing transformation or validation logic.
5. Prefer small, cohesive changes. Avoid speculative abstractions, guessed survey semantics, silent coercion, and unnecessary dependencies.
6. Review separately for spec compliance, source-data preservation, correction-log completeness, history privacy, and output contract stability.
7. Run relevant tests and inspect the final diff. Report exact commands/results; distinguish fixture coverage from real Kobo acceptance.

## Quality gates

- Apply the full Anti-Slop core in `antislop.md`.
- This is a Streamlit UI. Use UI/UX Pro Max when installed for substantive interface work, while preserving the field-team workflow. Verify responsive behavior, keyboard/focus, loading/empty/error states, and real interactions.
- Model the data pipeline as a dependency graph: load → standardize → validate → correct → report → export → history. Do not parallelise dependent transformations.
- Never weaken tests, expose survey data in logs, or silently change the meaning of a QA finding.
