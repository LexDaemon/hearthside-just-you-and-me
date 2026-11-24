---
name: caldav-calendar
description: Work with CalDAV calendars using vdirsyncer and khal, with explicit approval for synchronization and edits.
version: 2.0.0
license: LicenseRef-Hearthside-Source-Available-1.0
platforms: [linux]
metadata:
  hermes:
    requires:
      bins: [vdirsyncer, khal]
---

# CalDAV Calendar Workflows

Original Hearthside instructions for the documented command-line interfaces of vdirsyncer and khal. These programs and calendar providers are separate dependencies; this skill neither installs them nor supplies an account or credentials.

## Establish the operation

1. Identify the calendar, date range, intended action, and relevant time zone. Do not assume a country, provider, default calendar, or source of truth.
2. Check the available tool versions with `vdirsyncer --version` and `khal --version`. This workflow targets Linux. Do not run `apt` or apply Linux paths to a different platform.
3. Agree whether to inspect existing local data, synchronize remote data, change events, or configure a new account. Calendar synchronization can write both locally and remotely; permission to search a calendar is not permission to synchronize or edit it.
4. Never request passwords, app passwords, access tokens, or recovery codes in chat. Use a user-approved local credential store/helper. Do not print configuration files or calendar bodies indiscriminately; they may contain private information.

## Read an existing calendar

Use the user's configured calendar names. The following are command patterns, not claims that a calendar or event exists:

```bash
khal list today 7d
khal list -a CalendarName today 7d
khal list 2030-01-15 2030-01-20
khal search "search phrase"
khal list --format "{start-date} {start-time} | {title}" today 7d
```

Date examples are illustrative. Resolve the requested dates and time zone before using them. Listing and searching are not intended to edit events, although the tools may maintain normal local caches. Do not claim remote freshness without an authorized successful synchronization.

For structured summaries, select only necessary fields. Useful khal formatting fields include `{uid}`, `{title}`, `{start-date}`, `{start-time}`, `{end-date}`, and `{end-time}`; verify available fields and option placement against the installed version's `--help`. Preserve UIDs and source calendar names exactly. Do not merge similar titles as though they were the same event.

## Synchronize deliberately

Use the existing configured pair and the scope the user approved:

```bash
vdirsyncer sync
```

A first-time `vdirsyncer discover` discovers collections and can update synchronization configuration/state; it also requires approval. Do not introduce a sync schedule, daemon, conflict winner, deletion policy, or account link as part of an ordinary lookup. If a conflict appears, inspect the actual conflict and ask which version to preserve. Never choose an automatic overwrite policy merely to suppress the error.

## Create, edit, or delete

Before writing, confirm the calendar, title, exact date/time, time zone, duration, recurrence if any, and whether remote upload is included in the authorization. Use the installed version's help for ambiguous date syntax.

```bash
khal new -a CalendarName 2030-01-15 09:00 10:00 "Example appointment"
khal new -a CalendarName 2030-01-15 "Example all-day entry"
khal edit "exact search phrase"
```

These examples do not authorize execution. Avoid matching and changing several events by title alone. `khal edit` is interactive: use an approved TTY interaction or give the user the exact command. Its edit workflow also offers deletion; do not invent a `khal delete` subcommand or simulate unexplained keystrokes. Confirm deletion separately and preserve a backup/export when appropriate.

After an authorized local change, synchronize only if remote upload was also approved. Read back the specific event and its calendar, time, title, and intended recurrence. Distinguish a local edit from a verified server update; report a failed upload rather than claiming completion.

## Set up a new local connection

Setup is optional and requires authorization for the exact configuration and storage paths. Common configuration locations are `~/.config/vdirsyncer/config` and `~/.config/khal/config`; do not overwrite existing files.

Build the configuration from the user's provider details and the installed versions' official documentation:

- vdirsyncer needs a general status path, a pair connecting the selected collections, a `caldav` storage with provider URL and username, and a `filesystem` storage for `.ics` files. Configure credential retrieval through the user's approved helper rather than embedding a secret in the skill or a shared document.
- khal needs a calendar entry pointing to the approved local `.ics` directory and the user's date/time preferences. Choose a default calendar only with the user's agreement.
- Discover collections, inspect the proposed mapping, then perform the approved initial sync. Verify a listing before declaring setup successful. A configuration template is not a connected account.

Use the provider's current official instructions for iCloud, Google, Fastmail, or Nextcloud. Provider authentication and application-password requirements differ; do not transplant one provider's settings into another.

## Troubleshooting and delivery

Separate authentication failure, network failure, missing dependencies, incorrect collection mapping, conflicts, and stale caches. Do not delete calendar files or databases to fix a listing problem. Cache rebuilds and destructive recovery require an approved scope and preservation of authoritative `.ics` data.

Return the actual results, calendar/time-zone context, freshness status, and any unresolved conflict or failed operation. Do not expose credentials or unrelated events. No automatic recurring synchronization, booking, payment, message, or external sharing is implied.

## Official references

- vdirsyncer setup and synchronization: https://vdirsyncer.pimutils.org/en/stable/tutorial.html
- khal command usage and formatting: https://khal.readthedocs.io/en/latest/usage.html

This guidance is not distributed by, affiliated with, or endorsed by those projects or calendar providers. Their software and services retain their own licenses and terms.
