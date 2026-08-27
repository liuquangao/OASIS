[CmdletBinding()]
param(
    [string]$GeoServerUrl = "http://localhost:8080/geoserver",
    [string]$AdminUser = "admin",
    [string]$AdminPassword = "geoserver",
    [string]$Workspace = "glasgow_flood",
    [string]$LayerName = "hazard_class_5m",
    [string]$StyleName = "hazard_class"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$rasterPath = Join-Path $projectRoot "data\raster\processed\hazard_class_5m.tif"
$stylePath = Join-Path $projectRoot "geoserver\styles\hazard_class.sld"

foreach ($path in @($rasterPath, $stylePath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required file not found: $path"
    }
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
        TimeoutSec = 120
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

$null = Invoke-GeoServerRequest -Method Get -Path "/rest/about/version.json"
if (-not (Test-GeoServerResource -Path "/rest/workspaces/${Workspace}.json")) {
    $workspaceXml = "<workspace><name>${Workspace}</name></workspace>"
    $null = Invoke-GeoServerRequest -Method Post -Path "/rest/workspaces" -ContentType "application/xml" -Body $workspaceXml
}

$qualifiedLayer = [Uri]::EscapeDataString("${Workspace}:${LayerName}")
if (-not (Test-GeoServerResource -Path "/rest/layers/${qualifiedLayer}.json")) {
    $rasterBytes = [IO.File]::ReadAllBytes($rasterPath)
    $publishPath = "/rest/workspaces/${Workspace}/coveragestores/${LayerName}/file.geotiff?configure=first&coverageName=${LayerName}"
    $null = Invoke-GeoServerRequest -Method Put -Path $publishPath -ContentType "image/tiff" -Body $rasterBytes
}

$styleBytes = [IO.File]::ReadAllBytes($stylePath)
$styleExists = Test-GeoServerResource -Path "/rest/workspaces/${Workspace}/styles/${StyleName}.json"
if ($styleExists) {
    $null = Invoke-GeoServerRequest -Method Put -Path "/rest/workspaces/${Workspace}/styles/${StyleName}" -ContentType "application/vnd.ogc.sld+xml" -Body $styleBytes
}
else {
    $null = Invoke-GeoServerRequest -Method Post -Path "/rest/workspaces/${Workspace}/styles?name=${StyleName}" -ContentType "application/vnd.ogc.sld+xml" -Body $styleBytes
}

$layerXml = "<layer><defaultStyle><name>${StyleName}</name><workspace>${Workspace}</workspace></defaultStyle></layer>"
$null = Invoke-GeoServerRequest -Method Put -Path "/rest/layers/${qualifiedLayer}" -ContentType "application/xml" -Body $layerXml

$legendUrl = "${baseUrl}/${Workspace}/wms?service=WMS&version=1.1.1&request=GetLegendGraphic&format=image/png&layer=${Workspace}:${LayerName}"
$legend = Invoke-WebRequest -Uri $legendUrl -UseBasicParsing -TimeoutSec 120
if ($legend.StatusCode -ne 200 -or $legend.Headers["Content-Type"] -notlike "image/png*") {
    throw "Published layer could not be verified through WMS."
}

Write-Host "Verified ${Workspace}:${LayerName} with style ${Workspace}:${StyleName}."
