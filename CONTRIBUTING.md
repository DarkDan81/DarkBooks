# Contributing to DarkBooks

Small fixes, useful workflows and honest feedback are welcome. This is an early
project, so please open an issue before taking on a large change.

## Development

Use Python 3.11+, create a virtual environment, then:

```shell
python -m pip install -e .
python -m unittest discover -s tests
python launch.py
```

No live model server or API key is needed for the test suite. Integration tests
start local fake model APIs and real application workers, exercise both image
adapters, pause after an image, resume without duplicate requests, and validate
the exported FB2. Optional experimental OpenCV face tests are skipped when those
dependencies are absent; face correction is not a web-app feature.

## Architecture

| Area | Files |
|---|---|
| Browser UI and translations | `book_illustrator/web/` |
| Loopback HTTP API | `book_illustrator/webapp.py` |
| Projects, immutable settings, worker | `book_illustrator/app_jobs.py` |
| Book parsing and validated export | `book_illustrator/core.py` |
| Scene analysis | `production.py`, `visual_analysis.py` |
| Provider-neutral image routing | `image_adapters.py` |
| Model transport and ComfyUI workflows | `backends.py`, `resources/workflows/` |

See [the adapter contract](docs/ADAPTERS.md) before adding a provider. Keep the book
pipeline independent of model-specific settings and preserve resumable cache keys.
Do not accept truncated model output or silently reuse incompatible generations.

## Interface

English is the first-run default. All interface strings must have an English and
Russian entry in `web/strings.json`. Do not translate the user's book, prompts,
or model names when the interface language changes. Keep advanced implementation
details out of the main reading/creation flow; put them in the guide or advanced controls.

No external fonts, CDN scripts, analytics or UI build step are needed.

## Windows release

On Windows, install the dependencies into the Python used to build:

```shell
python scripts/build_portable.py
```

The script creates `dist/DarkBooks-Windows-x64.zip`, bundles that Python plus
Pillow/lxml, and runs an isolated import/resource check. It excludes settings,
projects and model weights. Only the original DarkBooks sample story is included.
The build report lists the staging folder. To exercise workers with its bundled
interpreter, set `BOOK_TEST_PORTABLE_ROOT` to that folder and run `test_webapp.py`.

CI tests Windows/Linux on Python 3.11 and 3.14, then builds the portable on Windows.
Pushing a `v*` tag builds and publishes a GitHub release through the release workflow.

Never commit API keys, downloaded weights, personal books, `.env`, populated
`data`, or local output directories. Use the included sample for reproducible reports.
