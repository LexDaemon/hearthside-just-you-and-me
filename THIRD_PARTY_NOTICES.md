# Third-party notices

Hearthside-owned material uses the accompanying LICENSE. Components with separate licenses retain their permissions; Hearthside's restrictions do not override them.

## MathJax

MathJax 3.2.2, The MathJax Consortium — Apache License 2.0. The modular engine from `mathjax-full@3.2.2` is compiled into `desktop/plugin.js`. The integration selects the base, AMS, newcommand, noundefined, and configmacros TeX packages and produces static SVG in a passive preview. The browser-startup bundle is not included. License, integration notice, and version/integrity provenance are in `dashboard/vendor/mathjax-3.2.2/`.

Source: https://github.com/mathjax/MathJax-src/tree/3.2.2

## Weather and location data

Weather data: Open-Meteo, CC BY 4.0. Location search is supplied by Open-Meteo using GeoNames data; attribution to GeoNames is preserved here. Data is formatted for the interface; these services do not endorse Hearthside.

- https://open-meteo.com/
- https://open-meteo.com/en/terms
- https://open-meteo.com/en/docs/geocoding-api
- https://www.geonames.org/
- https://creativecommons.org/licenses/by/4.0/

Data licensing and hosted API access are distinct. The integration uses Open-Meteo's free API, whose service terms restrict it to non-commercial use and impose rate limits. It does not configure a commercial customer endpoint. For a commercial deployment, leave optional weather unconfigured unless the integration is adapted to a properly licensed service plan. Permission for paid professional use of Hearthside itself does not waive provider terms.

## External runtime dependencies and tools

Hermes Agent/Desktop, configured models, Python dependencies (`zstandard`, `fsrs`, `fsrs-rs-python`, and `regex`), and programs or services invoked by skills retain their own licenses and terms. Their distributions are not included in this package; each plugin manifest declares the Python dependencies and versions. FSRS provides the study scheduler through those dependencies. No paid model subscription or provider account is supplied or cloned.

## Full profile workflows

The following workflows are included only in the prepared Full profile. Complete license texts and existing upstream copyright notices accompany each workflow so they remain available after installation. Existing notices are preserved; no third-party copyright ownership is assigned to Hearthside. The CalDAV and document-collaboration instructions are separately written Hearthside implementations, not redistributed copies of external skills. The relationship guidance is published with its creator's authorization and is not a certified clinical product.

| Workflow | Terms | Accompanying files |
|---|---|---|
| `accessibility` | MIT | `UPSTREAM-LICENSE.txt` |
| `anki` | MIT | `LICENSE` |
| `ast-grep` | MIT | `LICENSE`, `UPSTREAM-HERMES-LICENSE.txt` |
| `blogwatcher` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `caldav-calendar` | LicenseRef-Hearthside-Source-Available-1.0 | `LICENSE` |
| `code-wiki` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `contract-compare` | MIT | `LICENSE` |
| `contract-review` | MIT | `LICENSE` |
| `decision-questionnaire` | MIT | `UPSTREAM-LICENSE.txt`, `UPSTREAM-HERMES-LICENSE.txt` |
| `defuddle-local` | MIT | `LICENSE` |
| `dimensional-analysis` | Apache-2.0 | `UPSTREAM-LICENSE.txt` |
| `doc-coauthoring` | LicenseRef-Hearthside-Source-Available-1.0 | `LICENSE` |
| `doc-deliverable-structure` | MIT | `LICENSE` |
| `docker-management` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `domain-intel` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `exploratory-data-analysis` | MIT | `UPSTREAM-LICENSE.txt` |
| `fastmcp` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `godmode` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `grill-me` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `influence-psychology` | MIT | `LICENSE` |
| `invoice-organizer` | Apache-2.0 | `UPSTREAM-LICENSE.txt` |
| `legal-translation` | MIT | `LICENSE` |
| `literature-review` | MIT license | `UPSTREAM-LICENSE.txt` |
| `loopy` | MIT | `LICENSE` |
| `mock-interview` | MIT | `LICENSE` |
| `nutrition-for-training` | MIT | `LICENSE` |
| `oss-forensics` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `personal-presentation-review` | MIT | `LICENSE` |
| `planning-with-files` | MIT | `LICENSE` |
| `privacy-impact-assessment` | MIT | `LICENSE` |
| `qmd` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `recovery-fatigue` | MIT | `LICENSE` |
| `relationship-science-coach` | LicenseRef-Hearthside-Source-Available-1.0 | `LICENSE.txt` |
| `rest-graphql-debug` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `resume-ats-linkedin-optimizer` | MIT | `LICENSE` |
| `rss-feeds` | MIT | `UPSTREAM-HERMES-LICENSE.txt` |
| `scientific-critical-thinking` | MIT license | `UPSTREAM-LICENSE.txt` |
| `scientific-writing` | MIT | `UPSTREAM-LICENSE.txt` |
| `statistical-analysis` | MIT license | `UPSTREAM-LICENSE.txt` |
| `study-strategy-selector` | MIT | `LICENSE` |
| `style-direction-guidance` | MIT | `LICENSE` |
| `style-reference-comparison` | MIT | `LICENSE` |
| `supplements-fitness-claims` | MIT | `LICENSE` |
| `tabular-document-review` | MIT | `LICENSE` |
| `teach` | MIT | `LICENSE` |
| `thinking-first-principles` | MIT | `UPSTREAM-LICENSE.txt` |
| `third-party-skill-curation` | MIT | `LICENSE` |
| `travel-planner` | MIT | `UPSTREAM-LICENSE.txt` |
| `wait-what` | MIT | `LICENSE` |
| `writing-clearly-and-concisely` | Hearthside license (workflow); public-domain 1918 reference | `LICENSE` |

Core-derived workflow notices retain Copyright (c) 2025 Nous Research. Other included notices identify Addy Osmani, Yeongyu Kim, K-Dense Inc., ameliapeng-gif, TJ Boudreaux, and ailabs-393 as applicable. `invoice-organizer` credits ComposioHQ's Apache-2.0 workflow and identifies the Hermes adaptation. `dimensional-analysis` retains its existing Apache-2.0 notice. Original source attributions and reference citations remain in the relevant folders. These credits are not institutional endorsements.
