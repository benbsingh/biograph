# Copilot instructions for Biograph

## Project overview

This repository is the Biograph app, a Frappe/ERPNext-based healthcare management system. The main app lives under `healthcare/`, while the patient-facing portal is a separate Vue/Vite app in `patient_portal/`. The project is designed to run inside a Frappe bench and installed as the `healthcare` app on a site.

## Build, test, and lint commands

### Python / app checks

Run the repo lint/format hooks before finishing changes:

```bash
pre-commit run --all-files
```

The repository CI uses a Frappe bench environment and runs app tests like this from the bench root:

```bash
bench --site test_site run-parallel-tests --app healthcare --total-builds 1 --build-number 1
```

For a single focused test file in the ABDM area:

```bash
pytest healthcare/regional/india/abdm/tests/test_token_security.py -v --tb=short
```

Other examples in the repo similarly use:

```bash
pytest healthcare/regional/india/abdm/tests/test_e2e_flows.py -v --tb=short
pytest healthcare/regional/india/abdm/tests/test_wasa_security.py -v --tb=short
```

CI also compiles Python files to catch syntax issues:

```bash
python -m compileall -f .
```

### Frontend / patient portal

From the repo root:

```bash
yarn install
yarn build
```

The root `package.json` delegates the build to the patient portal:

```bash
cd patient_portal && yarn build
```

For local patient portal development:

```bash
cd patient_portal && yarn dev
```

## High-level architecture

- `healthcare/` is the Frappe app package. Most business logic and ERP metadata live under `healthcare/healthcare/`:
  - `doctype/` contains DocTypes and server/client code for medical entities and workflows.
  - `api/` contains Python API endpoints and custom business logic.
  - `report/`, `workspace/`, `dashboard_chart/`, and `number_card/` define ERPNext views and analytics.
  - `setup/` and `patches/` handle app setup and migration logic.
- `healthcare/patches/` contains versioned migrations; add incremental patch files rather than writing one-off DB changes in ad hoc places.
- `healthcare/public/` and `healthcare/www/` are app-level static assets and page entry points.
- `patient_portal/` is a separate Vue + Vite app for the patient-facing portal; the build output is emitted to the app's public assets path, not manually maintained as source.
- `frontend/abha-ui/` is another front-end workspace and is not the default app entry unless a task explicitly targets it.

The project is structured as a metadata-driven Frappe app: custom forms, fields, permissions, and workflows are usually modeled in DocTypes and hooks instead of a pure object-oriented backend service layer.

## Key conventions specific to this repo

- This repository follows the Frappe app layout, so server-side changes often belong under `healthcare/healthcare/...` rather than a separate `src/` or `services/` tree.
- Keep migrations in versioned patch folders (`healthcare/patches/v0_0`, `v15_0`, `v16_0`) when schema or data changes are needed.
- Python formatting is enforced by Ruff with a line length of 110; the repo config also sets tab-based formatting for Python code. Keep changes compatible with the existing Ruff config in `pyproject.toml`.
- Pre-commit hooks run Prettier for JS/Vue/CSS and Ruff for Python; do not bypass them when changing tracked frontend or Python files.
- The patient portal is built with Vite and outputs assets under the app's static path. If you modify the Vue app, run the portal build rather than editing generated bundles by hand.
- This repo is not a standalone Node-only app; it is a Frappe app that also includes front-end assets. For server-side work, prefer the Frappe/ERPNext patterns already used in the app rather than introducing framework-agnostic abstractions.

## Repository signals to preserve

- README describes the app as Biograph / Marley Health enhancements for Frappe ERPNext.
- CI is set up for a bench-based server test environment with MariaDB and Python 3.14.
- Pre-commit is the standard gate for local correctness, and the app uses Ruff, Prettier, and Semgrep in CI.
