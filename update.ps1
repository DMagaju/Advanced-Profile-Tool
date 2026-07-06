# update.ps1 - Update the plugin from GitHub.
# Run from this folder (wherever the plugin is installed).
#
# Usage:
#   .\update.ps1          # git pull
#   .\update.ps1 -Init    # convert a ZIP install to git, then pull

param([switch]$Init)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/DMagaju/Advanced-Profile-Tool.git"

Write-Host ""
Write-Host "Advanced Profile Tool - Updater" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $PSScriptRoot

if ($Init) {
    if (Test-Path (Join-Path $PSScriptRoot ".git")) {
        Write-Host "Already a git repository. Running git pull instead..." -ForegroundColor Yellow
        git pull origin master
    } else {
        Write-Host "Initialising git in this folder..." -ForegroundColor Cyan
        git init
        git remote add origin $RepoUrl
        Write-Host "Fetching latest code from GitHub..." -ForegroundColor Cyan
        git fetch origin master --depth=1
        git checkout -B master origin/master
        git branch --set-upstream-to=origin/master master
        Write-Host ""
        Write-Host "Converted to git successfully!" -ForegroundColor Green
        Write-Host "Future updates: just run  .\update.ps1" -ForegroundColor Cyan
    }
} else {
    if (-not (Test-Path (Join-Path $PSScriptRoot ".git"))) {
        Write-Host "This folder has no git history." -ForegroundColor Yellow
        Write-Host "To enable git pull updates, run:" -ForegroundColor Yellow
        Write-Host "  .\update.ps1 -Init" -ForegroundColor White
        Write-Host ""
        exit 0
    }
    git pull origin master
}

Write-Host ""
Write-Host "Update complete!" -ForegroundColor Green
Write-Host ""
Write-Host "To apply in QGIS:" -ForegroundColor Cyan
Write-Host "  Restart QGIS  or  use Plugin Reloader: AdvancedProfileTool - Reload"
Write-Host ""
