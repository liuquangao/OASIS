# HydroMind (OASIS)

HydroMind is a Glasgow flood-analysis WebGIS. It combines a Leaflet map, a
tool-using PydanticAI agent, live observations, and deterministic raster
workflows from Core Analyst.

The language model interprets requests and selects tools. Scientific
calculations remain deterministic and provider-independent.

## Quick start

Requirements: Python 3.10+, Docker, and about 10 GB of free space for the full
Glasgow dataset.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
cp .env.example .env              # Windows: copy .env.example .env
```

For a hosted OpenAI model, edit `.env`:

```dotenv
OASIS_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your-key
```

Offline tests can use `OASIS_MODEL=test`. Local inference is also supported
through vLLM on port `8001`; configure `OASIS_MODEL_PROVIDER=vllm`,
`OPENAI_BASE_URL`, and a non-empty placeholder `OPENAI_API_KEY`. The tested
Qwen3.8-27B setup requires the `qwen3` reasoning parser and `qwen3_coder`
tool-call parser.

## Build the Glasgow data

Download `gb2019lcm25m.tif` yourself from the
[UKCEH EIDC order page](https://order-eidc.ceh.ac.uk/resources/1GYPT1HZ/order)
and accept its licence. This file must not be redistributed or committed.

```bash
oasis data rebuild --lcm2019 /path/to/gb2019lcm25m.tif --accept-licences
oasis data verify
```

The build downloads and prepares the complete Glasgow EPSG:27700 5 m inputs.
A valid reproduction must finish with `"ok": true`. Generated data, caches,
outputs, `.env`, and local frontend configuration stay outside Git.

## Run the application

Start GeoServer and PostGIS:

```bash
docker compose -f webgis/docker-compose.yml up -d postgis geoserver
```

Start the Agent API:

```bash
python -m uvicorn oasis.api:app --host 127.0.0.1 --port 8000
```

Serve the frontend in another terminal:

```bash
python -m http.server 3000 --directory webgis/frontend
```

Open <http://localhost:3000>. GeoServer runs on port `8080`; port `8001` is
reserved for local vLLM.

For a watermark-free CARTO basemap, copy
`webgis/frontend/config.local.example.js` to
`webgis/frontend/config.local.js` and add your CARTO key.

## Useful commands

```bash
oasis doctor
oasis agent "Assess the available flood evidence for Glasgow"
oasis all-hazards --forecast-horizon-hours 24 --no-publish
oasis data prepare-risk
oasis priority-assessment --scenario future --forecast-horizon-hours 24 --no-publish
```

Optional runtime integrations are configured in `.env`:

- `METOFFICE_SITE_API_KEY` for 24-hour forecast analysis
- `ADMIRALTY_API_KEY` for live tidal predictions
- CEDA credentials or `OASIS_HISTORICAL_UKV_PATH` for the October 2023 study

## Project layout

```text
src/oasis/          Agent, API, toolsets, models, and integrations
src/core_analyst/   Deterministic hazard and exposure workflows
webgis/frontend/    HydroMind Leaflet interface
webgis/             GeoServer, PostGIS, and WebGIS configuration
analysis/           Local inputs and generated outputs (ignored by Git)
tests/              Automated tests
```

## Validation

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -p pytest_asyncio.plugin
```

Rainfall, river levels, hazard maps, exposure estimates, and priority scores
are research evidence for human review. They are not operational flood
warnings. Provenance, timestamps, limitations, and explicit decision weights
must remain visible in any reported result.

See [the demo script](docs/demo-script.md),
[competition evidence](docs/competition-evidence.md), and
[WebGIS notes](webgis/README.md) for more detail.
