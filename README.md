# OASIS GeoAgent

A model-portable GeoAI agent that combines Glasgow WebGIS interaction, recent
SEPA observations, and deterministic Core Analyst flood-risk workflows.

## Start here: environment, data, and API configuration

For the exact Glasgow 5 m workflow, a new user supplies one licensed UKCEH
file and the credentials needed by the website. One command then downloads and
processes all other analysis data. Never commit API keys or the UKCEH raster.

### What the user must provide

| Item | Needed for | Configuration |
| --- | --- | --- |
| UKCEH LCM 2019 `gb2019lcm25m.tif` | Exact Glasgow data build | Log in, accept the licence, and download it from the [EIDC order page](https://order-eidc.ceh.ac.uk/resources/1GYPT1HZ/order). This is not an API key and the file must not be redistributed. |
| Agent model API key | Real natural-language Agent conversations | Select a supported model in `.env` and add its provider key. `OASIS_MODEL=test` works only for offline smoke tests. |
| CARTO Basemaps API key | A watermark-free website basemap | Add it to the ignored `webgis/frontend/config.local.js`. It does not affect flood calculations. |
| Met Office Site-Specific Forecast key | 24-hour forecast reports and future-pluvial analysis | Add `METOFFICE_SITE_API_KEY` to `.env`. Without it, current observations and static hazard analyses still work. |
| ADMIRALTY Tidal API key | Optional live coastal tidal prediction | Add `ADMIRALTY_API_KEY` to `.env`. Static SEPA coastal analysis does not require it. |

LiDAR, OS OpenData, SEPA Flood Maps, Census, NRS, SIMD, and NRFA inputs do not
need API keys; the exact-data command downloads them from their official
services automatically.

### 1. Install the environment

Python 3.10 or newer is required. From the repository root, run:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

### 2. Configure the website credentials

Edit the ignored `.env` file. Choose one real Agent model/provider and replace
only the placeholders for the features you intend to use:

```dotenv
OASIS_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your-provider-key
METOFFICE_SITE_API_KEY=your-met-office-site-specific-key
# ADMIRALTY_API_KEY=your-admiralty-tidal-key
```

For the CARTO basemap, create its separate ignored browser configuration:

```powershell
Copy-Item webgis/frontend/config.local.example.js webgis/frontend/config.local.js
```

Then set the issued key in `webgis/frontend/config.local.js`:

```javascript
window.OASIS_CONFIG = {
  cartoBasemapKey: "your-carto-basemap-key"
};
```

See [External API keys](#external-api-keys) below for the official application
pages, product selection, domain registration, and feature-specific details.

### 3. Download the one licence-gated source file

Order UKCEH Land Cover Map 2019, accept the
[LCM raster licence](https://eidc.ac.uk/licences/lcm-raster/plain), and extract
`gb2019lcm25m.tif` from the downloaded package. Keep it outside Git or in an
ignored local data directory. Every user must obtain this file under their own
licence; do not upload it to GitHub, a Release, Docker image, or public storage.

### 4. Download and build all Glasgow analysis data

Pass the downloaded TIFF to the single exact-data command:

```powershell
oasis data rebuild `
  --lcm2019 "C:\data\LCM2019\gb2019lcm25m.tif" `
  --accept-licences
```

The command downloads roughly 9.5 GB of official source data, resumes
interrupted downloads, and builds the complete locked EPSG:27700 Glasgow 5 m
input set. Verify the finished installation with:

```powershell
oasis data verify
```

The expected final result is `"ok": true`. Generated inputs and raw caches are
Git-ignored; `REPRODUCIBILITY_MANIFEST.json` records sources and checksums.

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

## Exact Glasgow data build: sources and verification

### Reproduce the exact Glasgow 5 m data from an empty clone

Large inputs are excluded from Git, but their official source identities,
versions, tile list, analysis grid, and licences are locked in
`data/glasgow-5m-sources.json`. The exact workflow uses the same data families
as the current analysis rather than substituting Copernicus DEM or WorldCover:

- Scottish Public Sector LiDAR Phase 5 plus two Phase 3 gap-fill tiles, 0.5 m DTM
  (26 locked tiles, about 9.15 GB)
- UKCEH Land Cover Map 2019, 25 m rasterised land parcels, GB
- OS OpenMap Local buildings, OS Open Greenspace, and OS Open Rivers
- SEPA Flood Maps v3.0 river and coastal probability layers
- Scotland Census 2022 Data Zone tables, NRS lookups/population, and SIMD 2020v2
- NRFA gauged daily flow and catchment daily rainfall for the 14 project stations

UKCEH LCM 2019 is the only input that cannot be fetched anonymously. Each user
must log in to the EIDC order service, accept the raster licence, download the
GB package, and extract `gb2019lcm25m.tif`. Do not commit or publish that raster:
its licence restricts redistribution.

1. Order the dataset at the
   [UKCEH EIDC order page](https://order-eidc.ceh.ac.uk/resources/1GYPT1HZ/order)
   (dataset DOI `10.5285/f15289da-6424-4a5e-bd92-48c4d9c830cc`).
2. Read the [UKCEH LCM raster licence](https://eidc.ac.uk/licences/lcm-raster/plain)
   and the [NRFA terms](https://nrfa.ceh.ac.uk/costs-terms-and-conditions).
3. Run the small preflight before downloading anything large:

   ```powershell
   oasis data preflight `
     --lcm2019 "D:\data\LCM2019\gb2019lcm25m.tif" `
     --accept-licences
   ```

4. Build every input into an empty directory:

   ```powershell
   oasis data rebuild `
     --lcm2019 "D:\data\LCM2019\gb2019lcm25m.tif" `
     --accept-licences
   ```

   By default this writes to `analysis/core-analyst/Input` and keeps reusable
   raw downloads in `analysis/core-analyst/.oasis-data-cache`. Set
   `--input-dir` and `--cache-dir` to test in an isolated directory. Interrupted
   runs reuse completed downloads. Use `--force` only to retrieve every source
   again.

5. Verify CRS, extent, resolution, dimensions, required tables, and NRFA bundles:

   ```powershell
   oasis data verify
   ```

The build writes `REPRODUCIBILITY_MANIFEST.json` with SHA-256 checksums for all
downloaded source files and outputs. All generated rasters are aligned to the
locked EPSG:27700 grid: 5 m cells, 4402 × 3722, bounds
`249424, 655461.5, 271434, 674071.5`.

The historic `Glasgow_Mosaic` used an undocumented edge source for 3,547 cells
and ArcGIS selected another 377 cells differently at the mask boundary. The
versioned `data/glasgow-dtm-edge-patch.csv` preserves those 3,924 legacy edge
values (0.024% of the grid) after the official LiDAR mosaic is built. Everywhere
that both the official tiles and historic DTM contain elevation, the values are
pixel-for-pixel identical. The patch is an auditable derived compatibility
asset, not a replacement for downloading the source terrain.

No API key is required to build the data. LiDAR, OS, SEPA, Census, SIMD, NRS,
and NRFA use official anonymous download services. UKCEH requires a personal
login/licence acceptance, not an API key. Raw UKCEH and NRFA files must remain
local and are not suitable for a public GitHub Release.

For a quick, lower-resolution development stack only, the older command remains:

```powershell
oasis data bootstrap
```

That command intentionally uses Copernicus GLO-30 and ESA WorldCover and is not
the competition/reproduction workflow.

## External API keys

The exact data build needs no API key. Runtime services use the following keys;
none should be committed to Git.

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

### 3. Agent model API key

Offline tests use `OASIS_MODEL=test` and need no model key. For real natural-
language Agent reasoning, configure one supported provider. For example:

```dotenv
OASIS_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your-provider-key
```

The project also supports the documented MiMo configuration in `.env.example`.

### 4. ADMIRALTY Tidal API key (optional)

Only live coastal tidal prediction needs this key. Static SEPA coastal flood-map
analysis and the complete data rebuild work without it. Register through the
[ADMIRALTY Developer Portal](https://developer.admiralty.co.uk/) and set:

```dotenv
ADMIRALTY_API_KEY=your-admiralty-tidal-api-key
```

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
