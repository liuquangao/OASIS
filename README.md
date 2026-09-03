# HydroMind

HydroMind is a Glasgow flood-analysis WebGIS. It combines a Leaflet map, a
tool-using PydanticAI agent, live observations, and deterministic raster
workflows from Core Analyst.

The language model interprets requests and selects tools. Scientific
calculations remain deterministic and provider-independent.

## Quick start

You need Python 3.12+, about 12 GB of free disk space, and a model API key.
Docker is optional — see [Run it](#run-it).

Windows, macOS and Linux are all supported. CI runs the test suite on
`windows-latest` and `ubuntu-latest` on every push.

**Windows (PowerShell):**

```powershell
git clone https://github.com/liuquangao/OASIS.git C:\hydromind
cd C:\hydromind
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -c constraints.txt
copy .env.example .env
```

**macOS / Linux:**

```bash
git clone https://github.com/liuquangao/OASIS.git
cd OASIS
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]" -c constraints.txt
cp .env.example .env
```

Two things about that install are deliberate:

- `-c constraints.txt` pins the 139 package versions this project was verified
  with. Without it pip resolves versions nobody has tested, and `pydantic-ai` in
  particular changes fast enough to alter agent behaviour.
- Python 3.12 is the floor because the pinned NumPy and SciPy require it.

On Windows, clone somewhere short like `C:\hydromind`. Generated data paths reach
153 characters on their own, so a deep location such as a synced `Documents`
folder can exceed the 260-character path limit.

## Choose a model

The default and simplest route is a hosted API. Edit `.env`:

```dotenv
HYDROMIND_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your-key
```

Any PydanticAI model identifier works. Set `HYDROMIND_MODEL=test` to run the
test suite offline; it performs no real reasoning. A local GPU is **not**
required — see [Local inference with vLLM](#optional-local-inference-with-vllm)
only if you want one.

Check what the application sees:

```bash
hydromind doctor
```

## Build the Glasgow data

Download `gb2019lcm25m.tif` yourself from the
[UKCEH EIDC order page](https://order-eidc.ceh.ac.uk/resources/1GYPT1HZ/order)
and accept its licence. This file must not be redistributed or committed.

Check the licence gate before committing to a 9.1 GB download:

```bash
hydromind data preflight --lcm2019 /path/to/gb2019lcm25m.tif
```

Then build and verify:

```bash
hydromind data rebuild --lcm2019 /path/to/gb2019lcm25m.tif --accept-licences
hydromind data verify
```

The build downloads and prepares the complete Glasgow EPSG:27700 5 m inputs.
A valid reproduction must finish with `"ok": true`. Downloads are cached and
checked against the sizes and checksums in `data/glasgow-5m-sources.json`, so an
interrupted build can be re-run without starting over. Generated data, caches,
outputs, `.env`, and local frontend configuration stay outside Git.

## Run it

There are two routes. The first needs nothing but Python.

### Command line only — no Docker

Pass `--no-publish` to skip GeoServer entirely. `hydromind agent` never
publishes anything, so it takes no such flag.

```bash
hydromind agent "Assess the available flood evidence for Glasgow"
hydromind all-hazards --forecast-horizon-hours 24 --no-publish
hydromind priority-assessment --scenario future --forecast-horizon-hours 24 --no-publish
hydromind rerank --run-id RUN_ID --no-publish
```

This is the full deterministic pipeline — hazard, exposure, vulnerability and
priority — with results written under `analysis/core-analyst/outputs/`. It is
the shortest path to a reproduction, and the only one that needs no containers.

### Full web interface

The browser map additionally serves the 5 m hazard rasters through GeoServer, so
this route needs Docker.

```bash
docker compose -f webgis/docker-compose.yml up -d postgis geoserver
python -m uvicorn hydromind.api:app --host 127.0.0.1 --port 8000
python -m http.server 3000 --directory webgis/frontend   # in another terminal
```

Open <http://localhost:3000> **on the same machine**: the page calls the Agent
API on `127.0.0.1:8000`, which does not listen on the network.

GeoServer runs on port `8080` and PostGIS on `5432`; port `8001` is reserved for
local vLLM. For a watermark-free CARTO basemap, copy
`webgis/frontend/config.local.example.js` to `webgis/frontend/config.local.js`
and add your CARTO key.

What GeoServer actually buys you: Data Zone results (hazard, exposure,
vulnerability and priority) are served as GeoJSON by the Agent API itself and
render without it. Only the 5 m hazard-class rasters are published as WMS
layers, so without GeoServer the map loses the raster overlay while the Data
Zone layers and the risk report still work.

## Optional: local inference with vLLM

Only needed if you want to run the model yourself instead of calling an API.

```dotenv
HYDROMIND_MODEL_PROVIDER=vllm
HYDROMIND_MODEL=qwen3.8-27b
OPENAI_BASE_URL=http://127.0.0.1:8001/v1
OPENAI_API_KEY=placeholder
```

The tested setup is Qwen3.8-27B (52 GB of weights) on an RTX PRO 6000 Blackwell:

```bash
VLLM_USE_FLASHINFER_SAMPLER=0 vllm serve /path/to/Qwen3.8-27B \
  --served-model-name qwen3.8-27b \
  --host 127.0.0.1 --port 8001 \
  --gpu-memory-utilization 0.78 \
  --max-model-len 32768 \
  --max-num-seqs 256 \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --enable-auto-tool-choice
```

Two of those are not defaults and the server will not start without them:

- `VLLM_USE_FLASHINFER_SAMPLER=0` — FlashInfer's JIT does not recognise `sm_120`,
  and reports the misleading `FlashInfer requires GPUs with sm75 or higher`.
- `--max-num-seqs 256` — Qwen3.5's hybrid Mamba layers cap the usable decode
  sequences; the default of 1024 exceeds the available cache blocks.

Give the model a generous token budget. Reasoning mode can spend more than 512
tokens before it emits its first tool call.

Confirm the server is up with `curl http://127.0.0.1:8001/v1/models`, then check
that `hydromind doctor` reports the model as configured.

## Optional integrations

Configured in `.env`:

- `METOFFICE_SITE_API_KEY` for 24-hour forecast analysis
- `ADMIRALTY_API_KEY` for live tidal predictions
- CEDA credentials or `HYDROMIND_HISTORICAL_UKV_PATH` for the October 2023 study

## Troubleshooting

**`does not match the source lock` when building data.** Your checkout is not
byte-exact. Run `python scripts/check_checkout.py`, which names the cause. The
usual one is Git for Windows rewriting LF to CRLF; the repository's
`.gitattributes` prevents this, so a fresh `git clone` is the reliable fix.

**Installation resolves unexpected versions.** Reinstall with
`-c constraints.txt`. Run `python scripts/write_constraints.py --check` to see
whether an environment matches the pinned set.

**Tests fail to collect.** Disable global plugin autoload so unrelated
system-wide pytest plugins cannot interfere:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin
```

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"; pytest -p pytest_asyncio.plugin
```

**The map says the language model is unavailable.** The Agent API returns 502
with the endpoint it tried. Check that key or local server, not the analysis
workflow.

## Project layout

```text
src/hydromind/      Agent, API, toolsets, models, and integrations
src/core_analyst/   Deterministic hazard and exposure workflows
webgis/frontend/    HydroMind Leaflet interface
webgis/             GeoServer, PostGIS, and WebGIS configuration
analysis/           Local inputs and generated outputs (ignored by Git)
scripts/            Environment and checkout checks
tests/              Automated tests
```

## Validation

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin
```

Rainfall, river levels, hazard maps, exposure estimates, and priority scores
are research evidence for human review. They are not operational flood
warnings. Provenance, timestamps, limitations, and explicit decision weights
must remain visible in any reported result.

See [the demo script](docs/demo-script.md) and
[competition evidence](docs/competition-evidence.md) for more detail.
