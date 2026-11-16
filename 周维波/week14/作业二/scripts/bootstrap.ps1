<#
.SYNOPSIS
  一键初始化 Python 环境（venv + 依赖）。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -IndexUrl https://mirrors.aliyun.com/pypi/simple
#>
param(
  [string]$IndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple",
  [switch]$WithModels
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".runtime\venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
  Write-Host "创建虚拟环境 .runtime\venv …"
  python -m venv (Join-Path $ProjectRoot ".runtime\venv")
  if (-not (Test-Path -LiteralPath $VenvPython)) { throw "venv 创建失败，请确认 python 在 PATH 中" }
}

Write-Host "安装依赖（index: $IndexUrl）…"
& $VenvPython -m pip install --upgrade pip -i $IndexUrl --disable-pip-version-check | Out-Null
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt") -i $IndexUrl --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

if ($WithModels) {
  Write-Host "安装本地向量模型依赖（torch 较大，仅需要 bge/CLIP 真实向量时安装）…"
  & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements-models.txt") -i $IndexUrl --disable-pip-version-check
}

Write-Host ""
Write-Host "环境就绪。接下来：" -ForegroundColor Green
Write-Host "  1) scripts\kafka-up.ps1     # 启动本地 Kafka（自动下载便携 JDK+Kafka）"
Write-Host "  2) scripts\run-api.ps1      # 启动 API + 前端，浏览器打开 http://127.0.0.1:8000"
Write-Host "  3) scripts\run-worker.ps1   # 另开一个窗口启动解析 worker"
