# HydroMind / OASIS

HydroMind is a Glasgow flood-analysis WebGIS. It combines a browser map, a
tool-using language-model Agent, live environmental observations, and
deterministic Core Analyst raster workflows.

The design rule is simple: the language model interprets the user request and
chooses tools; the flood calculations, raster processing, exposure analysis,
and priority ranking remain deterministic and auditable.

## What Can Run Without Deployment

The repository supports three practical modes.

| Mode | Needs Python API | Needs data/Input | Needs Docker/GeoServer | Needs model key | Purpose |
| --- | --- | --- | --- | --- | --- |
| Static demo | No | No | No | No | UI preview for GitHub Pages or embedding |
| Local analysis | Yes | Yes | No | Yes | Agent, reports, GeoJSON outputs, no raster WMS overlay |
| Full WebGIS | Yes | Yes | Optional | Yes | Full UI plus GeoServer WMS raster overlays |

The lowest-friction public demo is:

```text
webgis/frontend/demo-static.html
```

It is a standalone static page with an online CARTO basemap and mock flood
overlays. It does not call the HydroMind API, GeoServer, Docker, or any model.

## Current Data Logic

The default analysis input directory is:

```text
data/Input
```

The code accepts both the original `HYDROMIND_*` layout and the current
`OASIS_*` layout. For example:

```text
data/Input/OASIS_Rasters/OASIS_Rasters
data/Input/OASIS_Polygon/OASIS_Polygon
data/Input/DataZone/csv2022
data/Input/DataZone/shapefile2011
data/Input/DataZone/Geojson2022
data/Input/OASIS_CSV/CSV
data/Input/processed
```

`hydromind data verify` checks the 16 required Glasgow 5 m rasters. A valid
local analysis setup reports:

```json
{"ok": true}
```

`gb2019lcm25m.tif` is only the licensed UKCEH source material used when
rebuilding the full input directory. If `data/Input` is already prepared and
verified, the normal runtime does not need to read `gb2019lcm25m.tif`.

## API And Sensitive Configuration

Never commit `.env`, API keys, downloaded licensed data, raw GRIB files, or
generated analysis outputs.

Required for a real Agent run:

```dotenv
HYDROMIND_MODEL_PROVIDER=mimo
HYDROMIND_MODEL=mimo-v2.5-pro
MIMO_API_KEY=your_mimo_key
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
```

Alternative hosted OpenAI-compatible mode:

```dotenv
HYDROMIND_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your_openai_key
```

Optional local vLLM mode:

```dotenv
HYDROMIND_MODEL_PROVIDER=vllm
HYDROMIND_MODEL=qwen3.8-27b
OPENAI_BASE_URL=http://127.0.0.1:8001/v1
OPENAI_API_KEY=placeholder
```

Analysis and WebGIS paths:

```dotenv
HYDROMIND_CORE_ANALYST_INPUT_DIR=data/Input
HYDROMIND_CORE_ANALYST_ANALYSIS_OUTPUT_DIR=analysis/core-analyst/outputs/agent
HYDROMIND_CORE_ANALYST_CONFIG_DIR=analysis/core-analyst/config
HYDROMIND_CORE_ANALYST_CONFIG_PATH=analysis/core-analyst/config/pluvial_prediction_config.yaml
```

Optional live or historical integrations:

```dotenv
METOFFICE_SITE_API_KEY=your_metoffice_key
ADMIRALTY_API_KEY=your_admiralty_key
HYDROMIND_HISTORICAL_UKV_PATH=/absolute/path/to/ukv_202310
CEDA_ACCESS_TOKEN=your_ceda_token
CEDA_USERNAME=your_ceda_username
CEDA_PASSWORD=your_ceda_password
```

How these are used:

- `METOFFICE_SITE_API_KEY`: live future pluvial rainfall forecast.
- `ADMIRALTY_API_KEY`: optional live tide prediction for coastal evidence.
- `HYDROMIND_HISTORICAL_UKV_PATH`: preferred local UKV GRIB file or directory
  for the October 2023 hindcast.
- `CEDA_*`: fallback only if the UKV archive must be fetched at runtime.
- SEPA rainfall and water-level calls use public endpoints and do not require a
  key.
- Nominatim and OSRM are public default services for geocoding and routes.
- CARTO basemap keys are stored in `webgis/frontend/config.local.js`, which is
  ignored by Git.

## Install

Use Python 3.12 or newer. On Windows, a short path such as `C:\hydromind` is
recommended because generated geospatial paths can be long.

Windows PowerShell:

```powershell
git clone https://github.com/liuquangao/OASIS.git C:\hydromind
cd C:\hydromind
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -c constraints.txt
copy .env.example .env
```

macOS or Linux:

```bash
git clone https://github.com/liuquangao/OASIS.git
cd OASIS
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]" -c constraints.txt
cp .env.example .env
```

The `-c constraints.txt` flag is intentional. It pins the package versions that
were tested for this project.

## Run The Static Demo

This is the easiest GitHub-friendly demonstration. It needs only a browser and
internet access for the online basemap.

Open directly:

```text
webgis/frontend/demo-static.html
```

Or serve the folder:

```powershell
python -m http.server 3000 --directory webgis/frontend
```

Then open:

```text
http://127.0.0.1:3000/demo-static.html
```

To embed it inside another HTML file:

```html
<iframe src="webgis/frontend/demo-static.html" style="width:100%;height:100vh;border:0"></iframe>
```

## Run Local Analysis Without Docker

This mode uses the real Python backend and deterministic analysis workflows, but
skips GeoServer raster publication.

Edit `.env` and set at least:

```dotenv
HYDROMIND_MODEL_PROVIDER=mimo
HYDROMIND_MODEL=mimo-v2.5-pro
MIMO_API_KEY=your_mimo_key
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
HYDROMIND_CORE_ANALYST_INPUT_DIR=data/Input
```

Verify data:

```powershell
hydromind data verify
```

Run command-line analyses:

```powershell
hydromind agent "Assess the available flood evidence for Glasgow"
hydromind all-hazards --forecast-horizon-hours 24 --no-publish
hydromind priority-assessment --scenario future --forecast-horizon-hours 24 --no-publish
```

Outputs are written under:

```text
analysis/core-analyst/outputs
```

## Run The Full WebGIS

Start GeoServer and PostGIS only if you want WMS raster overlays on the map:

```powershell
docker compose -f webgis/docker-compose.yml up -d postgis geoserver
```

Start the HydroMind API:

```powershell
.venv\Scripts\python.exe -m uvicorn hydromind.api:app --host 127.0.0.1 --port 8000
```

In another terminal, start the frontend:

```powershell
python -m http.server 3000 --directory webgis/frontend
```

Open:

```text
http://127.0.0.1:3000
```

Default ports:

| Service | Port |
| --- | ---: |
| HydroMind API | 8000 |
| Frontend static server | 3000 |
| GeoServer | 8080 |
| PostGIS | 5432 |
| Optional local vLLM | 8001 |

If port 8000 is already used:

```powershell
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

## Rebuild Data From Licensed Sources

If `data/Input` is missing, rebuild it from source. The UKCEH LCM 2019 file is
licence-gated and must be downloaded by the user from the official UKCEH EIDC
order page. Do not commit or redistribute it.

```powershell
hydromind data preflight --lcm2019 D:\path\to\gb2019lcm25m.tif --accept-licences
hydromind data rebuild --lcm2019 D:\path\to\gb2019lcm25m.tif --accept-licences
hydromind data verify
```

The rebuild process downloads public supporting inputs, prepares the Glasgow 5 m
workflow, and writes generated files into the ignored input directory.

## Historical UKV Hindcast

For the October 2023 historical validation, prefer a local UKV archive:

```dotenv
HYDROMIND_HISTORICAL_UKV_PATH=D:\path\to\ukv_202310
```

If the path is a directory, HydroMind selects the GRIB matching the issue time,
for example:

```text
202310060600_*.grib
```

The GRIB must contain precipitation or rainfall bands. A file containing only
wind gust (`GUST`) bands is not a valid rainfall forecast input.

Run:

```powershell
hydromind historical-validation --issue-time 2023-10-06T06:00:00Z --forecast-horizon-hours 24 --no-publish
```

## What Should Not Be Uploaded To GitHub

The following are local-only and should stay ignored:

```text
.env
.venv/
data/Input/
data/gb2019lcm25m.tif
data/ukv_202310/
data/**/*.grib
data/**/*.part
analysis/core-analyst/outputs/
analysis/core-analyst/.hydromind-data-cache/
webgis/frontend/config.local.js
webgis/.runtime/
```

Small source-lock and geometry metadata files under `data/` may remain tracked
if they are part of reproducible setup, for example:

```text
data/glasgow-5m-sources.json
data/glasgow-city-1km-buffer.geojson
data/glasgow-dtm-edge-patch.csv
```

## Troubleshooting

Check configuration:

```powershell
hydromind doctor
```

Check the API:

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/setup/status
```

Check Docker services:

```powershell
docker compose -f webgis/docker-compose.yml ps
docker logs glasgow-geoserver --tail 100
```

If the browser still shows an old error after code changes, restart the API
process and hard-refresh the browser with `Ctrl+F5`.

If GeoServer upload returns `502`, check that local HTTP requests are not being
sent through a system proxy. The GeoServer publisher disables environment proxy
settings for local REST calls.

## Tests

Run focused tests while developing, then the suite:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"
.venv\Scripts\pytest.exe -p pytest_asyncio.plugin
```

On macOS or Linux:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin
```

## Project Layout

```text
src/hydromind/      Agent, API, settings, toolsets, models, integrations
src/core_analyst/   Deterministic hazard, exposure, vulnerability workflows
webgis/frontend/    Leaflet UI and static demo
webgis/             Optional GeoServer and PostGIS configuration
data/               Small source metadata plus ignored local input data
analysis/           Generated outputs and caches
tests/              Automated tests
```

## Scientific And Safety Notes

HydroMind outputs are research and planning evidence for human review. They are
not operational flood warnings, emergency instructions, or official forecasts.
Preserve provenance, timestamps, uncertainty, and the selected decision weights
when reporting results.
