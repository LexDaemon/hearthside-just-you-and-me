---
name: anki
description: Build and repair importable Anki cards from supplied material.
version: 2.0.0
license: MIT
---

# Anki Cards

## Use when
Use to convert supplied material into concise retrieval-practice cards or to repair an existing card set. Use `study-strategy-selector` to choose the method first when needed.

## Procedure
1. Confirm the learning objective, source, language, and card type.
2. Extract one testable fact, distinction, rule, or application step per card.
3. Preserve source context, relevant conditions, and how up to date the information is where they affect the answer.
4. Use clear prompts, precise answers, and tags that support review.
5. Check for ambiguity, duplication, unsupported claims, and excessive card length.

## Output
Return a reviewable table or TSV only when explicitly requested; otherwise provide cards in chat.

## Boundaries
Do not open or modify a personal Anki collection or store source material without request. Do not invent facts or create cards from unsupported claims.
