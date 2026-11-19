# 用法示例：
#   .\scripts\build.ps1                                   # 只构建本地镜像
#   .\scripts\build.ps1 -Registry registry.cn-hangzhou.aliyuncs.com -Namespace your-ns -Push
# 说明：PowerShell 7 下执行，首次推送到阿里云 ACR 需先 docker login。
param(
    [string]$ImageName = "sentiment-agents",
    [string]$Version = "1.0.0",
    [string]$Registry = "",
    [string]$Namespace = "",
    [string]$PipIndexUrl = "https://mirrors.aliyun.com/pypi/simple/",
    [switch]$Push
)

$ErrorActionPreference = "Stop"

function Get-DockerCli {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $fallback) { return $fallback }
    throw "未找到 docker CLI，请先安装并启动 Docker Desktop。"
}

$docker = Get-DockerCli
$root = Split-Path -Parent $PSScriptRoot
$localTag = "${ImageName}:${Version}"

Push-Location $root
try {
    Write-Host "==> 构建镜像 $localTag" -ForegroundColor Cyan
    & $docker build --build-arg "PIP_INDEX_URL=$PipIndexUrl" -t $localTag .
    if ($LASTEXITCODE -ne 0) { throw "镜像构建失败（exit $LASTEXITCODE）" }

    Write-Host "==> 本地镜像就绪" -ForegroundColor Green
    & $docker images $localTag

    if ($Push) {
        if (-not $Registry -or -not $Namespace) {
            throw "推送需要同时提供 -Registry 与 -Namespace，例如 -Registry registry.cn-hangzhou.aliyuncs.com -Namespace your-ns"
        }
        $remoteTag = "$Registry/$Namespace/${localTag}"
        Write-Host "==> 打标签 $remoteTag" -ForegroundColor Cyan
        & $docker tag $localTag $remoteTag
        if ($LASTEXITCODE -ne 0) { throw "docker tag 失败" }

        Write-Host "==> 推送（若未登录请先执行：docker login --username=<账号> $Registry）" -ForegroundColor Cyan
        & $docker push $remoteTag
        if ($LASTEXITCODE -ne 0) { throw "docker push 失败，请检查登录状态与命名空间权限" }

        Write-Host "==> 推送完成：$remoteTag" -ForegroundColor Green
    }
}
finally {
    Pop-Location
}
