[CmdletBinding()]
param(
    [ValidateRange(1, 200)][double]$RadiusKm = 30,
    [ValidateRange(1, 10)][int]$StationLimit = 10,
    [ValidateRange(1, 168)][int]$RainfallHours = 24,
    [ValidateRange(1, 31)][int]$WaterLevelDays = 1
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$hydromindRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot "..\..")).Path
$hydromindExe = Join-Path $hydromindRoot ".venv\Scripts\hydromind.exe"
$snapshotPath = Join-Path $projectRoot "frontend\assets\terrain-monitoring\monitoring-snapshot.js"

if (-not (Test-Path -LiteralPath $hydromindExe -PathType Leaf)) {
    throw "HydroMind CLI was not found: $hydromindExe"
}

function Invoke-HydroMindJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $text = (& $hydromindExe @Arguments | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "HydroMind command failed: $($Arguments -join ' ')"
    }
    return $text | ConvertFrom-Json
}

$levels = Invoke-HydroMindJson -Arguments @(
    "tool", "water-levels", "--place", "glasgow",
    "--radius-km", "$RadiusKm", "--days", "$WaterLevelDays",
    "--limit", "$StationLimit"
)
$rainfall = Invoke-HydroMindJson -Arguments @(
    "tool", "rainfall", "--place", "glasgow",
    "--radius-km", "$RadiusKm", "--hours", "$RainfallHours",
    "--limit", "$StationLimit"
)

$payload = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    place = "Glasgow"
    rainfall = $rainfall
    water_levels = $levels
}
$json = $payload | ConvertTo-Json -Depth 30 -Compress
$javascript = "window.HYDROMIND_MONITORING_DATA = ${json};`n"
[IO.File]::WriteAllText($snapshotPath, $javascript, [Text.UTF8Encoding]::new($false))

Write-Host "Updated monitoring snapshot: $snapshotPath"
Write-Host "Rainfall stations: $($rainfall.station_count)"
Write-Host "Water-level stations: $($levels.station_count)"
