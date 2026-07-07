# Builds the standalone "TheBus Copilot.exe" (GUI + autopilot, no Python
# required to run) into dist\ via PyInstaller, plus the Steam companion
# launcher (dist\TheBusSteamCompanion.exe) that starts the Copilot
# together with the game via Steam Launch Options.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force "$root\build", "$root\dist" | Out-Null

# app icon (green BUS rounded square, all standard sizes)
python -c @"
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, 248, 248], radius=48, fill='#1f6f33')
try:
    f = ImageFont.truetype(r'C:\Windows\Fonts\seguisb.ttf', 84)
except OSError:
    f = ImageFont.load_default()
d.text((128, 128), 'BUS', font=f, fill='#e8e8e8', anchor='mm')
img.save(r'$root\build\app.ico',
         sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print('icon ok')
"@
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

# --paths: the package is pip-installed EDITABLE, whose import finder
# PyInstaller cannot follow - point it at the real source tree instead.
python -m PyInstaller --noconfirm --onefile --windowed `
    --name "TheBus Copilot" `
    --icon "$root\build\app.ico" `
    --distpath "$root\dist" `
    --workpath "$root\build\pyi" `
    --specpath "$root\build" `
    --paths "$root\python" `
    --collect-submodules thebus_ai_bridge `
    --collect-all vgamepad `
    --exclude-module thebus_ai_bridge.mcp_server `
    --exclude-module thebus_ai_bridge.deck_plugin `
    "$root\tools\app_entry.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Get-Item "$root\dist\TheBus Copilot.exe" |
    Select-Object FullName, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}

# -- Steam companion launcher (MSVC) -----------------------------------------
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = $null
if (Test-Path $vswhere) {
    $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
if (-not $vs) { throw "MSVC C++ x64 toolset not found (VS Build Tools)" }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$bat = @"
@echo off
call "$vcvars" >nul
if errorlevel 1 exit /b 1
cl /nologo /O2 /W4 /MT /EHs-c- /GR- /std:c++17 "$root\tools\steam_companion.cpp" /Fo"$root\build\\" /Fe"$root\dist\TheBusSteamCompanion.exe" /link /SUBSYSTEM:WINDOWS user32.lib
if errorlevel 1 exit /b 1
echo COMPANION_OK
"@
$batPath = "$root\build\compile_companion.bat"
Set-Content -Path $batPath -Value $bat -Encoding ascii
& cmd /c $batPath
if ($LASTEXITCODE -ne 0) { throw "companion build failed" }
Get-Item "$root\dist\TheBusSteamCompanion.exe" | Select-Object FullName

Write-Host ""
Write-Host "Steam setup: The Bus -> Properties -> Launch Options:"
Write-Host "  `"$root\dist\TheBusSteamCompanion.exe`" %command%"
