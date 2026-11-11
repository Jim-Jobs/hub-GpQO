# One-command local start: Docker/Kafka + MinerU + Milvus Lite + API + Worker
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT
. "$PSScriptRoot\local-env.ps1"

$STATE = Join-Path $ROOT "data\.local"
$LOG = Join-Path $STATE "logs"
New-Item -ItemType Directory -Force -Path $LOG | Out-Null

Write-Host "[0/7] Preflight..."
$envFile = Join-Path $ROOT ".env"
if (-not (Test-Path $envFile)) { throw ".env not found at $envFile" }
$envText = Get-Content $envFile -Raw
if ($envText -notmatch "(?m)^\s*DASHSCOPE_API_KEY\s*=\s*\S+") {
    throw ".env is missing a non-empty DASHSCOPE_API_KEY"
}
foreach ($modelPath in @($env:BGE_MODEL_PATH, $env:CLIP_MODEL_PATH)) {
    if (-not (Test-Path $modelPath)) { throw "model directory not found: $modelPath" }
}
foreach ($tool in @($PY, $MINERU, $LITE)) {
    if (-not (Test-Path $tool)) { throw "tool not found: $tool" }
}

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $task.Wait(500)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Port([int]$Port, [int]$Seconds) {
    for ($i = 0; $i -lt ($Seconds * 2); $i++) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-WithTimeout([string]$File, [string[]]$Arguments, [int]$TimeoutSec, [string]$OutLog, [string]$ErrLog) {
    $proc = Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $ROOT `
        -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
    if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        throw "command timeout after ${TimeoutSec}s: $File $($Arguments -join ' '); see $OutLog / $ErrLog"
    }
    return $proc.ExitCode
}

$apiProc = $null
$workerProc = $null
$liteProc = $null

try {
    Write-Host "[1/7] Checking Docker..."
    docker info --format "{{.ServerVersion}}" *> $null
    if ($LASTEXITCODE -ne 0) {
        $desktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        if (-not (Test-Path $desktop)) { throw "Docker Desktop not found and docker daemon is not running" }
        Write-Host "      Starting Docker Desktop..."
        Start-Process -FilePath $desktop
        $dockerReady = $false
        for ($i = 0; $i -lt 120; $i++) {
            docker info --format "{{.ServerVersion}}" *> $null
            if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
            Start-Sleep -Seconds 1
        }
        if (-not $dockerReady) { throw "Docker Desktop startup timed out; start it manually and retry" }
    }

    Write-Host "[2/7] Starting Kafka..."
    if (-not (Test-Port 9092)) {
        $code = Start-WithTimeout "docker" @("compose", "-f", "deploy/compose.local.yaml", "up", "-d", "kafka") 120 "$LOG\kafka.out" "$LOG\kafka.err"
        if ($null -ne $code -and $code -ne 0) { throw "docker compose failed (exit $code); see $LOG\kafka.err" }
        if (-not (Wait-Port 9092 90)) { throw "Kafka 9092 not ready; see $LOG\kafka.err" }
    }
    Write-Host "      Kafka ready"

    Write-Host "[3/7] Starting MinerU..."
    if (-not (Test-Port 15980)) {
        $code = Start-WithTimeout $MINERU @("server", "start") 90 "$LOG\mineru.out" "$LOG\mineru.err"
        if ($null -ne $code -and $code -ne 0) { throw "MinerU start failed (exit $code); see $LOG\mineru.err" }
        if (-not (Wait-Port 15980 60)) { throw "MinerU 15980 not ready; see $LOG\mineru.err" }
    }
    Write-Host "      MinerU ready"

    Write-Host "[4/7] Starting Milvus Lite server..."
    if (-not (Test-Port 19530)) {
        $liteProc = Start-Process -FilePath $LITE -ArgumentList @(
            "server", "--data-dir", (Join-Path $ROOT "data\milvus-lite-server"),
            "--host", "127.0.0.1", "--port", "19530"
        ) -WorkingDirectory $ROOT -WindowStyle Hidden `
          -RedirectStandardOutput "$LOG\lite.out" -RedirectStandardError "$LOG\lite.err" -PassThru
        if (-not (Wait-Port 19530 60)) { throw "Milvus Lite 19530 not ready; see $LOG\lite.err" }
    }
    Write-Host "      Milvus Lite ready"

    Write-Host "[5/7] Checking Milvus collection..."
    $code = Start-WithTimeout $PY @("scripts\ensure_milvus_schema.py") 120 "$LOG\schema.out" "$LOG\schema.err"
    if ($null -ne $code -and $code -ne 0) { throw "Milvus collection initialization failed; see $LOG\schema.err" }
    Write-Host "      Collection ready"

    Write-Host "[6/7] Starting API..."
    if (Test-Port 8000) { throw "Port 8000 is in use; run stop-local.ps1 or close the process first" }
    $apiProc = Start-Process -FilePath $PY -ArgumentList @(
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory $ROOT -WindowStyle Hidden `
      -RedirectStandardOutput "$LOG\api.out" -RedirectStandardError "$LOG\api.err" -PassThru

    $health = $null
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
            if ($health.status -eq "ok") { break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $health -or $health.status -ne "ok") {
        throw "API not ready; see $LOG\api.err"
    }
    Write-Host "      API ready"

    Write-Host "[7/7] Starting Worker..."
    $workerProc = Start-Process -FilePath $PY -ArgumentList @("-m", "app.worker") `
      -WorkingDirectory $ROOT -WindowStyle Hidden `
      -RedirectStandardOutput "$LOG\worker.out" -RedirectStandardError "$LOG\worker.err" -PassThru

    $litePid = $null
    if ($liteProc) { $litePid = $liteProc.Id }
    $pids = [ordered]@{
        api = $apiProc.Id
        worker = $workerProc.Id
        lite = $litePid
    }
    $pids | ConvertTo-Json | Set-Content -Path (Join-Path $STATE "pids.json") -Encoding utf8

    try {
        $deps = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/deps" -TimeoutSec 10
        $bad = @($deps.deps.PSObject.Properties | Where-Object { -not $_.Value.ok } | ForEach-Object { $_.Name })
        if ($bad.Count -gt 0) { Write-Host "WARNING: dependencies not ready: $($bad -join ', ')" }
    } catch { }

    Write-Host ""
    Write-Host "System ready: http://127.0.0.1:8000"
    Write-Host "Logs: $LOG"
    Write-Host "Stop: powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1"
} catch {
    foreach ($proc in @($apiProc, $workerProc, $liteProc)) {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    throw
}
