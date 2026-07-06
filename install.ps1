# install.ps1 - Packages the plugin and installs it into QGIS via ZIP extraction.
# Run from the repo root directory. No administrator privileges required.
# Produces AdvancedProfileTool.zip for the office computer as a side effect.
#
# Usage:
#   .\install.ps1                           # uses "default" QGIS profile
#   .\install.ps1 -QGISProfile "MyProfile"  # specific profile

param(
    [string]$QGISProfile = "default"
)

$ErrorActionPreference = "Stop"

$PluginName  = "AdvancedProfileTool"
$ZipOut      = Join-Path $PSScriptRoot "$PluginName.zip"
$QGISPlugins = Join-Path $env:APPDATA "QGIS\QGIS3\profiles\$QGISProfile\python\plugins"
$PluginDest  = Join-Path $QGISPlugins $PluginName

Write-Host ""
Write-Host "Advanced Profile Tool - Plugin Installer" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Target : $PluginDest"
Write-Host ""

# ── Package ───────────────────────────────────────────────────────────────────
Write-Host "Packaging..." -ForegroundColor Cyan

$Temp = Join-Path $env:TEMP $PluginName
if (Test-Path $Temp) { Remove-Item $Temp -Recurse -Force }
New-Item -ItemType Directory -Path $Temp | Out-Null

$Exclude = @('.git', '.gitignore', '__pycache__', 'APT.md',
             'install.ps1', 'install.bat', 'install_dev.ps1', 'install_dev.bat',
             'package_dist.ps1', 'package_dist.bat', '*.zip', 'plugin_backup_working.py')
Get-ChildItem $PSScriptRoot -Exclude $Exclude | Copy-Item -Destination $Temp -Recurse

Get-ChildItem $Temp -Filter '__pycache__' -Recurse -Directory | Remove-Item -Recurse -Force

$Wrapper = Join-Path $env:TEMP "${PluginName}_wrap"
if (Test-Path $Wrapper) { Remove-Item $Wrapper -Recurse -Force }
New-Item -ItemType Directory -Path $Wrapper | Out-Null
Move-Item $Temp (Join-Path $Wrapper $PluginName)

if (Test-Path $ZipOut) { Remove-Item $ZipOut -Force }
Compress-Archive -Path (Join-Path $Wrapper $PluginName) -DestinationPath $ZipOut
Remove-Item $Wrapper -Recurse -Force

Write-Host "Packaged : $ZipOut" -ForegroundColor Gray

# ── Install ───────────────────────────────────────────────────────────────────
Write-Host "Installing to QGIS..." -ForegroundColor Cyan

if (Get-Process -Name "qgis*" -ErrorAction SilentlyContinue) {
    Write-Host ""
    Write-Host "QGIS is currently running. Please close QGIS first, then run install.bat again." -ForegroundColor Red
    Write-Host ""
    exit 1
}

if (-not (Test-Path $QGISPlugins)) {
    New-Item -ItemType Directory -Force -Path $QGISPlugins | Out-Null
}

if (Test-Path $PluginDest) {
    Write-Host "Removing existing installation..."
    Remove-Item $PluginDest -Recurse -Force
}

Expand-Archive -Path $ZipOut -DestinationPath $QGISPlugins -Force

Write-Host ""
Write-Host "Plugin installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Restart QGIS"
Write-Host "  2. Plugins > Manage and Install Plugins"
Write-Host "  3. Search 'Advanced Profile Tool' and enable it"
Write-Host ""
