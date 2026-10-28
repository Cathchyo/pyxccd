# Repository Guidelines

## Project Structure & Module Organization

- `src/python/pyxccd/` contains the Python API, change-detection wrappers, classification, phenology, and PCBF modules. `imagetool/` provides raster preparation and tile-processing utilities.
- `_ccd_cython.pyx` connects Python to the C and Fortran algorithms in `src/cxx/`. CMake files configure the compiled backend.
- `tests/` holds automated tests; `tests/resources/` stores spectral samples, configurations, and model fixtures.
- `docs/source/` contains Sphinx documentation; `tutorials/notebooks/` contains notebooks and supporting GEE/GeoTIFF examples.
- `requirements/` separates build, runtime, test, and documentation dependencies. `.github/workflows/` runs linting and platform-specific packaging checks.

## Build, Test, and Development Commands

Use a virtual environment with Python 3.9 or newer. Native builds require C and Fortran compilers, GSL, and zlib; consult `docs/source/installation.rst` for platform setup.

- `python -m pip install -r requirements/build.txt -r requirements/tests.txt`: install build and test tooling.
- `python -m pip install ".[headless]"`: compile and install the package with headless OpenCV dependencies. Reinstall after native-code changes.
- `python -m pip wheel . --no-deps -w dist`: build a distributable wheel.
- `python run_tests.py`: run tests and package doctests with terminal, HTML, and XML coverage reports.
- `python -m pytest tests/test_pycold.py -q`: run a focused test module.
- `bash run_linter.sh`: run the repository's Flake8 checks; install `flake8` separately.
- `python -m sphinx -b html docs/source docs/build/html`: build documentation after installing `requirements/docs.txt`.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions and modules, `PascalCase` classes, and `UPPER_CASE` constants. Match nearby code and document public parameters and array shapes. Preserve consistency across Python, Cython, and native interfaces. Flake8 checks `E9,F63,F7,F82`; no automatic formatter is configured.

## Testing Guidelines

Use pytest with xdoctest and pytest-cov. Name files `test_*.py` and functions `test_*`. Add regression tests for changed algorithm behavior, using deterministic fixtures under `tests/resources/`. Assert numerical outputs and record fields explicitly. Branch coverage is enabled; no minimum percentage is configured.

## Commit & Pull Request Guidelines

Recent history mixes short imperative subjects with prefixes such as `docs:`. Use a concise, descriptive subject and keep commits focused. PRs should explain the problem, behavior changes, relevant issues, and validation commands with results. Include example outputs or figures when changing scientific results or tutorial visuals, and identify native-build or dependency changes.
