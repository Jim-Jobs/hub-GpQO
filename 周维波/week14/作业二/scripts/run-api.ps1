<#
.SYNOPSIS
  启动 FastAPI（含前端页面）。默认 http://127.0.0.1:8000
#>
param(
  [int]$Port = 8000,
  [switch]$Reload
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".runtime\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) { throw "未找到 venv，请先运行 scripts\bootstrap.ps1" }

Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUTF8 = "1"

$args = @("-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", "$Port")
if ($Reload) { $args += "--reload" }

Write-Host "API 启动中：http://127.0.0.1:$Port" -ForegroundColor Green
& $VenvPython @args
