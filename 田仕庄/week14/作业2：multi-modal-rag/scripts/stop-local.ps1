# One-command local stop: API/Worker/Milvus Lite, plus Kafka/MinerU by default
# Usage: powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1
# Keep dependencies: powershell ... stop-local.ps1 -KeepDeps

param(
    [switch]$KeepDeps
)

$ErrorActionPreference = "Continue"
$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT
. "$PSScriptRoot\local-env.ps1"

$STATE = Join-Path $ROOT "data\.local"
$pidsPath = Join-Path $STATE "pids.json"

if (Test-Path $pidsPath) {
    $pids = Get-Content $pidsPath -Raw | ConvertFrom-Json
    foreach ($id in @($pids.api, $pids.worker, $pids.lite)) {
        if ($id) {
            Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $pidsPath -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped API / Worker / Milvus Lite"
} else {
    Write-Host "No start record found; stopping dependencies only"
}

if (-not $KeepDeps) {
    docker compose -f deploy/compose.local.yaml stop kafka *> $null
    & $MINERU server stop *> $null
    Write-Host "Stopped Kafka / MinerU"
} else {
    Write-Host "Kafka / MinerU left running"
}
