# OASIS GeoAgent

A model-portable GeoAI agent that combines Glasgow WebGIS interaction, recent
SEPA observations, and deterministic Core Analyst flood-risk workflows.

The complete application now lives in this directory. The interactive map is
`webgis/frontend/index.html`; its browser logic and styles are
`webgis/frontend/demo.js` and `webgis/frontend/demo.css`. GeoServer files,
rasters, and the older standalone map pages remain under `webgis/`.

## Architecture

```text
src/oasis/
├── agent.py          PydanticAI Agent and instructions
├── deps.py           typed, swappable runtime dependencies
├── toolsets/         reusable PydanticAI FunctionToolsets
├── models/           provider-neutral Pydantic models
├── domain/           deterministic geographic and rainfall calculations
├── integrations/     SEPA time-series integration
├── runtime.py        dependency wiring and Agent lifecycle
└── cli.py            deterministic tools and Agent entry point

webgis/
├── frontend/         Leaflet map and Agent chat interface
├── geoserver/        GeoServer styles and configuration
├── data/             project metadata and source data
└── .runtime/         local GeoServer runtime and published raster
```

Codex can run and debug the same application through the `oasis` CLI. The CLI
does not contain a second implementation of the tools; it calls the same Python
services used by the PydanticAI agent.

## Current vertical slice

- Resolve a small set of named study areas, starting with Glasgow.
- Find nearby SEPA stations and summarize recent 15-minute level observations.
- Summarize nearby 15-minute rainfall observations over 1/3/6/24-hour windows.
- Geocode natural-language locations and retain multi-turn map context.
- Calculate a whole-Glasgow current-hazard prototype from Core Analyst terrain,
  runoff and imperviousness inputs plus the latest SEPA rainfall observations.
- Query the generated 5 m raster and display it through GeoServer.
- Find nearby facilities and plan, analyse, rank, and display driving routes.
- Produce typed provenance, warnings, and structured agent output.
- Inspect machine-readable data readiness without fabricating unavailable data.
- Run pluvial, fluvial, coastal, exposure, vulnerability, priority, scenario,
  sensitivity, and combined-hazard analysis through controlled Agent tools.

The observation Agent exposes three tools:

- `resolve_area`
- `get_recent_water_levels_near_location`
- `get_recent_rainfall_near_location`

The browser Spatial Agent exposes twenty-five composable tools. Nine cover
geocoding, nearby-place search, route retrieval and analysis, route ranking,
map display, and session cleanup. One retrieves recent observations from nearby
SEPA rain gauges. Four hazard tools expose snapshot status, whole-area
recalculation, point lookup, and layer visibility. Eleven extended Core Analyst
tools cover data readiness, controlled input preparation, pluvial/fluvial/coastal
hazard analysis, combined hazard, coastal dynamic evidence, exposure,
vulnerability, explicit priority ranking, scenario comparison, and sensitivity.
Every point and route risk analysis uses the same latest calculated raster.
Core Analyst source is under
`src/core_analyst/`; its local geodatabase inputs and generated rasters are
under `analysis/core-analyst/` and are intentionally ignored by Git.

Extended analysis results are persisted under
`analysis/core-analyst/outputs/agent/runs/<run_id>/result.json`. The Agent sees a
compact typed summary and uses `run_id` to connect dependent analyses; it cannot
choose arbitrary server filesystem paths.

The entire application preserves Core Analyst's native class convention:
`1 = Low`, `2 = Medium`, `3 = High`, and `0 = NoData`.

The current version does **not** claim to issue an operational flood warning.
Rainfall and river-level observations are returned with provenance and quality
warnings for human interpretation. The current-hazard calculation is an MVP
prototype, not a forecast or official warning; its weights and thresholds need
scientific validation. Priority weights must be explicit and are treated as
human value judgements. The random-forest future-pluvial component remains an
experimental proxy and must not be presented as a validated forecast. Exposure
and vulnerability analyses return `partial` or `unavailable` when verified
population, building, facility, geography, Census, or SIMD inputs are missing.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## CLI

```powershell
oasis doctor
oasis tool area --place glasgow
oasis tool water-levels --place glasgow --radius-km 30 --days 1 --limit 3
oasis tool rainfall --place glasgow --hours 24 --limit 3
oasis agent "Assess the available flood evidence for Glasgow" --model test
```

For a real agent run, configure a PydanticAI-supported model and its provider
credentials, then pass its model identifier with `--model` or `OASIS_MODEL`.

## Design rule

Provider-specific API code belongs under `integrations/`. Reusable
`FunctionToolset` objects expose domain-level operations and return Pydantic
models. `agent.py` only composes those toolsets; `runtime.py` binds concrete
providers through `Deps`. This follows PydanticAI's Agent + toolset pattern and
lets another provider replace SEPA without rewriting the Agent loop.
