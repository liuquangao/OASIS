# HydroMind WebGIS

HydroMind's WebGIS platform for Glasgow flood-risk layers. The stack is Leaflet, FastAPI, GeoServer, PostgreSQL/PostGIS, and Docker Compose.

The generated project folder is named `glagow-flood-webgis` to match the requested structure. If you prefer the corrected spelling, rename it to `glasgow-flood-webgis`.

## Updated Architecture

```text
Frontend WebGIS
  Leaflet, OpenStreetMap, layer control, legend, vector popups
        |
        | metadata + future GIS tool calls
        v
FastAPI Backend
  /datasets, /layers, /features, /analysis/*
        |
        | SQL / PostGIS functions
        v
PostgreSQL + PostGIS
  spatial vector storage and reusable GIS analysis

Frontend WebGIS
        |
        | WMS/WFS map rendering
        v
GeoServer
        |
        v
GeoTIFF rasters + PostGIS vector layers
```

GeoServer still serves visualization through WMS/WFS. FastAPI is the new agent-ready API layer for metadata, spatial queries, and reusable flood-risk analysis tools.

## Modified File Structure

```text
glagow-flood-webgis/
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── backend/
│   ├── Dockerfile
│   ├── api/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── requirements.txt
│   │   └── routes/
│   │       ├── analysis.py
│   │       ├── datasets.py
│   │       ├── features.py
│   │       └── layers.py
│   ├── analysis/
│   │   ├── db.py
│   │   ├── spatial_query.py
│   │   ├── overlay.py
│   │   └── exposure.py
│   ├── metadata/
│   │   └── repository.py
│   ├── processing/
│   └── postgis/
│       └── init.sql
├── data/
│   ├── raster/
│   ├── vector/
│   └── metadata/
│       └── datasets.json
├── docker-compose.yml
└── README.md
```

## Layer Plan

All source datasets should be converted to British National Grid, `EPSG:27700`.

| Group | Layers | Storage | Service |
| --- | --- | --- | --- |
| Flood Hazard | DEM, DTM, DSM, slope, flow accumulation, rivers, culverts, drainage network, SEPA flood maps, historical flood extent | GeoTIFF/PostGIS | WMS/WFS |
| Exposure | Buildings, roads, facilities, critical infrastructure | PostGIS | WFS |
| Vulnerability | Population, Census Data Zone, SIMD, social vulnerability indicators | PostGIS | WFS |
| Environmental Surface | Land cover, impervious surface, soil | GeoTIFF/PostGIS | WMS/WFS |

Layer metadata lives in `data/metadata/datasets.json`. The frontend reads `/layers` from FastAPI and dynamically builds the map layer control from this metadata.

## Step 1: Install Required Software

Install:

- Docker Desktop
- Git
- GDAL/OGR command line tools

Windows options:

```powershell
winget install Docker.DockerDesktop
winget install Git.Git
winget install OSGeo.OSGeo4W
```

After installing OSGeo4W, open the OSGeo4W Shell or add its `bin` folder to your `PATH`, then check:

```powershell
docker --version
docker compose version
gdalinfo --version
ogr2ogr --version
```

## Step 2: Create Docker Environment

From this project folder:

```powershell
cd glagow-flood-webgis
docker compose up -d
```

Services:

- PostGIS: `localhost:5432`
- GeoServer: `http://localhost:8080/geoserver`
- FastAPI: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- GeoServer login: `admin` / `geoserver`
- PostgreSQL login: database `glasgow_flood`, user `gis`, password `gis_password`

Check containers:

```powershell
docker compose ps
docker logs glasgow-postgis
docker logs glasgow-geoserver
docker logs glasgow-fastapi
```

If GeoServer image pulling fails, make sure `docker-compose.yml` uses the official image:

```yaml
image: docker.osgeo.org/geoserver:2.28.2
```

## Step 3: Configure PostGIS

The database is initialized automatically from `backend/postgis/init.sql`.

To connect:

```powershell
docker exec -it glasgow-postgis psql -U gis -d glasgow_flood
```

Useful checks:

```sql
SELECT postgis_full_version();
\dn
```

Schemas created:

```text
terrain
land_surface
hydrology
flood_information
built_environment
social_vulnerability
flood_hazard
exposure
vulnerability
environmental_surface
```

The older schemas are kept for compatibility with the first MVP import commands. The newer schemas support the agent-ready layer grouping.

## Step 4: Configure GeoServer Workspace

Open GeoServer:

```text
http://localhost:8080/geoserver
```

Create workspace:

- Name: `glasgow_flood`
- Namespace URI: `https://example.org/glasgow_flood`

You can also create it with REST:

```powershell
curl -u admin:geoserver -XPOST -H "Content-Type: text/xml" `
  -d "<workspace><name>glasgow_flood</name></workspace>" `
  http://localhost:8080/geoserver/rest/workspaces
```

Create a PostGIS store:

```powershell
curl -u admin:geoserver -XPOST -H "Content-Type: text/xml" `
  -d "<dataStore><name>postgis</name><connectionParameters><host>postgis</host><port>5432</port><database>glasgow_flood</database><user>gis</user><passwd>gis_password</passwd><dbtype>postgis</dbtype><schema>public</schema></connectionParameters></dataStore>" `
  http://localhost:8080/geoserver/rest/workspaces/glasgow_flood/datastores
```

For schema-specific stores, repeat the request and change `<name>` and `<schema>`, for example `hydrology` / `hydrology`.

## Step 5: Upload Example Datasets

Place source files here:

```text
data/raster/source/
data/vector/source/
```

Create output folders:

```powershell
mkdir data\raster\processed
mkdir data\vector\processed
```

### Raster Conversion To EPSG:27700 GeoTIFF

DEM:

```powershell
gdalwarp -t_srs EPSG:27700 -r bilinear -of GTiff -co COMPRESS=LZW `
  data/raster/source/dem.tif data/raster/processed/dem_27700.tif
```

DTM:

```powershell
gdalwarp -t_srs EPSG:27700 -r bilinear -of GTiff -co COMPRESS=LZW `
  data/raster/source/dtm.tif data/raster/processed/dtm_27700.tif
```

DSM:

```powershell
gdalwarp -t_srs EPSG:27700 -r bilinear -of GTiff -co COMPRESS=LZW `
  data/raster/source/dsm.tif data/raster/processed/dsm_27700.tif
```

Derived slope from DEM:

```powershell
gdaldem slope data/raster/processed/dem_27700.tif data/raster/processed/slope_27700.tif `
  -of GTiff -co COMPRESS=LZW
```

Flow accumulation placeholder command using WhiteboxTools if installed:

```powershell
whitebox_tools -r=D8FlowAccumulation `
  --dem=data/raster/processed/dem_27700.tif `
  --output=data/raster/processed/flow_accumulation_27700.tif `
  --out_type=cells
```

Land cover:

```powershell
gdalwarp -t_srs EPSG:27700 -r near -of GTiff -co COMPRESS=LZW `
  data/raster/source/land_cover.tif data/raster/processed/land_cover_27700.tif
```

Impervious surface:

```powershell
gdalwarp -t_srs EPSG:27700 -r near -of GTiff -co COMPRESS=LZW `
  data/raster/source/impervious_surface.tif data/raster/processed/impervious_surface_27700.tif
```

### Vector Import To PostGIS

Use `-t_srs EPSG:27700` for reprojection and `-nlt PROMOTE_TO_MULTI` for robust geometry loading.

Rivers:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/rivers.gpkg -nln hydrology.rivers -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Culverts:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/culverts.gpkg -nln hydrology.culverts -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Drainage network:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/drainage_network.gpkg -nln hydrology.drainage_network -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Buildings:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/buildings.gpkg -nln built_environment.buildings -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Roads:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/roads.gpkg -nln built_environment.roads -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Facilities:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/facilities.gpkg -nln built_environment.facilities -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Population:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/population.gpkg -nln social_vulnerability.population -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Census Data Zone:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/census_data_zone.gpkg -nln social_vulnerability.census_data_zone -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

SIMD:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/simd.gpkg -nln social_vulnerability.simd -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

## Step 6: Create Leaflet Frontend

The frontend is already in:

```text
frontend/index.html
frontend/app.js
frontend/style.css
```

Because the frontend calls GeoServer WFS with `fetch`, serve it through a small local web server:

```powershell
cd frontend
python -m http.server 3000
```

Open:

```text
http://localhost:3000
```

## Step 7: Connect Leaflet With GeoServer WMS/WFS

Leaflet uses:

- WMS endpoint: `http://localhost:8080/geoserver/glasgow_flood/wms`
- WFS endpoint: `http://localhost:8080/geoserver/glasgow_flood/ows`
- Metadata endpoint: `http://localhost:8000/layers`

Expected published layer names:

```text
glasgow_flood:dem
glasgow_flood:dtm
glasgow_flood:dsm
glasgow_flood:slope
glasgow_flood:flow_accumulation
glasgow_flood:land_cover
glasgow_flood:impervious_surface
glasgow_flood:rivers
glasgow_flood:culverts
glasgow_flood:drainage_network
glasgow_flood:sepa_flood_maps
glasgow_flood:historical_flood_extent
glasgow_flood:buildings
glasgow_flood:roads
glasgow_flood:facilities
glasgow_flood:population
glasgow_flood:census_data_zone
glasgow_flood:simd
```

If you publish with different layer names, update the `name` values in `frontend/app.js`.

In the new version, prefer updating `data/metadata/datasets.json` instead of editing frontend code.

For WMS rasters in GeoServer:

1. Add a GeoTIFF store in workspace `glasgow_flood`.
2. Use files from `/data/raster/processed`.
3. Publish each raster.
4. Confirm native SRS is `EPSG:27700`.
5. Enable declared SRS reprojection to `EPSG:4326`/`EPSG:3857` for web display.

The downloaded Glasgow Phase V DTM can be published with its verified elevation
style after GeoServer is running:

```powershell
.\scripts\publish-dem.ps1
```

This publishes the existing file mounted at
`/data/raster/source/NS56NE_50CM_DTM_PHASE5.tif` as
`glasgow_flood:dem`, uploads the workspace style `dem_elevation`, assigns it as
the default style, and verifies the WMS legend. The colour ramp is based on the
observed raster range of -1.495 m to 91.13 m, with `-9999` rendered transparent.

For WFS vectors in GeoServer:

1. Add a PostGIS datastore.
2. Publish tables from schemas such as `hydrology`, `built_environment`, and `social_vulnerability`.
3. Confirm native SRS is `EPSG:27700`.
4. Enable WFS and GeoJSON output.

## Step 8: Add New Datasets

1. Put raw files in `data/raster/source` or `data/vector/source`.
2. Reproject to `EPSG:27700`.
3. Store rasters as compressed GeoTIFFs in `data/raster/processed`.
4. Import vectors into the correct PostGIS schema with `ogr2ogr`.
5. Publish the layer in GeoServer under workspace `glasgow_flood`.
6. Add or update the dataset definition in `data/metadata/datasets.json`.
7. Restart FastAPI if it is running, because metadata is cached in the API process.

Raster template:

```powershell
gdalwarp -t_srs EPSG:27700 -r near -of GTiff -co COMPRESS=LZW `
  data/raster/source/new_layer.tif data/raster/processed/new_layer_27700.tif
```

Vector template:

```powershell
ogr2ogr -f PostgreSQL PG:"host=localhost port=5432 dbname=glasgow_flood user=gis password=gis_password" `
  data/vector/source/new_layer.gpkg -nln target_schema.new_layer -t_srs EPSG:27700 `
  -lco GEOMETRY_NAME=geom -lco FID=id -nlt PROMOTE_TO_MULTI -overwrite
```

Metadata raster definition:

```json
{
  "name": "new_layer",
  "display_name": "New Raster",
  "source": "source name",
  "category": "Environmental Surface",
  "type": "raster",
  "crs": "EPSG:27700",
  "spatial_resolution": "10 m",
  "temporal_resolution": "static",
  "update_frequency": "on source refresh",
  "description": "Description for users and future agents.",
  "layer": {
    "enabled": true,
    "service": "WMS",
    "geoserver_name": "new_layer"
  }
}
```

Metadata vector definition:

```json
{
  "name": "new_vector",
  "display_name": "New Vector",
  "source": "source name",
  "category": "Exposure",
  "type": "vector",
  "crs": "EPSG:27700",
  "spatial_resolution": "vector",
  "temporal_resolution": "static",
  "update_frequency": "on source refresh",
  "description": "Description for users and future agents.",
  "database_table": "exposure.new_vector",
  "layer": {
    "enabled": true,
    "service": "WFS",
    "geoserver_name": "new_vector",
    "color": "#1677b8"
  }
}
```

## API Documentation

Interactive docs are available after startup:

```text
http://localhost:8000/docs
```

Health check:

```powershell
curl http://localhost:8000/health
```

List dataset metadata:

```powershell
curl http://localhost:8000/datasets
```

List WebGIS layers:

```powershell
curl http://localhost:8000/layers
```

Query vector features from PostGIS:

```powershell
curl "http://localhost:8000/features/buildings?limit=20"
```

Query vector features with a WGS84 bbox:

```powershell
curl "http://localhost:8000/features/buildings?bbox=-4.35,55.80,-4.15,55.92&limit=50"
```

Spatial intersection:

```powershell
curl -X POST http://localhost:8000/analysis/intersection `
  -H "Content-Type: application/json" `
  -d "{\"source_layer\":\"historical_flood_extent\",\"target_layer\":\"buildings\",\"limit\":100}"
```

River buffer analysis:

```powershell
curl -X POST http://localhost:8000/analysis/buffer `
  -H "Content-Type: application/json" `
  -d "{\"layer_name\":\"rivers\",\"distance_m\":50,\"limit\":100}"
```

Exposure analysis:

```powershell
curl -X POST http://localhost:8000/analysis/exposure `
  -H "Content-Type: application/json" `
  -d "{\"flood_extent_layer\":\"historical_flood_extent\",\"building_layer\":\"buildings\",\"population_layer\":\"population\",\"population_field\":\"population\"}"
```

Zonal statistics contract:

```powershell
curl -X POST http://localhost:8000/analysis/zonal-statistics `
  -H "Content-Type: application/json" `
  -d "{\"raster_layer\":\"sepa_flood_maps\",\"zone_layer\":\"population\",\"statistic\":\"mean\"}"
```

Raster overlay contract:

```powershell
curl -X POST http://localhost:8000/analysis/raster-overlay `
  -H "Content-Type: application/json" `
  -d "{\"raster_layers\":[\"dem\",\"land_cover\",\"sepa_flood_maps\"],\"method\":\"weighted_sum\"}"
```

The vector analysis endpoints execute PostGIS functions now. The raster endpoints define stable agent tool contracts and can later be connected to PostGIS Raster, GDAL, or rasterio processing workers.

## Local Test Workflow

Start services:

```powershell
docker compose up -d --build
```

Check API:

```powershell
curl http://localhost:8000/health
curl http://localhost:8000/layers
```

Start frontend:

```powershell
cd frontend
python -m http.server 3000
```

Open:

```text
http://localhost:3000
```

Expected behavior:

- Leaflet opens centered on Glasgow.
- OpenStreetMap is visible.
- Layer control is generated from `data/metadata/datasets.json` through FastAPI.
- Raster layers request GeoServer WMS.
- Vector layers request GeoServer WFS and show popups.
- Legend updates when layers are toggled.

## Notes

- Use `EPSG:27700` for database storage and raster preprocessing.
- GeoServer can reproject published layers to web map display coordinates for Leaflet.
- Large WFS layers should later be replaced by tiled vector delivery or WMS rendering for performance.
