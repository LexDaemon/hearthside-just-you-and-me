---
name: defuddle-local
description: Clean a publicly accessible article locally after normal extraction fails.
version: 2.0.0
license: MIT
---

# Local Article Cleaning

## Use when
Use only after ordinary extraction fails and the article is public or the user is authorized to access it.

## Prerequisites
The local runner and its pinned dependencies must already be installed in this skill directory through an explicitly approved setup. Do not install them automatically.

This bundle does not include installed Node dependencies. The runner requires Node.js with built-in `fetch` (Node.js 18 or newer) and the packages pinned in `scripts/package.json`: `defuddle` 0.19.4 and `linkedom` 0.18.12.

Upstream source and setup documentation:
- Defuddle: https://github.com/kepano/defuddle
- LinkeDOM: https://github.com/WebReflection/linkedom

After explicit setup approval, install the pinned packages with `npm install` from this skill's `scripts/` directory. Run the bundled cleaner there with `node defuddle.mjs <public-http-or-https-url>`. Copying this skill alone does not make the cleaner ready to run.

## Procedure
1. Confirm authorization, URL, and why the normal extractor failed.
2. Run the local cleaner only within its documented directory and capture errors.
3. Validate title, byline, date, body completeness, and source URL against the original page.
4. Keep the raw page and extracted text separate when preservation is requested.

## Output
Return cleaned text, source URL, extraction caveats, and any missing sections.

## Boundaries
Do not bypass paywalls, authentication, access controls, or terms. Do not send content to third-party services without approval.
