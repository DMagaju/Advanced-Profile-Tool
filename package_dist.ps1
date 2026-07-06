# package_dist.ps1 - Package a clean distribution ZIP for external / third-party users.
# Contains only the plugin runtime files. No update scripts, no source dev files.
# Increment metadata.txt version= before running.
#
# Usage:
#   .\package_dist.ps1

$ErrorActionPreference = "Stop"

$PluginName = "AdvancedProfileTool"
$ZipOut     = Join-Path $PSScriptRoot "${PluginName}-dist.zip"

Write-Host ""
Write-Host "Advanced Profile Tool - Distribution Packager" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "Output : $ZipOut"
Write-Host ""

$Temp = Join-Path $env:TEMP "${PluginName}_dist_tmp"
if (Test-Path $Temp) { Remove-Item $Temp -Recurse -Force }
New-Item -ItemType Directory -Path $Temp | Out-Null

# Include only runtime files
$Include = @('__init__.py', 'plugin.py', 'profile_line_tool.py', 'metadata.txt', 'icon.svg', 'icon.png')
foreach ($item in $Include) {
    $src = Join-Path $PSScriptRoot $item
    if (Test-Path $src) {
        Copy-Item $src -Destination $Temp -Recurse
    }
}

# Remove __pycache__ from copied content
Get-ChildItem $Temp -Filter '__pycache__' -Recurse -Directory | Remove-Item -Recurse -Force

# Wrap in plugin-name subfolder (required by QGIS Install from ZIP)
$Wrapper = Join-Path $env:TEMP "${PluginName}_dist_wrap"
if (Test-Path $Wrapper) { Remove-Item $Wrapper -Recurse -Force }
New-Item -ItemType Directory -Path $Wrapper | Out-Null
Move-Item $Temp (Join-Path $Wrapper $PluginName)

if (Test-Path $ZipOut) { Remove-Item $ZipOut -Force }
Compress-Archive -Path (Join-Path $Wrapper $PluginName) -DestinationPath $ZipOut
Remove-Item $Wrapper -Recurse -Force

Write-Host "Distribution ZIP created: $ZipOut" -ForegroundColor Green
Write-Host ""
Write-Host "Distribute this ZIP to users — install via QGIS Plugins > Install from ZIP." -ForegroundColor Cyan
Write-Host ""
