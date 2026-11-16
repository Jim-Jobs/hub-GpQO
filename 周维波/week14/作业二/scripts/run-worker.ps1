<#
.SYNOPSIS
  启动离线解析 worker（Kafka 消费者，串行/多进程均可）。
#>
param(
  [int]$Concurrency = 1
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".runtime\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) { throw "未找到 venv，请先运行 scripts\bootstrap.ps1" }

Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUTF8 = "1"

if ($Concurrency -gt 1) {
  Write-Host "启动 $Concurrency 个 worker（同一 group，Kafka 会自动分配分区）…"
  $jobs = 1..$Concurrency | ForEach-Object {
    Start-Process -FilePath $VenvPython -ArgumentList "-m", "app.worker.main" -PassThru
  }
  Write-Host "worker PIDs: $($jobs.Id -join ', ')"
  $jobs | Wait-Process
} else {
  & $VenvPython -m app.worker.main
}
