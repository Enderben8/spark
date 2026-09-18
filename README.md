# Spark

A Windows desktop app that drives your own Chrome browser with an AI model: it reads the
text on a page (from the DOM, falling back to OCR), clicks through the flow, answers the
questions that follow from what it read, and keeps going until the score the site reports
reaches your target.

- **Using it?** Start with [`docs/USAGE.md`](docs/USAGE.md).
- **Building on it / curious how it works?** Start with [`docs/BUILD_SPEC.md`](docs/BUILD_SPEC.md).

## Status

All twelve milestones in the build spec are implemented and covered by
automated tests (178 passing — DOM extraction, OCR/vision, the four LLM
providers, the full perceive→classify→act loop, grounded question
answering, score-target stopping, script record/replay, run artefacts, and
the desktop GUI) driven against a local fixture site with real Chromium, no
mocking of the core pipeline.

**What that does *not* cover**, because this project was built in a Linux
sandbox with no Windows machine available: real Windows OCR
(`Windows.Media.Ocr`, tested only against a mocked API surface — see
`src/spark/perception/ocr/WINDOWS_OCR_NOTES.md`), the packaged `.exe`
running on actual Windows (the PyInstaller spec has been built and
smoke-tested, but only producing a Linux binary — see
`packaging/spark.spec`'s own header), full GUI visual polish (verified via
an offscreen Qt platform, not a real display), and every LLM provider
against a live API key (each is implemented against its real, installed
SDK and unit-tested against a mocked network boundary, never called for
real). None of the model names configured as defaults should be trusted
without checking each provider's own current model list first.

Quick start: `pip install -e ".[gemini,anthropic,openai,windows-ocr]"`,
then `python -m spark`. See [`docs/USAGE.md`](docs/USAGE.md) for the rest,
including the required one-time browser sign-in step.
