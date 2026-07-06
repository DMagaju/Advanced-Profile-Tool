# install_dev.ps1 - Developer install: creates an NTFS junction so QGIS reads
# the source folder directly. Edit code here, reload QGIS plugin, done.
# No administrator privileges required.
#
# Usage:
#   .\install_dev.ps1                           # uses "default" QGIS profile
#   .\install_dev.ps1 -QGISProfile "MyProfile"  # specific profile

param(
    [string]$QGISProfile = "default"
)

$ErrorActionPreference = "Stop"

$PluginSrc   = $PSScriptRoot
$QGISPlugins = Join-Path $env:APPDATA "QGIS\QGIS3\profiles\$QGISProfile\python\plugins"
$PluginDest  = Join-Path $QGISPlugins "AdvancedProfileTool"

Write-Host ""
Write-Host "Advanced Profile Tool - Developer Install (Junction)" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "Source : $PluginSrc"
Write-Host "Target : $PluginDest"
Write-Host ""

if (-not (Test-Path $QGISPlugins)) {
    New-Item -ItemType Directory -Force -Path $QGISPlugins | Out-Null
}

if (Test-Path $PluginDest) {
    Write-Host "Removing existing installation..."
    cmd /c "rmdir `"$PluginDest`"" 2>$null
    if (Test-Path $PluginDest) {
        cmd /c "rmdir /s /q `"$PluginDest`"" 2>$null
    }
    if (Test-Path $PluginDest) {
        Write-Host "Could not remove existing installation. Close QGIS first, then try again." -ForegroundColor Red
        exit 1
    }
}

New-Item -ItemType Junction -Path $PluginDest -Target $PluginSrc | Out-Null

Write-Host "Junction created successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Developer workflow:" -ForegroundColor Cyan
Write-Host "  1. Restart QGIS and enable Advanced Profile Tool"
Write-Host "  2. Edit source files in this folder"
Write-Host "  3. Reload in QGIS: Plugins > Plugin Reloader > AdvancedProfileTool > Reload"
Write-Host "     Or in Python Console: from qgis.utils import reloadPlugin; reloadPlugin('AdvancedProfileTool')"
Write-Host ""
