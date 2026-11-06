# M4S Seagrass QA — Engineering Constitution

## I. Configurable project-specific rules
Species, site geography, GPS boundaries, and QA thresholds belong in configuration. The pipeline must not silently embed M4S site assumptions in generic processing logic.

## II. Human-reviewed findings
QA findings are evidence for investigation, not authoritative truth. Scientific and GPS values are flagged for human review and are not auto-corrected.

## III. Conservative, logged correction
Enumerator and geography normalization must use explicit configurable thresholds and record every correction. Ambiguous matches must remain unresolved rather than guessed.

## IV. Source-data preservation
Preserve original rows, columns, identifiers, and provenance. Clean outputs, raw-with-flags exports, correction logs, and per-enumerator reports must follow their documented contracts.

## V. Privacy and local-first history
QA history stores aggregate quality statistics, not raw survey records. Preserve the user-carried SQLite history workflow and do not add server-side persistence or telemetry.

## VI. Test the whole data contract
Behavior changes require representative regression fixtures. Verify severity, row/column preservation, corrections, export formats, and history behavior.

## VII. Honest delivery
Inspect the diff and run relevant tests. Report exact commands and results, including what has not been tested against a real Kobo export.

## Governance
Keep the README, configuration, pipeline, dashboard, and output contracts aligned. Amend this constitution only when the project-wide QA or data-handling contract changes.
