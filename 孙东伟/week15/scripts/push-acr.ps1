#!/usr/bin/env pwsh
# 把镜像推到阿里云容器镜像服务（ACR）。
# 用法示例：
#   ./scripts/push-acr.ps1 -Namespace week15 -Repo sentiment-companion -Tag v1
# 需要先 docker login registry.cn-hangzhou.aliyuncs.com

param(
    [string]$Registry = "registry.cn-hangzhou.aliyuncs.com",
    [Parameter(Mandatory = $true)][string]$Namespace,
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$Tag = "v1",
    [string]$LocalImage = "sentiment-companion:agents"
)

$ErrorActionPreference = "Stop"
$Remote = "$Registry/$Namespace/$Repo`:$Tag"

Write-Host "==> 1/3 构建 $LocalImage" -ForegroundColor Cyan
docker build -t $LocalImage .

Write-Host "==> 2/3 打标签 $Remote" -ForegroundColor Cyan
docker tag $LocalImage $Remote

Write-Host "==> 3/3 推送（需要先 docker login）" -ForegroundColor Cyan
docker push $Remote

Write-Host "完成：$Remote" -ForegroundColor Green
Write-Host "拉取运行：docker run -d -p 8000:8000 --env-file .env $Remote"
