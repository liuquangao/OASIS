# HydroMind / OASIS

HydroMind is a Glasgow flood-analysis WebGIS. It combines a browser map, a
tool-using language-model Agent, live environmental observations, and
deterministic Core Analyst raster workflows.

The design rule is simple: the language model interprets the user request and
chooses tools; the flood calculations, raster processing, exposure analysis,
and priority ranking remain deterministic and auditable.

## Quick Start (Windows PowerShell)

Follow these steps to install the environment, prepare the data, and configure
API access. The examples use `C:\OASIS`; replace this path if your repository
is located elsewhere.

### 1. Set Up Git And Python, Then Clone The Repository

Install Git and Python 3.12 or newer. Open PowerShell and check both tools:

```powershell
git --version
python --version
```

Clone the branch used by this guide and install the project dependencies:

```powershell
git clone --branch github-ready-demo-and-runtime https://github.com/liuquangao/OASIS.git C:\OASIS
cd C:\OASIS
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c constraints.txt
```

If you have already cloned this branch, enter your existing repository folder
and start with the Python environment setup.

### 2. Download The Data And Extract It Into The Repository Root

Obtain `data.zip` through the channel provided by the project maintainer and
save it locally. This repository does not currently include a public download
link for the archive.

The archive already contains a `data/` folder. Extract it into the
**repository root, `C:\OASIS`**, which contains `README.md`.
Replace the archive path below with its actual location:

```powershell
cd C:\OASIS
Expand-Archive -LiteralPath "C:\path\to\data.zip" -DestinationPath .
```

Alternatively, select **Extract All** in File Explorer and set the destination
to `C:\OASIS`. If files already exist, compare them before replacing them;
the repository includes a few data metadata files.

The extracted directory structure should be:

```text
C:\OASIS\
├─ README.md
├─ .env.example
└─ data\
   ├─ Input\
   │  ├─ OASIS_Rasters\
   │  ├─ OASIS_Polygon\
   │  ├─ DataZone\
   │  ├─ OASIS_CSV\
   │  └─ processed\
   └─ gb2019lcm25m.tif
```

The application reads `data/Input` by default. Avoid an extra folder level such
as `data/data/Input`, and extract the ZIP instead of placing the archive itself
in the input directory. Prepared input data does not need to be rebuilt.

### 3. Choose A Model: Hosted API Or Local vLLM

Choose one of the two options below: use your own hosted model API credentials,
or run a model locally with vLLM. The Agent requires a model that supports
tool calling.

Create a local configuration file in the repository root. If `.env` already
exists, edit it while preserving your existing settings:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Replace the existing `HYDROMIND_MODEL=test` entry and keep only one active
model configuration. Both options use `HYDROMIND_CORE_ANALYST_INPUT_DIR=data/Input`.

#### Option A: Use Your Own Hosted Model API

Configure your provider's model identifier and your own API key. For example,
to use OpenAI:

```dotenv
HYDROMIND_MODEL_PROVIDER=auto
HYDROMIND_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=your_openai_api_key_here
HYDROMIND_CORE_ANALYST_INPUT_DIR=data/Input
```

The model name is an example; select a tool-capable model available to your
account. Other providers require their PydanticAI model identifier and
provider-specific credentials.

For MiMo, use this configuration instead. Set `MIMO_BASE_URL` to the endpoint
shown for your API key in the provider console; the URL below is a Token Plan
example:

```dotenv
HYDROMIND_MODEL_PROVIDER=mimo
HYDROMIND_MODEL=mimo-v2.5-pro
MIMO_API_KEY=your_mimo_api_key_here
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
HYDROMIND_CORE_ANALYST_INPUT_DIR=data/Input
```

#### Option B: Run A Model Locally With vLLM

Start your vLLM server separately, then connect HydroMind to its
OpenAI-compatible endpoint. Editing `.env` does not install or start vLLM.
Use port `8001` for vLLM; port `8000` is reserved for the HydroMind API.

```dotenv
HYDROMIND_MODEL_PROVIDER=vllm
HYDROMIND_MODEL=qwen3.8-27b
OPENAI_BASE_URL=http://127.0.0.1:8001/v1
OPENAI_API_KEY=placeholder
HYDROMIND_CORE_ANALYST_INPUT_DIR=data/Input
```

Set `HYDROMIND_MODEL` to the exact model name served by your vLLM instance.
For a server without authentication, the client still requires a non-empty
placeholder key; otherwise, use the server's configured API key.
The repository's tested Qwen profile uses the `qwen3` reasoning parser and
the `qwen3_coder` tool-call parser. Enable tool calling when serving the model.

Check that the server is reachable before running the Agent:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/v1/models -Headers @{ Authorization = "Bearer placeholder" }
```

Replace `placeholder` in this check if your server requires authentication.

### 4. Configure Data APIs For The Features You Need

Model credentials do not provide access to weather or tidal data. Add the
settings required by your planned analysis to the same `.env` file:

| Feature | Required setting | When to configure it |
| --- | --- | --- |
| Analysis using the prepared local inputs | No additional data API key | The extracted `data/Input` directory supplies these inputs. |
| Live future rainfall forecasts | `METOFFICE_SITE_API_KEY` | Before running analyses that request Met Office rainfall forecasts. |
| Live coastal tidal predictions | `ADMIRALTY_API_KEY` | Before requesting ADMIRALTY tidal predictions. |
| Historical UKV analysis using local GRIB data | `HYDROMIND_HISTORICAL_UKV_PATH` | Point to your local rainfall GRIB file or directory. |
| Historical UKV downloads from CEDA | `CEDA_ACCESS_TOKEN` or `CEDA_USERNAME` and `CEDA_PASSWORD` | Only when the historical archive must be fetched online. |
| SEPA rainfall and water-level observations | No API key | Uses public endpoints; internet access is required. |
| Nominatim geocoding and OSRM routing | No API key for the default endpoints | Uses public services; internet access is required. |

Uncomment and fill only the entries needed for your workflow. Leave unused
credentials commented out instead of setting placeholder keys:

```dotenv
# Live future rainfall forecasts
# METOFFICE_SITE_API_KEY=your_metoffice_site_specific_key

# Live coastal tidal predictions
# ADMIRALTY_API_KEY=your_admiralty_tidal_api_key

# Historical analysis: prefer a local rainfall GRIB file or directory
# HYDROMIND_HISTORICAL_UKV_PATH=C:/path/to/ukv_202310

# Online historical data access, only if needed
# CEDA_ACCESS_TOKEN=your_ceda_access_token
# CEDA_USERNAME=your_ceda_username
# CEDA_PASSWORD=your_ceda_password
```

You can skip these credentials when using only prepared local data and public
observation services. Forecast features that need them require separate setup.

For a CARTO basemap key, create `webgis/frontend/config.local.js` from
`config.local.example.js` in the same folder and set your key:

```javascript
window.HYDROMIND_CONFIG = {
  cartoBasemapKey: "your_carto_basemap_key"
};
```

This local frontend configuration is ignored by Git.

### 5. Verify The Configuration And Data, Then Run

Save `.env` and close Notepad. Keep `.env`, API keys, extracted inputs, and
licensed source data local; do not commit them to GitHub.

From the repository root, check the configuration and verify the data:

```powershell
.\.venv\Scripts\hydromind.exe doctor
.\.venv\Scripts\hydromind.exe data verify
```

Review the configuration checks and confirm that data verification reports
`"ok": true`. These checks do not establish that every external API credential
works; the relevant service is exercised when its feature is used.
Then run an Agent analysis:

```powershell
.\.venv\Scripts\hydromind.exe agent "Assess the available flood evidence for Glasgow"
```

This command runs one analysis in the terminal. Continue below to launch the
web interface and its supporting services.

### 6. Start The Full WebGIS

Complete the configuration and data checks above first. The full setup runs
PostGIS and GeoServer in Docker, the HydroMind API on port `8000`, and the
frontend on port `3000`. If you selected local vLLM, keep its model server
running on port `8001` as well.

#### Start PostGIS And GeoServer

Install and start Docker Desktop with Linux containers enabled. In PowerShell,
enter your repository root and start the containers:

```powershell
cd C:\OASIS
docker compose -f webgis/docker-compose.yml up -d postgis geoserver
docker compose -f webgis/docker-compose.yml ps
```

The first run downloads the container images and may take several minutes.
Wait for GeoServer to respond at <http://127.0.0.1:8080/geoserver/>. Container
startup alone does not confirm that GeoServer is ready. To inspect startup:

```powershell
docker compose -f webgis/docker-compose.yml logs --tail 50 geoserver
```

These containers provide storage and WMS raster overlays and are required for the full deployment described here.

#### Start The Backend In Terminal 1

```powershell
cd C:\OASIS
.\.venv\Scripts\python.exe -m uvicorn hydromind.api:app --host 127.0.0.1 --port 8000
```

Keep this terminal open. Wait for the application startup message before
continuing.

#### Start The Frontend In Terminal 2

Open a second PowerShell terminal:

```powershell
cd C:\OASIS
.\.venv\Scripts\python.exe -m http.server 3000 --bind 127.0.0.1 --directory webgis/frontend
```

Keep this terminal open, then visit <http://127.0.0.1:3000> in your browser.
Use the main page for the live application; `demo-static.html` uses mock data.

#### Check The Services

In another PowerShell terminal, check the backend:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/setup/status
```

Review the reported configuration and data readiness, then submit an analysis
request in the web interface to exercise the model and the selected data APIs.
WMS analysis overlays require generated results to be published to GeoServer;
starting the services does not generate those results automatically.

To stop the frontend and backend, press `Ctrl+C` in their respective terminals.
To stop this project's containers while retaining their data:

```powershell
cd C:\OASIS
docker compose -f webgis/docker-compose.yml stop
```

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `docker` is unavailable or cannot connect | Install and start Docker Desktop, then reopen PowerShell. |
| A service port is already in use | Use `Get-NetTCPConnection -State Listen` to identify the process before changing ports or stopping it. |
| The Agent cannot connect to its model | Check the provider, model, key, and Base URL in `.env`. For vLLM, confirm the model server is running on port `8001`. |
| Data verification fails | Confirm the archive was extracted to `data/Input`, then review the file errors reported by `data verify`. |
| Raster overlays are missing | Check GeoServer startup logs and confirm analysis results have been published. |
| The browser shows an old error | Restart the backend after configuration changes and refresh the browser with `Ctrl+F5`. |
| GeoServer upload returns `502` | Check that local service requests are not routed through a system proxy. |

## Further Documentation

- [Data preparation and historical analysis](docs/data-workflows.md): input layout, rebuilding the Glasgow 5 m dataset, and historical UKV runs.
- [Development reference](docs/development.md): tests, project layout, and files that must remain local.

HydroMind outputs are research and planning evidence for human review, not
operational flood warnings or official forecasts. Keep `.env`, API keys,
licensed inputs, and generated outputs out of Git.
