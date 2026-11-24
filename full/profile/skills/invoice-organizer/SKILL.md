---
name: invoice-organizer
description: Organize invoices with traceable extraction and copies.
license: Apache-2.0
---

# Invoice Organizer

Adapted from ComposioHQ/awesome-claude-skills. Organize supplied financial records without assuming a country, tax regime, currency, retention period, or fiscal year. This is document organization, not accounting certification or tax advice.

## When to use
Use to sort receipts or invoices, reconcile documented expenses, prepare an archive, or export a summary for the user or accountant.

## Prerequisites
Use `search_files` for the approved source folder, `read_file` for readable documents, and `vision_analyze` for images when available. A scanned PDF may need an approved OCR tool; do not invent extracted fields when OCR is unavailable. Use `write_file` for reports and an approved local Python operation through `terminal` to copy files. No sibling skill or account is required for the core workflow. Do not assume email or cloud access.

## Procedure
1. Confirm source folder, destination folder, file scope, organization scheme, and whether the user wants only a proposed plan or execution. Copy originals by default. Obtain separate explicit approval for moves, overwrites, deletion, merging, or recurring automation.
2. Enumerate all candidate files. Keep a manifest of original paths and hashes and reconcile the number found, processed, unreadable, duplicated, and excluded.
3. Extract vendor, invoice identifier, invoice date, description, amount, currency, taxes when explicitly shown, and source locator. Distinguish subtotal, total, balance due, and proof of payment. Preserve original identifiers; do not treat an invoice as evidence it was paid.
4. Mark uncertain or missing fields for review. File timestamps and filename clues may be labeled hints but must not silently become invoice dates. Resolve ambiguous date formats or decimal separators from document context, not nationality assumptions.
5. Propose paths such as `Year/Category/Vendor/YYYY-MM-DD Vendor - Invoice - Description.ext`. Sanitize path separators and reserved names, avoid personal or tax identifiers in filenames, preserve extension, and handle collisions with a stable suffix. Unknown dates belong in a review group rather than a fabricated date.
6. Execute only the approved plan. Copy without overwriting existing files, preserve originals, and log the original-to-destination mapping. Exact byte hashes identify identical files; near-duplicates or multiple representations of one invoice need content review and must not be silently deleted.
7. Produce CSV or an agreed report containing source path, destination path, extracted fields, currency, review flag, and rationale. Quote fields correctly and protect spreadsheet imports from formula injection in untrusted text. Use a calculation tool for totals; aggregate separately by currency and prevent duplicate invoice counting.
8. Verify copies against source hashes, reconciliation counts, and sample extraction. Report all exceptions and incomplete files explicitly.

## Output
Deliver organization manifest, approved copies if requested, per-currency summary, and manual-review items. Fictional examples and estimates must not be presented as processed records.

## Pitfalls
Never label an expense deductible, assign a tax rate, or impose a seven-year retention rule without a verified applicable framework and user/accountant direction. Organizational categories are not legal determinations. Recurring watching, external sharing, and financial-record uploads require separate scope and approval; this skill is not a scheduler or configured connector.

## Verification
Reconcile every input and output, retain originals, check hash equality for copies, preserve source identifiers and currency, and confirm that no unapproved files or destinations changed.
