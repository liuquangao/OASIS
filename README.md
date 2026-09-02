# OASIS GeoAgent

A model-portable GeoAI agent that combines Glasgow WebGIS interaction, recent
SEPA observations, and deterministic Core Analyst flood-risk workflows.

## Start here: environment, data, and model configuration

For the exact Glasgow 5 m workflow, a new user supplies one licensed UKCEH
file and the credentials needed by the website. One command then downloads and
processes all other analysis data. Never commit API keys or the UKCEH raster.

### What the user must provide

| Item | Needed for | Configuration |
| --- | --- | --- |
| UKCEH LCM 2019 `gb2019lcm25m.tif` | Exact Glasgow data build | Log in, accept the licence, and download it from the [EIDC order page](https://order-eidc.ceh.ac.uk/resources/1GYPT1HZ/order). This is not an API key and the file must not be redistributed. |
| Agent model access | Real natural-language Agent conversations | Choose either a hosted model API or a locally deployed vLLM model. `OASIS_MODEL=test` works only for offline smoke tests. |
| CARTO Basemaps API key | A watermark-free website basemap | Add it to the ignored `webgis/frontend/config.local.js`. It does not affect flood calculations. |
| Met Office Site-Specific Forecast key | 24-hour forecast reports and future-pluvial analysis | Add `METOFFICE_SITE_API_KEY` to `.env`. Without it, current observations and static hazard analyses still work. |
| CEDA archive access | Reproduce the 6–7 October 2023 historical validation | Set `CEDA_ACCESS_TOKEN`, `CEDA_USERNAME`/`CEDA_PASSWORD`, or point `OASIS_HISTORICAL_UKV_PATH` at a downloaded precipitation file. |
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

### 2. Configure the model and website credentials

Edit the ignored `.env` file. Choose either a hosted model API or the local
deployment described in [Agent model configuration](#3-agent-model-configuration-two-modes),
then replace only the placeholders for the features you intend to use. This
example uses a hosted OpenAI API:

```dotenv
OASIS_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your-provider-key
METOFFICE_SITE_API_KEY=your-met-office-site-specific-key
OASIS_METOFFICE_SAMPLE_GRID_SIZE=5
# CEDA_ACCESS_TOKEN=your-short-lived-token
# ADMIRALTY_API_KEY=your-admiralty-tidal-key
```

`OASIS_METOFFICE_SAMPLE_GRID_SIZE` controls the square SiteSpecific forecast
sampling grid. The default `5` requests 25 points once per all-hazards run and
shares the resulting rainfall grid across pluvial, fluvial, and coastal models.
Valid values are 3–9; denser grids improve spatial sampling but consume more API
quota. This point sampling plus IDW interpolation remains a prototype substitute
for a native gridded rainfall or radar product.

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

### Website startup preflight and automatic preparation

The WebGIS calls `POST /setup/initialize` when it opens. Before enabling the
conversation controls, the startup panel:

- runs the same complete `oasis data verify` contract used by the CLI;
- reports whether the language model, Met Office forecast, optional ADMIRALTY,
  and browser-side CARTO configuration are present without exposing key values;
- automatically downloads and processes missing anonymous social-risk inputs
  when the existing core dataset makes that repair possible; and
- polls a background initialization job until verification succeeds or an
  actionable failure is returned.

A complete rebuild is deliberately gated because UKCEH LCM 2019 requires a
personal download and explicit licence acceptance. To let the webpage start
that rebuild automatically, add these ignored local settings and restart the
Agent API:

```dotenv
OASIS_LCM2019_PATH=/absolute/path/to/gb2019lcm25m.tif
OASIS_ACCEPT_DATA_LICENCES=true
OASIS_AUTO_PREPARE_DATA=true
```

The build may download about 9.5 GB. It runs in the API background and reuses
the normal `.oasis-data-cache`; refreshing the browser does not launch a second
job. Set `OASIS_AUTO_PREPARE_DATA=false` to keep the status checks while running
all data commands manually. `GET /setup/status` returns the same machine-readable
state shown in the panel.

The complete application now lives in this directory. The interactive map is
`webgis/frontend/index.html`; its browser logic and styles are
`webgis/frontend/demo.js` and `webgis/frontend/demo.css`. GeoServer files,
rasters, and the older standalone map pages remain under `webgis/`.

## Architecture

```text
src/oasis/
├── agent.py          PydanticAI Agent and instructions
├── assessment.py     typed HITL plans and persistence
├── assessment_jobs.py confirmed asynchronous execution and audit traces
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
- Run one deterministic Census 2022 Data Zone assessment that combines hazard,
  population/building/facility exposure, social vulnerability, explicit
  priority scenarios, and paper-ready sensitivity outputs.
- Route requests with a compact no-thinking intent model, dynamically expose at
  most eight relevant tools, and require confirmation before an integrated run.
- Re-rank persisted Hazard/Exposure/Vulnerability components after a stakeholder
  changes weights or SIMD inclusion, without requesting weather or rerunning Hazard.
- Run a no-time-leakage 6–7 October 2023 forecast-input and decision-stability
  validation from CEDA UKV forecasts and downloaded NRFA observations.
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

The browser Agent catalog contains composable map, observation, routing and Core
Analyst tools, but the model no longer receives the whole catalog. A structured
`AnalysisIntent` pass sees only the current question and compact map state.
PydanticAI `FilteredToolset` then exposes 3–8 tools for that intent. Citywide
Risk/Exposure/Vulnerability/Priority and historical-validation requests return an
editable `AssessmentPlan`; they do not execute until the user confirms it. This
keeps semantic flexibility without exact-sentence routing or a large 32-tool
prompt on every turn.
Every point and route risk analysis uses the same latest calculated raster.
Core Analyst source is under
`src/core_analyst/`; its local geodatabase inputs and generated rasters are
under `analysis/core-analyst/` and are intentionally ignored by Git.

Extended analysis results are persisted under
`analysis/core-analyst/outputs/agent/runs/<run_id>/result.json`. The Agent sees a
compact typed summary and uses `run_id` to connect dependent analyses; it cannot
choose arbitrary server filesystem paths.

The one-click all-hazards workflow runs each of the three hazard models once.
Each model invocation produces both current and future outputs, avoiding a
second identical model run for the other time scenario. Its Agent summary
reports Low/Medium/High pixel counts, classified area, and area percentages for
each hazard and the combined maps. The browser receives five focused layers:
the three future hazard classes plus combined current and combined future.
Detailed outputs for all six hazard/scenario pairs remain in the persisted run
record. A mixed citywide raster is not collapsed to a single class by the
deterministic analysis code; the report must explain the distribution.

The entire application preserves Core Analyst's native class convention:
`1 = Low`, `2 = Medium`, `3 = High`, and `0 = NoData`.

## Data Zone flood-risk and social-equity priority workflow

Prepare only the social-risk inputs without repeating the 9.5 GB terrain build:

```bash
oasis data prepare-risk
```

This downloads checksum-locked official releases for 2011 Data Zone boundaries,
the NRS Scottish Postcode Directory, NHS hospitals, Scottish schools, Care
Inspectorate care homes, and Scottish Fire and Rescue Service stations. It
normalizes postcodes and uses the NRS grid reference; it never falls back to an
online geocoder. Unmatched records remain in
`Input/processed/facilities/facility_data_quality.json`.

Run the full default experiment from an existing or newly calculated all-hazard
run:

```bash
oasis priority-assessment \
  --scenario future \
  --forecast-horizon-hours 24 \
  --hazard-threshold 2 \
  --priority-scenario social_equity \
  --no-publish
```

The deterministic workflow uses these working definitions:

- **Hazard** is the Data Zone distribution of the combined maximum pluvial,
  fluvial, and coastal class raster. It is not full risk.
- **Exposure** is the equal-weight normalized combination of estimated exposed
  population, exposed building footprints, and exposed official facilities.
  Population is estimated as Data Zone population multiplied by hazardous-area
  fraction, so it assumes uniform population within the zone.
- **Vulnerability** combines elderly population, no-car households, area-weighted
  SIMD 2020 deprivation, and separate distance-to-hospital and
  distance-to-fire-station accessibility indicators. SIMD is transferred from
  2011 to 2022 Data Zones by polygon-overlap area and is explicitly marked as an
  approximation.
- **Priority** is a relative weighted decision score. The default
  `social_equity` weights are hazard `0.25`, exposure `0.25`, vulnerability
  `0.50`; it is not a probability or official warning.

Each run writes `hazard_by_data_zone.geojson`,
`exposure_by_data_zone.geojson`, `vulnerability_by_data_zone.geojson`,
`priority_by_data_zone.geojson`, a complete CSV, scenario and sensitivity JSON,
a four-panel map, and a sensitivity figure. The web map initially shows only
the selected priority layer; the other three remain available as toggles.

For the paper, report the vulnerability-weight sweep (`0.10`–`0.70`), exposure
threshold comparison (classes `>=2` versus class `3`), and vulnerability with
and without harmonized SIMD. Generated values remain research proxies requiring
human review and scientific validation.

## Human-confirmed decision Agent

Ask a broad question such as:

```text
What is the flood risk and social-equity priority across Glasgow for the next 24 hours?
```

The first response is a plan, not a calculation. The WebGIS shows scenario,
horizon, hazard threshold, decision preset, explicit Hazard/Exposure/Vulnerability
weights, and a SIMD switch. `Confirm and run` calls
`POST /assessments/{plan_id}/execute`; `GET /assessment-jobs/{job_id}` reports
Data readiness, Hazard, Exposure, Vulnerability, Priority, Validation and Publish
progress. Each run writes `execution_trace.json`, `quality_report.json`, and
`recovery_log.json` alongside its deterministic outputs.

Changing only weights or SIMD after completion calls
`POST /analysis/runs/{run_id}/rerank`. This reads saved Data Zone components and
records `external_api_calls: 0`; it does not contact Met Office or recompute a
hazard raster. Quality gates check Data Zone counts, non-empty outputs, score and
rank consistency, GeoJSON coordinates, raster alignment, source times, artifact
checksums, published-layer presence, and anomalously dominant hazard classes. A
failed gate prevents the interface from describing the result as a normal,
validated conclusion.

The current release fully configures only Glasgow. AOI and provider interfaces
are configurable and covered by synthetic tests, but a second city is not claimed
to be operational.

## 6–7 October 2023 historical validation

The validation issue time defaults to `2023-10-06T06:00:00Z` with a 24-hour
horizon. It uses Met Office UKV hourly forecast data from the authenticated
[CEDA NWP-UKV archive](https://catalogue.ceda.ac.uk/uuid/f47bc62786394626b665e23b658d385f/),
local NRFA daily rainfall/river-flow histories, and the official
[Met Office event report](https://www.metoffice.gov.uk/binaries/content/assets/metofficegovuk/pdf/weather/learn-about/uk-past-events/interesting/2023/2023_07_scotland_rain.pdf).
Forecast reference time is checked before any analysis; later forecasts are
rejected rather than substituted.

Configure one archive-access mode:

```dotenv
CEDA_ACCESS_TOKEN=your-short-lived-token
# or CEDA_USERNAME and CEDA_PASSWORD
# or OASIS_HISTORICAL_UKV_PATH=/absolute/path/to/auditable-precipitation.grib
```

Then reproduce without publishing intermediate rasters:

```bash
oasis historical-validation \
  --issue-time 2023-10-06T06:00:00Z \
  --forecast-horizon-hours 24 \
  --hazard-threshold 2 \
  --priority-scenario social_equity \
  --no-publish
```

The downloaded UKV GRIB is cached after a successful response. The processor
selects precipitation bands within T+24 using GRIB metadata, integrates rate or
interval fields, and refuses files whose rainfall semantics cannot be audited.
Outputs include forecast/observation rainfall comparison, forecast and
observation-driven Data Zone priority maps, rainfall bias/MAE/spatial correlation,
Top-10 overlap, rank correlation, NRFA river-flow context, and baseline-versus-event
hazard class distributions. This is explicitly **forecast-input and
decision-stability validation**. NRFA catchment daily rainfall is an observation
proxy and there is no independent Glasgow inundation footprint, so OASIS does not
report flood-pixel IoU, F1, or accuracy.

Run the 24-prompt English/Chinese semantic routing evaluation against the
configured model with:

```bash
python scripts/evaluate_intent_routing.py
```

The short competition demonstration and evidence checklist are in
[`docs/demo-script.md`](docs/demo-script.md) and
[`docs/competition-evidence.md`](docs/competition-evidence.md).

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
- Scottish Government 2011 Data Zone boundaries for SIMD harmonization
- NRS postcode coordinates plus official hospital, school, care-home, and fire
  station releases
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

### 3. Agent model configuration: two modes

Offline tests can use `OASIS_MODEL=test` without a model server or API key. A
real Agent run must use exactly one of the following modes.

#### Mode A: hosted model API

Use this mode when a provider hosts the model. OASIS sends prompts and tool
schemas to that provider, so internet access and a valid provider key are
required. For OpenAI, add the following to the repository-root `.env`:

```dotenv
OASIS_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your-provider-key
```

MiMo is also supported through its OpenAI-compatible endpoint:

```dotenv
OASIS_MODEL_PROVIDER=mimo
OASIS_MODEL=mimo-v2.5-pro
MIMO_API_KEY=your-mimo-api-key
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
```

Use the exact MiMo base URL associated with the purchased key. With hosted
APIs, model inference leaves the local machine; deterministic OASIS analysis
and local data remain unchanged.

#### Mode B: locally deployed model with vLLM

Use this mode when the model weights and a sufficiently large NVIDIA GPU are
available locally. Model prompts and inference remain on the machine, although
OASIS tools may still contact configured services such as SEPA, Nominatim, or
OSRM.

Start vLLM on port `8001`; port `8000` is reserved for the OASIS API. This
tested example serves Qwen3.8-27B:

```bash
VLLM_USE_FLASHINFER_SAMPLER=0 vllm serve /path/to/Qwen3.8-27B \
  --host 127.0.0.1 --port 8001 \
  --served-model-name qwen3.8-27b \
  --max-model-len 65536 --max-num-seqs 8 \
  --gpu-memory-utilization 0.80 --enable-prefix-caching \
  --language-model-only --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder
```

`VLLM_USE_FLASHINFER_SAMPLER=0` selects the PyTorch sampler and avoids a
FlashInfer architecture-detection failure seen on the tested Blackwell setup.
It can be omitted when FlashInfer works correctly. The reasoning and tool-call
parsers are required for Qwen3.8 tool use; use the parsers recommended for a
different local model.

Configure the repository-root `.env` to point OASIS at the local server:

```dotenv
OASIS_MODEL_PROVIDER=vllm
OASIS_MODEL=qwen3.8-27b
OPENAI_BASE_URL=http://127.0.0.1:8001/v1
OPENAI_API_KEY=EMPTY
```

The vLLM provider merges OASIS's leading system instructions for compatibility
with chat templates that accept only one initial system message.

Verify either mode with:

```bash
oasis doctor
oasis agent "Assess the available flood evidence for Glasgow"
```

For local mode, also verify the model server directly:

```bash
curl http://127.0.0.1:8001/v1/models
```

If `oasis doctor` reports `model=test`, the real semantic Agent is not enabled.

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
oasis data prepare-risk
oasis priority-assessment --scenario future --forecast-horizon-hours 24 --no-publish
oasis agent "Assess the available flood evidence for Glasgow" --model test
```

For a real Agent run, configure either hosted API mode or local vLLM mode, then
select its model with `--model` or `OASIS_MODEL`.

## Design rule

Provider-specific API code belongs under `integrations/`. Reusable
`FunctionToolset` objects expose domain-level operations and return Pydantic
models. `agent.py` only composes those toolsets; `runtime.py` binds concrete
providers through `Deps`. This follows PydanticAI's Agent + toolset pattern and
lets another provider replace SEPA without rewriting the Agent loop.
