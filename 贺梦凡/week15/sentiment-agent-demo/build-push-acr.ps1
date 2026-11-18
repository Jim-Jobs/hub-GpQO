# 构建 OpenAI Agents 镜像并推送到阿里云 ACR
# 用法：
#   $env:ACR_USERNAME = "你的阿里云账号"
#   .\build-push-acr.ps1
# 可选环境变量：ACR_REGISTRY / ACR_NAMESPACE / ACR_REPO / IMAGE_TAG

$ErrorActionPreference = "Stop"

$Registry  = if ($env:ACR_REGISTRY)  { $env:ACR_REGISTRY }  else { "registry.cn-hangzhou.aliyuncs.com" }
$Namespace = if ($env:ACR_NAMESPACE) { $env:ACR_NAMESPACE } else { "ai-lesson" }
$Repo      = if ($env:ACR_REPO)      { $env:ACR_REPO }      else { "sentiment-agents" }
$Tag       = if ($env:IMAGE_TAG)     { $env:IMAGE_TAG }     else { "v1" }
$Local     = "${Repo}:${Tag}"
$Remote    = "${Registry}/${Namespace}/${Repo}:${Tag}"

if (-not $env:ACR_USERNAME) {
    Write-Error "请先设置 `$env:ACR_USERNAME = '你的阿里云账号'"
}

Write-Host "==> docker build $Local"
docker build --platform linux/amd64 -t $Local .

Write-Host "==> docker tag -> $Remote"
docker tag $Local $Remote

Write-Host "==> docker login $Registry"
docker login --username=$env:ACR_USERNAME $Registry

Write-Host "==> docker push $Remote"
docker push $Remote

Write-Host "Done: $Remote"
