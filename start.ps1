param(
    [switch]$Qwen,
    [ValidateRange(1024, 65535)][int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$gatewayPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $gatewayPython)) {
    throw '项目虚拟环境不存在。请按 README 用 uv sync --locked 安装依赖。'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $gatewayPython -m gateway bootstrap
    if ($LASTEXITCODE -ne 0) { throw '管理员初始化失败。' }
    $gatewayArgs = @('-m', 'gateway', 'serve', '--port', [string]$Port)
    if ($Qwen) { $gatewayArgs += '--qwen' }
    & $gatewayPython @gatewayArgs
    if ($LASTEXITCODE -ne 0) { throw '本地网关退出，请查看上述错误。' }
} finally {
    Pop-Location
}
