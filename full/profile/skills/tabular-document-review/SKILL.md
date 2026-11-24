---
name: tabular-document-review
description: Extract cited fields consistently across a document set.
version: 2.0.0
license: MIT
---

# Tabular Document Review

## Use when
Use to extract the same defined fields from multiple documents with source locators.

## Procedure
1. Define the document set, extraction fields, normalization rules, and missing-data policy.
2. Read each document consistently and record the exact source locator for every value.
3. Preserve ambiguity, conflicting values, and absent fields rather than guessing.
4. Validate row count against the supplied document count.

## Output
Return a table with document identifier, requested fields, locator, and status or uncertainty note.

## Boundaries
Do not declare a document compliant, complete, or legally safe from field extraction alone.
