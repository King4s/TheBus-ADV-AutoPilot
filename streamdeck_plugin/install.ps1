# Installs com.thebusaibridge.sdPlugin into the Elgato Stream Deck app
# (restarts the app if it is running so the plugin loads).
$ErrorActionPreference = "Stop"

$src = Join-Path $PSScriptRoot "com.thebusaibridge.sdPlugin"
if (-not (Test-Path (Join-Path $src "thebuslauncher.exe"))) {
    throw "plugin not built - run streamdeck_plugin\build.ps1 first"
}
$dest = Join-Path $env:APPDATA "Elgato\StreamDeck\Plugins\com.thebusaibridge.sdPlugin"

$app = Get-Process StreamDeck -ErrorAction SilentlyContinue
$appPath = if ($app) { $app.Path } else { $null }
if ($app) {
    Write-Host "stopping Stream Deck app ..."
    $app | Stop-Process -Force
    Start-Sleep -Seconds 2
}

if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Copy-Item -Recurse $src $dest
Write-Host "installed -> $dest"

if ($appPath) {
    Start-Process $appPath
    Write-Host "Stream Deck app restarted - The Bus AI Bridge actions are in the action list."
} else {
    Write-Host "Start the Elgato Stream Deck app to use the plugin."
}
