[CmdletBinding()]
param(
    [string]$GeoServerUrl = "http://localhost:8080/geoserver",
    [string]$AdminUser = "admin",
    [string]$AdminPassword = "geoserver"
)

$ErrorActionPreference = "Stop"
$workspace = "glasgow_flood"
$layer = "current_hazard_class_5m"
$style = "hazard_class"
$projectRoot = Split-Path -Parent $PSScriptRoot
$rasterPath = Join-Path $projectRoot ".runtime\data_dir\data\glasgow_flood\current_hazard_class_5m\current_hazard_class_5m.geotiff"
$baseUrl = $GeoServerUrl.TrimEnd('/')
$credential = [Convert]::ToBase64String(
    [Text.Encoding]::ASCII.GetBytes("${AdminUser}:${AdminPassword}")
)
$headers = @{ Authorization = "Basic ${credential}" }
$qualifiedLayer = [Uri]::EscapeDataString("${workspace}:${layer}")

try {
    $null = Invoke-WebRequest -Uri "${baseUrl}/rest/layers/${qualifiedLayer}.json" -Headers $headers -UseBasicParsing -TimeoutSec 30
}
catch {
    if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 404) { throw }
    $rasterBytes = [IO.File]::ReadAllBytes($rasterPath)
    $publishUrl = "${baseUrl}/rest/workspaces/${workspace}/coveragestores/${layer}/file.geotiff?configure=first&coverageName=${layer}"
    $null = Invoke-WebRequest -Uri $publishUrl -Method Put -Headers $headers -ContentType "image/tiff" -Body $rasterBytes -UseBasicParsing -TimeoutSec 120
}

$layerXml = "<layer><defaultStyle><name>${style}</name><workspace>${workspace}</workspace></defaultStyle></layer>"
$null = Invoke-WebRequest -Uri "${baseUrl}/rest/layers/${qualifiedLayer}" -Method Put -Headers $headers -ContentType "application/xml" -Body $layerXml -UseBasicParsing -TimeoutSec 30
$null = Invoke-WebRequest -Uri "${baseUrl}/rest/reload" -Method Post -Headers $headers -UseBasicParsing -TimeoutSec 120
Write-Host "Published ${workspace}:${layer}."
