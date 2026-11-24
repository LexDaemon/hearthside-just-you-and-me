---
name: contract-review
description: Review contracts with verified, jurisdiction-aware sources.
version: 2.0.0
license: MIT
---

# Contract Review

## Use when
Use for an existing agreement that needs issue spotting, explanation, and proposed redlines. Use the included `contract-compare` only for textual differences. Summarize completed findings for business readers directly when requested; no other skill is required.

## Procedure
1. Confirm the complete agreement, annexes, governing law, parties, transaction, and review objective.
2. Read the document in order; identify definitions, scope, payment, term, liability, IP, data, confidentiality, termination, dispute, and missing provisions.
3. Quote each material clause with an exact locator. Separate text, risk, verified rule, assumption, and proposed change.
4. Establish the relevant jurisdiction, governing law, forum, and effective date without assuming any country. Verify legal claims using `web_search` and `web_extract` against primary legislation, official case law, or supplied authoritative sources. Cite the exact provision and source date. If the framework or source is unavailable, report a legal question, not a settled conclusion.
5. Prioritize findings by consequence and negotiation leverage.

## Output
Return a ranked findings table: clause, issue, consequence, evidence or source question, and redline or decision option.

## Boundaries
Do not sign, submit, send, or claim a legal conclusion unsupported by verified material.
