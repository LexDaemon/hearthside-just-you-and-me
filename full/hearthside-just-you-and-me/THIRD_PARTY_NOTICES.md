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
