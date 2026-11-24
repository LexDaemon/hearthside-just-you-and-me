---
name: loopy
description: Design, run, and improve bounded repeatable workflows with evidence.
version: 2.0.0
license: MIT
---

# Repeatable Workflows

## Use when
Use for a recurring process that needs a stable trigger, bounded steps, evidence, and feedback. Use `planning-with-files` for a one-off or project-specific plan.

## Procedure
1. Define trigger, owner, inputs, output, stop condition, and failure mode.
2. Write the smallest repeatable sequence that can produce the output.
3. Attach evidence to each material step and define one quality check.
4. Run a pilot, record failures, and revise the loop rather than adding vague instructions.
5. Publish or reuse the loop only after the result is reproducible.

## Output
Return trigger, steps, evidence, quality check, stop condition, and revision rule.

## Boundaries
Do not automate irreversible or external actions without explicit approval and a verification step.
