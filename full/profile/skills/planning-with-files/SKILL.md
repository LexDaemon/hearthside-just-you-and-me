---
name: planning-with-files
description: Manage long multi-step work with concise persistent planning files.
version: 2.0.0
license: MIT
---

# Planning With Files

## Use when
Use for work that spans multiple phases, depends on discoveries, or may survive a context reset. Use `loopy` for a reusable repeatable workflow rather than a project-specific plan.

## Files
Keep only three lightweight files in an approved project location:
- `task_plan.md`: goal, scope, phases, acceptance criteria, and current status.
- `findings.md`: verified facts, decisions, blockers, and source references.
- `progress.md`: completed work, validation output, and next concrete action.

## Procedure
1. Read existing planning files before acting.
2. Write a plan with testable acceptance criteria before complex changes.
3. Update findings after material discovery and progress after each completed phase.
4. On resumption, read the files and continue from verified state rather than recreating context.
5. Remove temporary planning material when the project is complete if persistence is not requested.

## Boundaries
Do not create planning files for trivial tasks, store credentials, or treat a plan as evidence that work succeeded.
