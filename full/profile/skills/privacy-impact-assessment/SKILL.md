---
name: privacy-impact-assessment
description: Structure a privacy impact assessment from verified facts.
version: 2.0.0
license: MIT
---

# Privacy Impact Assessment

## Use when
Use when a processing activity needs a privacy-focused assessment. Assess the defined processing directly; no absent AI, vendor, or feature-review skill is required. For a supplied vendor contract, use the included `contract-review` if a separate contract review is requested.

## Procedure
1. Define purpose, data categories, people affected, sources, recipients, locations, retention, and processing stages.
2. Establish relevant jurisdictions, sector, cross-border transfers, and effective dates. Verify applicable legal and policy requirements through official authorities with `web_search` and `web_extract`; do not assume Argentine law, GDPR, or another framework applies by default. Separate statutory requirements, institutional policy, and voluntary safeguards. Identify whether a formal assessment is legally required and who must approve it; leave unresolved rules as questions.
3. Identify necessity, proportionality, risks, safeguards, residual risks, and evidence gaps.
4. Assign decision owners and verification tasks without asserting compliance prematurely.

## Output
Return processing map, legal or policy questions, risk register, safeguards, residual-risk rationale, and open items.

## Boundaries
Do not declare compliance or authorize processing.
