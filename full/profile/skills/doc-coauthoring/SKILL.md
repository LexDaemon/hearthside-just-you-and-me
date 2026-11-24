---
name: doc-coauthoring
description: Collaborate on a substantial document and check whether its intended reader can use it.
version: 3.0.0
license: LicenseRef-Hearthside-Source-Available-1.0
---

# Collaborative Document Work

Original Hearthside instructions for Hermes. The broad idea of gathering context, revising a document, and checking reader understanding is also described in Anthropic's public document-collaboration workflow. This is a separately written implementation, not a bundle of Anthropic's skill text, document engines, or Claude integrations.

## Choose the right level of help

Use this workflow for a substantial proposal, specification, report, decision record, or similar document. Ask whether the author wants coaching, a draft, a revision, or criticism. For a short edit, perform the requested edit rather than imposing a multi-stage process. Preserve the author's ownership of the decisions and voice.

## Establish the reader and evidence

1. Identify the intended reader, the decision or task the document should enable, its format, scope, deadline if relevant, and constraints.
2. Accept the author's existing text and notes in their present form. Organize them before asking more questions; do not require a perfect brief first.
3. Read only supplied or authorized sources. Use available Hermes tools such as `read_file`, `search_files`, `web_search`, and `web_extract`. No additional AI account, connector, MCP server, or sibling skill is required for the text workflow.
4. Keep facts, source locators, assumptions, disagreements, and missing information distinct. Ask a small batch of questions only when the answers materially change the document. Do not fill evidentiary gaps with plausible details.
5. Agree on the output location before writing files. A request to draft is not permission to publish, send a message, sign an agreement, or upload confidential material to another provider.

## Build and revise

1. Propose a structure that serves the reader's actual task. Begin with the most consequential or uncertain section when that will resolve the rest more efficiently.
2. Work in useful increments: establish a section's purpose, draft or revise at the requested level, and obtain focused feedback where a real choice remains.
3. Preserve names, identifiers, quotations, technical distinctions, uncertainties, and approved decisions. Flag contradictions rather than silently deciding them for the author.
4. Prefer direct, specific prose to repetition and filler. Keep terminology, headings, numbering, cross-references, and the strength of claims consistent throughout.
5. Give visual material useful captions or alternative text when needed. State the reading environment's actual limitations instead of assuming that every reviewer can or cannot see images.
6. Use `write_file` or `patch` within the approved scope when available. Request another format or an appropriate document tool if necessary; do not call Markdown a completed Word or PDF deliverable.

## Check the reader's task

1. Make a short set of realistic questions or actions the intended reader should be able to resolve from the document alone. Include the main decision, required action, important limitations, and evidence needed to trust the conclusions.
2. Check the answers against what the text actually says, not the author's private explanation. Identify missing context, ambiguous terms, unsupported claims, conflicting instructions, and links that do not lead where promised.
3. If an isolated reviewer is available and approved for this data, provide only the agreed document and reader tasks. Otherwise label the review as a same-context check or give the author a practical independent-reader test. Do not pretend a same-context review is blind, require another account, or fabricate another model's response.
4. Repair consequential failures and repeat the affected checks. Stop when the agreed task is satisfied; do not turn review into an endless interview.
5. Ask the author to confirm factual accuracy, sensitive information, commitments, and ownership before any separately authorized publication or external action.

## Deliver

Provide the requested document or feedback, sources where relevant, unresolved decisions, and a concise account of the reader check actually performed. Distinguish verified facts from assumptions and completed file creation from delivery, publication, signing, or approval by another person.

A reader check tests comprehensibility, not factual truth, legal compliance, or universal suitability. For regulated or consequential subjects, verify the relevant sources and framework rather than treating good prose as proof.

## Conceptual reference

Anthropic's publicly described document-collaboration workflow: https://github.com/anthropics/skills/tree/main/skills/doc-coauthoring
