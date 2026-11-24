---
name: travel-planner
description: Plan trips, budgets, itineraries, and packing.
---

# Travel Planner

Adapted from ai-labs-claude-skills. Plan travel conversationally by default. Local persistence is optional, explicitly authorized, and independent of Claude.

## When to use
Use for destinations, itineraries, budgets, packing, accessibility, and travel preparation. Do not demand a comprehensive personal profile for a simple question.

## Prerequisites
Text planning uses existing Hermes tools. The two bundled scripts use Python's standard library. Resolve `SKILL_DIR` to this directory, wherever the package is installed; do not assume a category or user path. Persistent mode requires an absolute `NORI_TRAVEL_DATA_DIR` selected and approved by the user. Without it, the scripts refuse to access a database; no default personal directory is created. No key or account is needed for the local scripts. They do not book or retrieve real prices.

## Procedure
1. Gather destination or options, dates, travelers, pace, budget and currency, starting point, and only relevant accessibility or dietary constraints. Ask for country of citizenship or residence only when entry requirements depend on it; do not request document numbers or copies of identity documents.
2. Ask whether to keep the plan only in the conversation or save preferences and trip data locally. Obtain approval for the exact storage directory and data scope before setting `NORI_TRAVEL_DATA_DIR` or invoking database operations. Reading an existing approved database does not authorize adding new sensitive fields or deleting records.
3. Use `web_search` and `web_extract` to verify transport, opening hours, entry requirements, seasonal conditions, and estimates. Use official government and operator sources for consequential claims; distinguish current quotes, estimates, and bookings. Do not assume a country, currency, or hemispheric season.
4. Build a feasible itinerary with travel time, rest, contingencies, accessibility, and alternatives. Preserve uncertainty about prices and availability. Calculate totals with a tool and state the currency, date, exclusions, and assumptions.
5. Include a practical packing and pre-trip checklist. Medical, visa, insurance, and legal questions need relevant official or professional guidance, not generic certainty.
6. Save, update, or export only the approved fields and output paths. Confirm deletion or reset separately. Never upload the database to a provider or create bookings, payments, messages, or calendar events without specific authorization.

## Local helpers
Use `terminal` to run `python3 "SKILL_DIR/scripts/travel_db.py" get_preferences`, `get_trips`, `stats`, or `export` with the approved `NORI_TRAVEL_DATA_DIR` in that process environment. `reset` is destructive and outside routine planning approval.

`travel_db.py` also exports `save_preferences`, `add_trip`, `update_trip`, and budget/itinerary helpers for explicitly approved local writes. `plan_generator.py --trip-id ID --output APPROVED_FILE` generates a template from a saved trip. The generator's activities are placeholders and its budget proportions are heuristic, not researched destinations or live prices. Its checklist dates use the runtime clock. Fill and verify the template before delivery; do not present generated placeholders as a finished itinerary.

## Output
Provide itinerary, dated budget assumptions, packing and preparation, verified source links, and open decisions. A booking remains unconfirmed unless actually authorized and verified.

## Pitfalls
Do not store passport numbers, payment data, passwords, health records, or other unnecessary sensitive information. Keep individuals' data in separate approved directories. Do not add amounts in different currencies or compare trips' aggregate budgets without currency normalization. Protect the JSON data with the user's local storage policy; no encryption is provided by these scripts.

## Verification
Check dates, feasible travel times, arithmetic, currency, current requirements, and source provenance. In persistent mode, test only an authorized directory and verify intended records after modifications. Report planning separately from saving or booking.
