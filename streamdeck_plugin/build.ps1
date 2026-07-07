# Builds the Elgato Stream Deck plugin: compiles the launcher, generates
# icons + property inspectors, and assembles com.thebusaibridge.sdPlugin\.
# Install afterwards with streamdeck_plugin\install.ps1.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$src  = $PSScriptRoot
$out  = Join-Path $src "com.thebusaibridge.sdPlugin"
$build = Join-Path $root "build"
New-Item -ItemType Directory -Force $out, (Join-Path $out "pi"), (Join-Path $out "images"), $build | Out-Null

# -- launcher.exe (MSVC) ------------------------------------------------------
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found - install VS Build Tools" }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "MSVC C++ x64 toolset not found" }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"

$bat = @"
@echo off
call "$vcvars" >nul
if errorlevel 1 exit /b 1
cl /nologo /O2 /W4 /MT /EHs-c- /GR- /std:c++17 "$src\launcher.cpp" /Fo"$build\\" /Fe"$out\thebuslauncher.exe" /link /SUBSYSTEM:WINDOWS user32.lib
if errorlevel 1 exit /b 1
echo BUILD_OK
"@
$batPath = Join-Path $build "compile_launcher.bat"
Set-Content -Path $batPath -Value $bat -Encoding ascii
& cmd /c $batPath
if ($LASTEXITCODE -ne 0) { throw "launcher build failed (exit $LASTEXITCODE)" }

# -- assets + manifest ---------------------------------------------------------
python (Join-Path $root "tools\gen_deck_icons.py")
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }
python (Join-Path $root "tools\gen_feature_pi.py")
if ($LASTEXITCODE -ne 0) { throw "feature PI generation failed" }
python (Join-Path $root "tools\gen_button_pi.py")
if ($LASTEXITCODE -ne 0) { throw "button PI generation failed" }
Copy-Item (Join-Path $src "manifest.json") $out -Force
Copy-Item (Join-Path $src "pi\*.html") (Join-Path $out "pi") -Force
Copy-Item (Join-Path $src "images\*.png") (Join-Path $out "images") -Force

# pin the exact interpreter for the launcher (Stream Deck app's PATH may differ)
$py = (Get-Command python.exe).Source
Set-Content -Path (Join-Path $out "launcher.cfg") -Value $py -Encoding unicode

Get-ChildItem $out -Recurse -File | Measure-Object | ForEach-Object {
    Write-Host "plugin assembled: $out ($($_.Count) files, python: $py)"
}
