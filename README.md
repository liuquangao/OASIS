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
  sensitivity, combined-hazard, and one-click current/future batch analysis.
- Publish generated rasters to GeoServer and add raster/GeoJSON results to the
  website's interactive layer list.
- List and query downloaded NRFA historical river-flow and catchment-rainfall
  daily series.
- Produce a component/data plan for transferring the framework to a new area
  or hazard without claiming that missing models or data already exist.

The observation Agent exposes three tools:

- `resolve_area`
- `get_recent_water_levels_near_location`
- `get_recent_rainfall_near_location`

The browser Spatial Agent exposes thirty-one composable tools. Nine cover
geocoding, nearby-place search, route retrieval and analysis, route ranking,
map display, and session cleanup. One retrieves recent observations from nearby
SEPA rain gauges. Four hazard tools expose snapshot status, whole-area
recalculation, point lookup, and layer visibility. Seventeen extended Core Analyst
tools cover data readiness, controlled input preparation, pluvial/fluvial/coastal
hazard analysis, combined hazard, coastal dynamic evidence, exposure,
vulnerability, explicit priority ranking, scenario comparison, sensitivity,
one-click all-hazard execution, NRFA station/series queries, and generalized
analysis planning, and configuration-driven hazard extension execution.
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
Copy-Item .env.example .env
```

### Reproduce the analysis data from an empty clone

Large local rasters are deliberately excluded from Git. A new clone can build
the minimum real-data Glasgow hazard stack directly from public services:

```powershell
oasis data bootstrap
```

This downloads and clips Copernicus DEM GLO-30, ESA WorldCover 2021, and the
six current SEPA river/coastal flood-map probability layers. It writes the
compatible raster contract under `analysis/core-analyst/Input/OASIS_Rasters/`
and records source URLs, retrieval time, actual resolution, attribution, and
SHA-256 checksums in `analysis/core-analyst/Input/OPEN_DATA_BOOTSTRAP.json`.
No API key is required. The current Glasgow profile downloads about 66 MB and
derives slope and the explicit topographic flow proxy from the DEM at analysis
time.

For building-footprint exposure as well, run:

```powershell
oasis data bootstrap --include-exposure
```

That option uses the anonymous OS Downloads API to fetch the current OS
OpenMap Local `NS` shapefile package, verifies its published MD5 checksum, and
clips buildings to the Glasgow analysis extent. It is a much larger download
(currently about 420 MB compressed). Census population, SIMD vulnerability,
NRFA history, and licensed/specialist datasets remain separate optional inputs;
the application reports them as unavailable rather than substituting invented
values.

The open-data profile is intentionally lower resolution than the original
local 5 m OASIS geodatabase. Legacy filenames are retained only so the same
analysis interfaces can consume either profile; the actual grid resolution is
always stated in the bootstrap manifest. SEPA data must retain its SEPA/OGL
attribution, ESA WorldCover is CC BY 4.0, and Copernicus DEM attribution is
included in the generated manifest.

## External API keys

Two API keys are needed for the complete browser experience and 24-hour
pluvial forecast workflow. Neither key should be committed to Git.

### 1. CARTO Basemaps API key

The Leaflet map uses CARTO Voyager as its visual basemap. CARTO now requires a
basemap key; without it, the tiles display an `API KEY REQUIRED` watermark.
This key affects only the background map and does not affect flood calculations.

1. Request a free key at
   [CARTO Basemaps API Keys](https://carto.com/basemaps/apikey/).
2. Register `localhost` and `127.0.0.1` for local development, one domain per
   line. Add the deployed website domain before publishing the application.
3. Create the ignored local configuration from the supplied example:

   ```powershell
   Copy-Item webgis/frontend/config.local.example.js webgis/frontend/config.local.js
   ```

4. Set the issued key in `webgis/frontend/config.local.js`:

   ```javascript
   window.OASIS_CONFIG = {
     cartoBasemapKey: "your-carto-basemap-key"
   };
   ```

The free basemap service currently includes up to five million tile requests
per calendar month. Keep both CARTO and OpenStreetMap attribution visible. If
old watermarked tiles remain after adding the key, force-refresh the browser to
clear its tile cache. `config.local.js` is ignored by this repository.

### 2. Met Office Site-Specific Forecast API key

The future-pluvial workflow samples Met Office hourly point forecasts and
interpolates the forecast precipitation over the Glasgow analysis grid. Without
this key, current SEPA rainfall observations, fluvial analysis, and coastal
analysis still work, but future rainfall and surface-water flood risk remain
unavailable and the 24-hour report is marked as partial.

1. Register at the
   [Met Office Weather DataHub](https://datahub.metoffice.gov.uk/).
2. Choose a Site-Specific forecast product and subscribe to a suitable plan.
   The [Site-Specific pricing page](https://datahub.metoffice.gov.uk/pricing/site-specific)
   includes free entry plans.
3. Copy the API key shown when the subscription is created. Weather DataHub
   also lists credentials under **My Subscriptions**.
4. Add the key to the repository-root `.env` file:

   ```dotenv
   METOFFICE_SITE_API_KEY=your-met-office-site-specific-key
   ```

5. Restart the Agent API, then verify the full workflow:

   ```powershell
   oasis all-hazards --forecast-horizon-hours 24 --no-publish
   ```

The key must belong to the subscribed Site-Specific product; the separate Met
Office Atmospheric, Map Images, and NSWWS warning keys do not replace it.

## CLI

```powershell
oasis doctor
oasis tool area --place glasgow
oasis tool water-levels --place glasgow --radius-km 30 --days 1 --limit 3
oasis tool rainfall --place glasgow --hours 24 --limit 3
oasis nrfa --dataset nrfa_historical_rainfall
oasis nrfa --dataset nrfa_historical_river_flow --station-id 84001 --start-date 2020-01-01 --end-date 2020-12-31
oasis all-hazards --static
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
