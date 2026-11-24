---
name: contract-compare
description: Compare two contract versions and expose textual drift.
version: 2.0.0
license: MIT
---

# Contract Compare

## Use when
Use for an auditable before-and-after comparison of exactly two identified contract versions, regardless of jurisdiction. For multiple amendments, identify the ordered documents and compare each relevant pair without claiming which language controls. Use the included `contract-review` for separately requested legal or commercial analysis.

## Procedure
1. Confirm the baseline, revision, completeness, and any annexes.
2. Match clauses by identifier, heading, and content; flag moved or renumbered text.
3. Classify changes as added, removed, modified, or relocated.
4. Quote both versions with exact locators.
5. Flag uncertainty caused by missing pages, inconsistent versions, or unmatched annexes.

## Output
Return a change table with baseline text, revised text, change type, and locator.

## Boundaries
Do not assess risk, materiality, favored party, or legal effect unless a separate review is requested.
