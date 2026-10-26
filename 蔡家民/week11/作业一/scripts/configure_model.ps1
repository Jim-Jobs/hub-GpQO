param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Path $PSScriptRoot -Parent
Write-Host '仅配置当前终端环境并检查配置，不发送模型请求。'
$endpoint = Read-Host '输入模型服务 Base URL（含版本路径，不含 /chat/completions）'
$modelName = Read-Host '输入模型名称'
$secureKey = Read-Host '输入 API Key（隐藏输入）' -AsSecureString
if ([string]::IsNullOrWhiteSpace($endpoint) -or [string]::IsNullOrWhiteSpace($modelName) -or $secureKey.Length -eq 0) {
    throw '模型服务地址、模型名称和 API Key 均不能为空。'
}
$env:SQL_AGENT_BASE_URL = $endpoint.Trim().TrimEnd('/')
$env:SQL_AGENT_MODEL = $modelName.Trim()
$env:SQL_AGENT_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
Push-Location -LiteralPath $projectRoot
try {
    & $Python -m sql_agent preflight
    if ($LASTEXITCODE -ne 0) { throw '配置检查未通过，请根据缺失字段修正。' }
    Write-Host '配置通过。密钥仅保留在当前终端进程环境，没有写入文件。'
    Write-Host '下一步可运行：python scripts/run_experiments.py --mode live --split dev --limit 3 --execute'
} finally {
    Pop-Location
}
