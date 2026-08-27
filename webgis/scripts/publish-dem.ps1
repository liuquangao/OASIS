[CmdletBinding()]
param(
    [string]$GeoServerUrl = "http://localhost:8080/geoserver",
    [string]$AdminUser = "admin",
    [string]$AdminPassword = "geoserver",
    [string]$Workspace = "glasgow_flood",
    [string]$LayerName = "dem",
    [string]$StyleName = "dem_elevation",
    [string]$DemPathInContainer = "/data/raster/source/NS56NE_50CM_DTM_PHASE5.tif"
)

$ErrorActionPreference = "Stop"

foreach ($value in @($Workspace, $LayerName, $StyleName)) {
    if ($value -notmatch '^[A-Za-z0-9_.-]+$') {
        throw "GeoServer names may only contain letters, digits, dot, underscore, and hyphen: $value"
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$demPathOnHost = Join-Path $projectRoot "data\raster\source\NS56NE_50CM_DTM_PHASE5.tif"
$stylePath = Join-Path $projectRoot "geoserver\styles\dem_elevation.sld"

if (-not (Test-Path -LiteralPath $demPathOnHost -PathType Leaf)) {
    throw "DEM file not found: $demPathOnHost"
}
if (-not (Test-Path -LiteralPath $stylePath -PathType Leaf)) {
    throw "SLD file not found: $stylePath"
}

$baseUrl = $GeoServerUrl.TrimEnd('/')
$credentialBytes = [Text.Encoding]::ASCII.GetBytes("${AdminUser}:${AdminPassword}")
$headers = @{ Authorization = "Basic $([Convert]::ToBase64String($credentialBytes))" }

function Invoke-GeoServerRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ContentType,
        [AllowNull()][object]$Body
    )

    $arguments = @{
        Uri = "${baseUrl}${Path}"
        Method = $Method
        Headers = $headers
        UseBasicParsing = $true
        TimeoutSec = 60
    }
    if ($ContentType) { $arguments.ContentType = $ContentType }
    if ($null -ne $Body) { $arguments.Body = $Body }
    Invoke-WebRequest @arguments
}

function Test-GeoServerResource {
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        $null = Invoke-GeoServerRequest -Method Get -Path $Path
        return $true
    }
    catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
            return $false
        }
        throw
    }
}

try {
    $version = Invoke-GeoServerRequest -Method Get -Path "/rest/about/version.json"
}
catch {
    throw "GeoServer is not reachable at $baseUrl. Start it before running this script. $($_.Exception.Message)"
}

if (-not (Test-GeoServerResource -Path "/rest/workspaces/${Workspace}.json")) {
    $workspaceXml = "<workspace><name>${Workspace}</name></workspace>"
    $null = Invoke-GeoServerRequest -Method Post -Path "/rest/workspaces" -ContentType "application/xml" -Body $workspaceXml
    Write-Host "Created workspace ${Workspace}."
}

$qualifiedLayer = [Uri]::EscapeDataString("${Workspace}:${LayerName}")
$layerExists = Test-GeoServerResource -Path "/rest/layers/${qualifiedLayer}.json"

if (-not $layerExists) {
    $storeExists = Test-GeoServerResource -Path "/rest/workspaces/${Workspace}/coveragestores/${LayerName}.json"
    if ($storeExists) {
        throw "Coverage store ${Workspace}:${LayerName} exists but the layer does not. Resolve the incomplete store before retrying."
    }

    $externalFileUrl = "file:${DemPathInContainer}"
    $publishPath = "/rest/workspaces/${Workspace}/coveragestores/${LayerName}/external.geotiff?configure=first&coverageName=${LayerName}"
    $null = Invoke-GeoServerRequest -Method Put -Path $publishPath -ContentType "text/plain" -Body $externalFileUrl
    Write-Host "Published ${Workspace}:${LayerName} from ${externalFileUrl}."
}

$styleExists = Test-GeoServerResource -Path "/rest/workspaces/${Workspace}/styles/${StyleName}.json"
$styleBody = [IO.File]::ReadAllBytes($stylePath)
if ($styleExists) {
    $null = Invoke-GeoServerRequest -Method Put -Path "/rest/workspaces/${Workspace}/styles/${StyleName}" -ContentType "application/vnd.ogc.sld+xml" -Body $styleBody
    Write-Host "Updated style ${Workspace}:${StyleName}."
}
else {
    $null = Invoke-GeoServerRequest -Method Post -Path "/rest/workspaces/${Workspace}/styles?name=${StyleName}" -ContentType "application/vnd.ogc.sld+xml" -Body $styleBody
    Write-Host "Created style ${Workspace}:${StyleName}."
}

$layerXml = "<layer><defaultStyle><name>${StyleName}</name><workspace>${Workspace}</workspace></defaultStyle></layer>"
$null = Invoke-GeoServerRequest -Method Put -Path "/rest/layers/${qualifiedLayer}" -ContentType "application/xml" -Body $layerXml

$null = Invoke-GeoServerRequest -Method Get -Path "/rest/layers/${qualifiedLayer}.json"
$legendUrl = "${baseUrl}/${Workspace}/wms?service=WMS&version=1.1.1&request=GetLegendGraphic&format=image/png&layer=${Workspace}:${LayerName}"
$legend = Invoke-WebRequest -Uri $legendUrl -UseBasicParsing -TimeoutSec 60
if ($legend.StatusCode -ne 200 -or $legend.Headers["Content-Type"] -notlike "image/png*") {
    throw "Layer was published, but its WMS legend could not be verified."
}

Write-Host "Verified ${Workspace}:${LayerName} with default style ${Workspace}:${StyleName}."
Write-Host "WMS: ${baseUrl}/${Workspace}/wms"
Write-Host "Legend: ${legendUrl}"
