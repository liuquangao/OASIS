[CmdletBinding()]
param([int]$Port = 8080)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$serverRoot = Join-Path $projectRoot ".runtime"
$java = "C:\Program Files\Android\Android Studio\jbr\bin\java.exe"

if (-not (Test-Path -LiteralPath (Join-Path $serverRoot "start.jar"))) {
    throw "GeoServer runtime is missing from $serverRoot"
}
if (-not (Test-Path -LiteralPath $java)) {
    throw "Java 21 was not found at $java"
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "GeoServer is already listening on port $Port."
    return
}

$logs = Join-Path $serverRoot "logs"
$process = Start-Process -FilePath $java -ArgumentList @(
    "-DGEOSERVER_DATA_DIR=$(Join-Path $serverRoot 'data_dir')",
    "-Djetty.http.port=$Port",
    "-Xms512m", "-Xmx1g", "-jar", "start.jar"
) -WorkingDirectory $serverRoot -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $logs "standalone-out.log") `
  -RedirectStandardError (Join-Path $logs "standalone-error.log")

Write-Host "GeoServer started with PID $($process.Id) at http://localhost:$Port/geoserver"
