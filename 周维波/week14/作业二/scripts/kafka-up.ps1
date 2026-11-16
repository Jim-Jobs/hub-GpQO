<#
.SYNOPSIS
  启动本地单机 Kafka（KRaft 模式，不需要 ZooKeeper / Docker / 系统级 Java）。

.DESCRIPTION
  Kafka 自带 .bat 启动脚本对路径引用和 cmd 环境比较敏感，这里直接调用
  `java -cp <kafka>\libs\* kafka.Kafka <config>` 启动 broker，行为与 bin 脚本一致但更可控：
  1) 首次运行会用 scripts/download_runtime.py 下载便携 JDK17 + Kafka 到 .runtime/
  2) 生成 .runtime/kafka-data/server.properties
  3) 首次启动用 kafka.tools.StorageTool format（KRaft 必须）
  4) 后台启动 broker，等待 9092 可连接（topic 由应用侧 AdminClient 幂等创建）

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\kafka-up.ps1
  powershell -ExecutionPolicy Bypass -File scripts\kafka-up.ps1 -ForceReformat
#>
param(
  [switch]$ForceReformat
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $ProjectRoot ".runtime"
$KafkaData = Join-Path $Runtime "kafka-data"
$LogDir = Join-Path $KafkaData "logs"
$PidFile = Join-Path $Runtime "kafka.pid"

function Test-PortOpen([int]$Port, [int]$TimeoutMs = 1000) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    return $client.ConnectAsync("127.0.0.1", $Port).Wait($TimeoutMs)
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Resolve-JavaExe([string]$RuntimeDir) {
  $direct = Join-Path $RuntimeDir "jdk\bin\java.exe"
  if (Test-Path -LiteralPath $direct) { return (Resolve-Path -LiteralPath $direct).Path }
  $nested = Get-ChildItem -LiteralPath (Join-Path $RuntimeDir "jdk") -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "bin\java.exe") } |
    Select-Object -First 1
  if ($nested) { return (Join-Path $nested.FullName "bin\java.exe") }
  return $null
}

function Resolve-KafkaHome([string]$RuntimeDir) {
  $nested = Get-ChildItem -LiteralPath (Join-Path $RuntimeDir "kafka") -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "libs\kafka_2.13-3.9.1.jar") } |
    Select-Object -First 1
  if ($nested) { return $nested.FullName }
  return $null
}

function New-KafkaClusterId {
  $hex = [guid]::NewGuid().ToString("N")
  $bytes = New-Object 'byte[]' 16
  for ($index = 0; $index -lt 16; $index++) {
    $bytes[$index] = [Convert]::ToByte($hex.Substring($index * 2, 2), 16)
  }
  return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

if (Test-PortOpen -Port 9092 -TimeoutMs 1500) {
  Write-Host "Kafka 已在 127.0.0.1:9092 运行，跳过启动。" -ForegroundColor Green
  exit 0
}

$JavaExe = Resolve-JavaExe $Runtime
$KafkaHome = Resolve-KafkaHome $Runtime

if (-not $JavaExe -or -not $KafkaHome) {
  Write-Host "未找到本地 JDK/Kafka，开始下载（约 300MB，仅首次）…" -ForegroundColor Yellow
  $python = Join-Path $Runtime "venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $python)) { $python = "python" }
  & $python (Join-Path $ProjectRoot "scripts\download_runtime.py")
  if ($LASTEXITCODE -ne 0) { throw "运行时下载失败，请检查网络或手动下载后放到 .runtime/jdk 与 .runtime/kafka" }
  $JavaExe = Resolve-JavaExe $Runtime
  $KafkaHome = Resolve-KafkaHome $Runtime
}

if (-not $JavaExe -or -not $KafkaHome) { throw "JDK / Kafka 目录不完整，请删除 .runtime/jdk、.runtime/kafka 后重试" }

New-Item -ItemType Directory -Force -Path $KafkaData, $LogDir | Out-Null

$configPath = Join-Path $KafkaData "server.properties"
$logDirs = $LogDir -replace "\\", "/"
$properties = @"
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@127.0.0.1:9093
listeners=PLAINTEXT://127.0.0.1:9092,CONTROLLER://127.0.0.1:9093
advertised.listeners=PLAINTEXT://127.0.0.1:9092
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
inter.broker.listener.name=PLAINTEXT
log.dirs=$logDirs
num.partitions=3
default.replication.factor=1
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
auto.create.topics.enable=true
log.retention.hours=72
"@
[IO.File]::WriteAllText($configPath, $properties, (New-Object Text.UTF8Encoding $false))

if ($ForceReformat -and (Test-Path -LiteralPath $LogDir)) {
  Write-Host "按参数强制重建日志目录：$LogDir" -ForegroundColor Yellow
  Remove-Item -LiteralPath $LogDir -Recurse -Force
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

$metaFile = Join-Path $LogDir "meta.properties"
$classPath = Join-Path $KafkaHome "libs\*"

if (-not (Test-Path -LiteralPath $metaFile)) {
  $clusterId = New-KafkaClusterId
  Write-Host "格式化 KRaft 存储（clusterId=$clusterId）…"
  & $JavaExe -Xmx256M -Xms128M -cp $classPath kafka.tools.StorageTool format -t $clusterId -c $configPath
  if ($LASTEXITCODE -ne 0) { throw "kafka.tools.StorageTool format 失败，请查看上方输出" }
}

$stdoutLog = Join-Path $Runtime "kafka.out.log"
$stderrLog = Join-Path $Runtime "kafka.err.log"

Write-Host "启动 Kafka（日志：$stdoutLog）…"
$process = Start-Process -FilePath $JavaExe `
  -ArgumentList @("-Xmx1G", "-Xms512M", "-cp", $classPath, "kafka.Kafka", $configPath) `
  -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
$process.Id | Set-Content -LiteralPath $PidFile -Encoding utf8

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
  if (Test-PortOpen -Port 9092) { break }
  Start-Sleep -Milliseconds 1500
}

if (-not (Test-PortOpen -Port 9092)) {
  Write-Host "Kafka 启动超时，最近日志：" -ForegroundColor Red
  Get-Content -LiteralPath $stdoutLog -Tail 30 -ErrorAction SilentlyContinue
  Get-Content -LiteralPath $stderrLog -Tail 30 -ErrorAction SilentlyContinue
  throw "Kafka 未能在 90s 内监听 9092"
}

Write-Host "Kafka 已就绪：127.0.0.1:9092（KRaft 单机，PID=$($process.Id)）" -ForegroundColor Green
