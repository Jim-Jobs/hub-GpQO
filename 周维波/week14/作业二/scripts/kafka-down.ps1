<#
.SYNOPSIS
  停止本地 Kafka（按 .runtime/kafka.pid 结束进程树）。
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".runtime\kafka.pid"

if (-not (Test-Path -LiteralPath $PidFile)) {
  Write-Host "没有找到 .runtime/kafka.pid，可能 Kafka 未由本脚本启动。" -ForegroundColor Yellow
  exit 0
}

$kafkaPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
Write-Host "停止 Kafka（PID=$kafkaPid）…"
Stop-Process -Id $kafkaPid -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "已停止。" -ForegroundColor Green
