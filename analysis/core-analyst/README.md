# Core Analyst: Pluvial Flood Hazard MVP

This repository contains Component 2 of a modular Glasgow Flood Risk Assessment system. It is a minimum viable Core Analyst, not a final scientific flood model.

## Architecture

The code separates three concerns:

- Data layer: `DataSource`, `StaticRasterSource`, `RealTimeAPISource`, and `MockRainfallAPISource`
- Analysis layer: validation, factor normalization, weighted overlay, hazard classification, and `PluvialFloodAnalyst`
- Output layer: WebGIS-ready GeoTIFF outputs plus `analysis_metadata.json`

The analysis code is data-source agnostic. Static rainfall and real-time rainfall both enter the workflow as an analysis-ready `RasterGrid`.

## Input Data Contract

Component 1 is expected to provide standardized rasters:

```text
data/static/dem.tif
data/static/slope.tif
data/static/flow_accumulation.tif
data/static/imperviousness.tif
data/static/rainfall.tif
```

All inputs should be GeoTIFFs with matching CRS, extent, resolution, shape, transform, and NoData handling. The Core Analyst validates these properties but does not preprocess source datasets.

## Demo Data

`python demo.py` creates synthetic rasters under `data/demo/` when real Glasgow data are unavailable. These rasters have realistic-looking spatial patterns for a workflow demo only.

## Pluvial Workflow

The demo computes:

```text
Hazard Index =
0.20 * Elevation Risk
+ 0.15 * Slope Risk
+ 0.25 * Flow Accumulation Risk
+ 0.20 * Imperviousness Risk
+ 0.20 * Rainfall Risk
```

Factor logic:

- Low elevation maps to higher hazard.
- Low slope maps to higher surface-water accumulation risk.
- High flow accumulation maps to higher hazard.
- High imperviousness maps to higher runoff potential.
- High rainfall maps to higher hazard.

Weights and class thresholds are in `config/pluvial_config.yaml`. They are demonstration values and are not scientifically validated.

## Outputs

The demo writes:

```text
outputs/hazard_index.tif
outputs/hazard_class.tif
outputs/analysis_metadata.json
outputs/normalized_factors/*.tif
outputs/debug_pluvial_maps.png
```

`hazard_class.tif` uses integer classes:

- `1`: low
- `2`: medium
- `3`: high

Component 3 can consume the GeoTIFF rasters and metadata directly in a WebGIS pipeline.

## Static and Real-Time Data

Static or slow-changing data should be prepared by Component 1 and stored as standardized raster layers. Dynamic environmental data should be retrieved through a `RealTimeAPISource`, converted to an aligned raster/grid, and then passed to the same analysis pipeline.

The MVP includes `MockRainfallAPISource`, which simulates current rainfall observations and interpolates them onto the analysis grid. It is deliberately separate from the pluvial analysis code. `SEPARainfallAPISource` is a placeholder for a future verified SEPA Time Series API connector.

## Developer Data Discovery

Run:

```bash
python demo.py --discover-data
```

This prints a developer/debug-only report using `data_catalogue.json`. It does not download data and does not claim success unless a local file exists. Manual download or Component 1 preprocessing is reported clearly where required.

This reflects the current project reality: some source discovery and preprocessing is human-led, while the demo only obtains or simulates enough data to run Component 2 end to end.

## Running

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the demo:

```bash
python demo.py
```

Run a rainfall scenario:

```bash
python demo.py --query "What would the flood hazard look like if rainfall increases by 20%?"
```

Run with the current `OASIS_Rasters` real raster folder:

```bash
python demo.py --use-real-data --real-data-dir OASIS_Rasters --output-dir outputs/oasis_real
```

This uses `DTM_5m_res.tif` as the reference grid, aligns the built-up and greenspace rasters to that grid at read time, derives a temporary slope layer from the DTM, and derives a temporary topographic flow proxy because a formal Component 1 `flow_accumulation.tif` is not present yet. The output extent is the DTM extent:

```text
CRS: EPSG:27700
X: 249424.0 to 271434.0
Y: 655461.5 to 674071.5
Resolution: 5 m
Shape: 3722 x 4402
Approximate size: 22.01 km x 18.61 km
```

The real-data mode is useful for workflow integration, but the slope and flow layers should be replaced by Component 1 products before scientific interpretation.

Current real inputs in `OASIS_Rasters` are not perfectly identical in bounds and shape. They share British National Grid and 5m resolution, so the real-data workflow aligns secondary rasters to the DTM reference grid while reading. For production, Component 1 should still export a clean canonical stack under `data/static/` with identical grid metadata.

Run with updated `Input` data and live SEPA rainfall station observations:

```bash
python demo.py --use-input-data --input-dir Input --config config/oasis_realtime_config.yaml --rainfall-source sepa --sepa-stations 15198 --output-dir outputs/input_sepa
```

For one SEPA station, the rainfall field is spatially uniform across the valid DTM mask. For multiple stations, pass a comma-separated list and the prototype uses inverse-distance weighting:

```bash
python demo.py --use-input-data --input-dir Input --config config/oasis_realtime_config.yaml --rainfall-source sepa --sepa-stations 15198,XXXXX,YYYYY --output-dir outputs/input_sepa
```

SEPA Rainfall data is treated as three separate data roles:

```text
SEPA rainfall station metadata
  Classification: relative static / slow-changing
  Endpoint: https://www2.sepa.org.uk/Rainfall/api/Stations
  Use: station inventory, station number/name/coordinates, and Glasgow study-area filtering.

SEPA latest rainfall observation
  Classification: dynamic / real-time observation
  Endpoint: https://www2.sepa.org.uk/Rainfall/api/Stations/{station_no}
  Use: current rainfall forcing at t0, converted to mm/hour and interpolated to the DTM grid.

SEPA hourly rainfall history
  Classification: dynamic historical time series / recent observational history
  Endpoint: https://www2.sepa.org.uk/Rainfall/api/Hourly/{station_no}?all=true
  Use: recent rainfall history and antecedent wetness state for temporal prediction.
```

Hourly history retrieval is optional in the current workflow:

```bash
python demo.py --use-input-data --input-dir Input --config config/oasis_realtime_config.yaml --rainfall-source sepa --sepa-stations 15198 --sepa-include-hourly-history --sepa-history-hours 6 --output-dir outputs/input_sepa_history
```

Met Office Weather DataHub should be added as a separate authenticated connector because it requires registration/subscription and API access configuration. Do not hard-code credentials in the Core Analyst.

Check Met Office Weather DataHub Atmospheric Models access without storing credentials:

```powershell
$env:METOFFICE_API_KEY="your_rotated_key_here"
python metoffice_check.py --orders
```

Then inspect one order:

```powershell
python metoffice_check.py --order YOUR_ORDER_NAME --run latest
```

The current code verifies access and retrieves order manifests. Converting Met Office GRIB2 precipitation products into the Core Analyst rainfall grid is the next integration step after choosing the correct order/product/parameter.

Check Met Office SiteSpecificForecast access:

```powershell
$env:METOFFICE_SITE_API_KEY="your_rotated_site_specific_key_here"
python metoffice_site_check.py --lat 55.8642 --lon -4.2518 --timesteps hourly
```

This returns a point forecast for Glasgow city centre and attempts to extract precipitation-like values. Site-specific data can support forecast rainfall scenarios, but it is not a full rainfall raster unless sampled at multiple points and interpolated.

Run the Core Analyst with Met Office SiteSpecificForecast rainfall from `env.env`:

```powershell
python demo.py --use-input-data --input-dir Input --config config/oasis_realtime_config.yaml --rainfall-source metoffice-site --metoffice-horizon-hours 6 --output-dir outputs/input_metoffice_site
```

The workflow samples nine points across the DTM study area, retrieves hourly site-specific forecasts, extracts `totalPrecipAmount`, takes the maximum hourly amount within the selected horizon, interpolates the point values to the DTM grid, and converts rainfall to risk using `rainfall_thresholds` in `config/oasis_realtime_config.yaml`.

Run all current/future MVP hazard workflows:

```powershell
python run_all_hazards.py --input-dir Input --output-dir outputs/all_hazards --metoffice-horizon-hours 6
```

This writes:

```text
outputs/all_hazards/pluvial/current/
outputs/all_hazards/pluvial/future/
outputs/all_hazards/fluvial/current/
outputs/all_hazards/fluvial/future/
outputs/all_hazards/coastal/current/
outputs/all_hazards/coastal/future/
outputs/all_hazards/all_hazards_summary.json
outputs/all_hazards/all_hazards_classes.png
```

The pluvial workflow now follows a state-aware prediction structure:

```text
STATIC / BASELINE
  DEM, slope, flow accumulation/proxy, imperviousness, land cover, SEPA flood maps

DYNAMIC / TEMPORAL
  Observations: SEPA rainfall observations, recent rainfall history placeholder, radar placeholder
  Forecast: Met Office rainfall forecast, river forecast placeholder

Current hazard H(t0)
  static susceptibility + observed rainfall

Predicted hazard H(t0 + delta)
  current hazard + future rainfall forcing + interaction term
```

SEPA flood maps are separated as static/baseline reference layers for calibration, validation, and official context. They are not used as dynamic rainfall forcing.

Fluvial and coastal workflows now follow the same high-level prediction framework:

```text
Baseline:
  SEPA flood map + DTM reference grid

Current forcing:
  Fluvial: SEPA river/tidal level observations + SEPA rainfall observations
  Coastal: SEPA river/tidal level observations as a provisional tide/sea-level proxy

Future forcing:
  Fluvial: Met Office rainfall forecast, with river forecast left unavailable
  Coastal: Met Office rainfall forecast is available, while tide/sea-level and storm-surge forecasts are unavailable
```

Unavailable dynamic inputs are explicitly recorded in metadata rather than fabricated. Fluvial outputs still need river flow observations/forecasts and station-specific river thresholds for scientific forecasting. Coastal outputs still need tide gauges, tide forecasts, surge forecasts, coastline/estuary boundaries, wave exposure, and coastal defence data.

Check Met Office Map Images access:

```powershell
$env:METOFFICE_MAP_API_KEY="your_rotated_map_images_key_here"
python metoffice_map_check.py --orders
```

Map images are suitable for visualization overlays or qualitative comparison. They should not be used as numeric rainfall inputs for the Core Analyst unless a separate georeferencing and colour/legend-to-value conversion workflow is defined and validated.

Check Met Office NSWWS warning feed access:

```powershell
$env:METOFFICE_NSWWWS_API_KEY="your_rotated_nswws_key_here"
$env:METOFFICE_NSWWWS_FEED_URL="the Atom Feed URL supplied by Met Office"
python metoffice_nswws_check.py --links
```

NSWWS is an Atom feed with linked warning GeoJSON endpoints. It should be used as a warning/context layer, for example a rain or thunderstorm warning modifier, rather than as a numeric rainfall raster.

Run tests:

```bash
pytest
```

## Replacing Demo Data

To use real Glasgow rasters, place Component 1 standardized GeoTIFFs in a data directory and replace the `StaticRasterSource` paths in `demo.py` or create a production workflow wrapper. Do not place raw downloaded datasets directly into the Core Analyst unless they already satisfy the input contract.
