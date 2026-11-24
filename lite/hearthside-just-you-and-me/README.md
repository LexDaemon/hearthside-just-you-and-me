# Hearthside: Just You and Me

![Nori reaching toward the screen](media/portada.png)

**For Hermes Desktop**

A shared room with Nori, a focus garden, and study tools—bringing a little everyday company to your workspace.

## Setup

This package provides the Desktop integration, its local backend, runtime images, and the Hearthside skill. It does not create Nori's dedicated Bot profile or Hearthside project. English and Spanish are selected inside Hearthside.

For local installation from the complete repository, use its [installation guide](../../INSTALL.md). Math previews use bundled MathJax to produce static SVG; the preview frame does not execute scripts.

## Data and network use

Study collections are kept in profile-scoped plugin storage. Weather uses Open-Meteo after you choose a city; usage information comes through Hermes. No telemetry or independent background service is added. Story reads are restricted to the requesting profile's owned Hearthside project; local project text can be shown to the configured model when explicitly loaded.

## License and credits

Created by Lex Daemon. Development assistance: OpenAI models through Hermes Agent, with [Codex](https://github.com/codex) credited on AI-assisted updates. Artwork generation and editing: GPT Image, alongside manual selection, correction, and composition.

See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). Open-Meteo supplies weather data. Third-party components retain their licenses and terms. This is an independent project, not an official Nous Research release.
