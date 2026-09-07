# Data Preparation And Historical Analysis

[Back to the quick start](../README.md#quick-start-windows-powershell). Run the commands below from the repository root after completing installation.

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

## Rebuild Data From Licensed Sources

If `data/Input` is missing, rebuild it from source. The UKCEH LCM 2019 file is
licence-gated and must be downloaded by the user from the official UKCEH EIDC
order page. Do not commit or redistribute it.

```powershell
.\.venv\Scripts\hydromind.exe data preflight --lcm2019 D:\path\to\gb2019lcm25m.tif --accept-licences
.\.venv\Scripts\hydromind.exe data rebuild --lcm2019 D:\path\to\gb2019lcm25m.tif --accept-licences
.\.venv\Scripts\hydromind.exe data verify
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
.\.venv\Scripts\hydromind.exe historical-validation --issue-time 2023-10-06T06:00:00Z --forecast-horizon-hours 24
```
