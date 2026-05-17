# GitHub Copilot — repository instructions

The operating contract for any AI coding assistant working in this repository is **[`AGENTS.md`](../AGENTS.md)** at the repository root.

Always read `AGENTS.md` before making non-trivial changes. It points to the requirement spec, the architecture & design document, and the external references, and it lists the non-negotiable architectural invariants you must preserve.

Do not duplicate guidance here. If a project-wide convention needs to change, change it in `AGENTS.md`; this file exists only so GitHub Copilot picks up the pointer in environments that haven't yet adopted the open `agents.md` convention.
