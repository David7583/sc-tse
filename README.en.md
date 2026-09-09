# SC-TSE · Integration MVP

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22669717.svg)](https://doi.org/10.5281/zenodo.22669717)

[中文](README.md)

This local Integration MVP connects the existing SC-TSE spatial computation pipeline to a two-dimensional workspace. Load a structured project, select a block, edit its center coordinates, and explicitly rerun the complete pipeline.

This is a test release of the integrated entry point, not completion of the full SC-TSE roadmap or professional approval of engineering outputs.

## Features

- Load the bundled synthetic four-block case or a project JSON matching the contract.
- Display explicit or computed Site/Block geometry, RoadGraph polylines, and Phase4 road centerlines.
- Edit `center_x / center_y`; edits immediately clear previous computational results.
- Run one backend chain: `Precheck → Layout → Connectivity → RoadGraph → Phase4 → Access`.
- Show `COMPLETED / WAITING_FOR_EXTERNAL_UPDATE / ERROR`, blocking nodes, and key metrics.
- Show actual versus required minimum distances from backend evidence, with expandable raw diagnostics. The current interface labels are Chinese.

## Requirements and installation

Validated on Windows with Python 3.12.7 and Chrome. Python 3.12 is recommended; other operating systems and Python versions have not been validated. The application does not require Node.js, model credentials, databases, or external services. Initial dependency installation requires access to a Python package index.

Open PowerShell at the extracted repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
```

`requirements.txt` declares direct dependencies. `requirements-lock.txt` pins the complete Python environment used for validation. Do not copy a virtual environment from another installation.

## Launch and try it

Double-click `scripts/action/development/scripts/sc_tse/frontend/launch_sc_tse_integration_v0001.cmd`, or run:

```powershell
.\.venv\Scripts\python.exe -B scripts/action/development/scripts/sc_tse/frontend/run_sc_tse_integration_v0001.py --open
```

Default URL: `http://127.0.0.1:8770`. Press Ctrl+C in the server window to stop it. Startup failures retain diagnostic output.

1. Run the default four-block case; expect `COMPLETED`.
2. Select `B3_ADMIN`, keep `center_x=75`, change `center_y` from `130` to `135`, and rerun. Geometry and roads update; Access reports `PASS`.
3. Change `center_y` to `129` and rerun. The actual gap to B1 is 12 m against a required minimum of 13 m; Layout blocks the chain.
4. Restore `135` and rerun to recover. Edits never trigger automatic computation.

## Inputs, states, and limitations

Example project: `scripts/action/development/scripts/sc_tse/frontend/integration_cases/gc_road_001_v0001.json`. It contains synthetic validation data, not surveyed data.

Project loading accepts only JSON matching `integration_schemas/contract_v0001.json`, up to 2 MiB. CAD files, natural language, and arbitrary JSON are not supported. IDs, coordinate frames, constraints, Gates, connection requests, and engineering templates must be consistent.

- The UI supports unrotated rectangular blocks and polygon sites, with dimensions in meters.
- All block positions are `FIXED` for each run. The system validates and computes without moving other blocks to resolve conflicts. Gates retain their local offsets when a block moves.
- `COMPLETED` means all six required nodes passed. `WAITING_FOR_EXTERNAL_UPDATE` requires revised input. `ERROR` covers request, adaptation, or transport errors. Unexecuted nodes do not receive fabricated results.
- Roads are computed centerlines, not complete road surfaces or construction drawings. Uncomputed landscaping, parking, and building details are excluded.
- No dragging, real-time or incremental recomputation, 3D, automatic optimization, advanced comparisons, complex layers, WebSocket, queues, or microservices.
- Edited projects are not persisted; refreshing loses edits. Original case files remain read-only.
- Temporary workflow paths are execution traces, not persistent resume locations. The supported release entry point is the Integration API; other Providers advertised by internal generic DAG modules are outside this package's supported scope.

## Structure and API

```text
config/action/config/                 Pipeline configurations
scripts/action/development/scripts/sc_tse/
  *.py                                27 core dependency modules
  schemas/                            Backend contracts
  frontend/                           Current UI, API, case, and launcher
tests/                                API and optional browser acceptance
SOURCE_MANIFEST.json                   Source hashes and copy adaptations
PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md  Original format document/root marker
```

Keep the relative structure and root marker intact. Lower-numbered modules such as `orchestrator_v0003` and `orchestration_state_v0002` are real dependencies of the current chain, not historical copies. Old Showcase code, archives, independent experiments, databases, and historical run outputs are excluded.

The frontend handles input and presentation only. The backend adapts structured requests into a single call to `run_sc_tse_orchestrator_v0004.run_workflow`.

| Endpoint | Purpose |
| --- | --- |
| GET `/api/project` | Load bundled project and explicit geometry |
| POST `/api/project` | Validate and load a project |
| POST `/api/run` | Run the complete chain and return structured results |

POST requires `Content-Type: application/json`, `X-SC-TSE: integration-v1`, and a same-origin request. The server binds only to `127.0.0.1`. This is a local single-user tool, not a public web service. Concurrent computation receives HTTP 409; there is no background queue.

## Tests

Run eight API tests without starting the HTTP server:

```powershell
.\.venv\Scripts\python.exe -B tests/test_integration_mvp_v0001.py
```

Optional browser acceptance requires Node.js, npm, Chrome, and Playwright. These are not application runtime dependencies:

```powershell
npm ci
$env:DEMO_BROWSER_CHANNEL="chrome"
$env:SC_TSE_TEST_OUTPUT=Join-Path $env:TEMP "sc-tse-browser-results"
npm test
```

Start this package's server in another terminal first. Tests use port 8770 by default; override with `SC_TSE_TEST_URL`. Do not target a different build. Tests cover project loading, full computation, edits, distance failures, recovery, error responses, transport failure, and layout; screenshots and reports go to the explicit output directory.

## Troubleshooting

- Python missing: install Python 3.12 and check `py -3.12 --version`.
- `MissingRuntime`: create `.venv` at the repository root and preserve its directory structure and marker file.
- Port in use: stop the old server or change the port in `frontend/integration_config_v0001.json`.
- Old page visible: refresh, reload the case, and rerun.
- Coordinates blocked: inspect actual constraint evidence; several original gaps are exactly at their hard minimum.
- Missing module: install locked requirements with the `.venv` interpreter, not a different Python installation.

## Release and rollback

Upload only this directory. Exclude virtual environments, `node_modules`, databases, credentials, logs, and test artifacts. Common exclusions are in `.gitignore`. Do not upload the sibling validation workspace or internal acceptance artifacts.

Runtime files are copied from the validated implementation without changing or moving the original project. Test copies adapt paths, reference loading, and the distance-display assertion. See `RELEASE_VALIDATION.md` for validation results. A project license is intentionally not added at the owner's request.

Rollback consists of stopping this copy and returning to the original project. This package does not modify the original project, databases, or source data.

The copied CMD launcher uses CRLF instead of LF to correct Windows command parsing; its logic is unchanged. `.gitattributes` preserves CMD line endings after checkout. The actual launcher dry-run passed without parsing errors.
