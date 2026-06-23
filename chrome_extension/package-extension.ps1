$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$extensionDir = Join-Path $here "gem-bidplus-autofill"
$zipPath = Join-Path $here "gem-bidplus-autofill.zip"

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Compress-Archive -LiteralPath (Join-Path $extensionDir "*") -DestinationPath $zipPath -Force
Write-Output "Created: $zipPath"
