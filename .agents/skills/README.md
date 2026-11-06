# Agent skills and engineering toolkit

Project rules are in `/AGENTS.md`; the engineering constitution is in `.specify/memory/constitution.md`. The Anti-Slop core is included at `/antislop.md`.

Upstream sources:
- Matt Pocock engineering skills: https://github.com/mattpocock/skills
- Anti-Slop: https://github.com/miqdadbadjuber/anti-slop
- GitHub Spec Kit: https://github.com/github/spec-kit
- UI/UX Pro Max: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill

Install selected Matt Pocock skills into this Codex project with `npx skills@latest add mattpocock/skills -a codex`. Select TDD, diagnosing-bugs, codebase-design, and code-review.

The Anti-Slop core is already vendored. To add its modular skills, use `npx skills@latest add miqdadbadjuber/anti-slop --all -a codex`.

Install Spec Kit with `uv tool install specify-cli` and verify using `specify version`. Preserve the current project structure and follow existing-project adoption guidance before generating templates.

This is a Streamlit dashboard. Install the complete UI/UX Pro Max skill and searchable design data with `npx ui-ux-pro-max-cli init --ai codex`. The CLI is the supported installation path.

Review installer diffs before accepting updates. Do not force-overwrite customized agent files or commit machine-specific global settings.
