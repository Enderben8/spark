# Spark

A Windows desktop app that drives your own Chrome browser with an AI model: it reads the
text on a page (from the DOM, falling back to OCR), clicks through the flow, answers the
questions that follow from what it read, and keeps going until the score the site reports
reaches your target.

- **Using it?** Start with [`docs/USAGE.md`](docs/USAGE.md).
- **Building on it / curious how it works?** Start with [`docs/BUILD_SPEC.md`](docs/BUILD_SPEC.md).

## Status

All twelve milestones in the build spec are implemented and covered by
automated tests (179 passing, 1 skipped, on both Linux and **real Windows 11**
— DOM extraction, OCR/vision, the four LLM providers, the full
perceive→classify→act loop, grounded question answering, score-target
stopping, script record/replay, run artefacts, and the desktop GUI), driven
against a local fixture site with real Chrome, no mocking of the core
pipeline.

**Verified on real Windows:** the shipped `Windows.Media.Ocr` engine (via
`winsdk`) reads the fixture passages correctly, including the two-column
layout; real headed Chrome launches on a dedicated profile with the debug
port answering; API keys round-trip through Windows Credential Manager; the
PyInstaller build produces a `Spark.exe` whose window opens and renders.

**Still not verified, because it needs things this environment lacks:** any
LLM provider against a live API key (each is implemented against its real,
installed SDK and tested against a mocked network boundary), a complete task
run against a real site, rotated-text OCR, and the sign-in flow against a real
login. None of the model names configured as defaults should be trusted
without checking each provider's own current model list first.

**Python 3.11 or newer is required** (3.12 tested); a stock Python 3.10 will
not install this.

Quick start: `pip install -e ".[gemini,anthropic,openai,windows-ocr]"`,
then `python -m spark`. See [`docs/USAGE.md`](docs/USAGE.md) for the rest,
including the required one-time browser sign-in step.
