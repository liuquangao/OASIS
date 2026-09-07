# Development Reference

[Back to the quick start](../README.md#quick-start-windows-powershell).

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
webgis/             Docker configuration for GeoServer and PostGIS
data/               Small source metadata plus ignored local input data
analysis/           Generated outputs and caches
tests/              Automated tests
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
