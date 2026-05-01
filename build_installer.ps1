$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$appExe = Join-Path $root "dist\PMDG Livery Installer MSFS2024.exe"
if (-not (Test-Path -LiteralPath $appExe)) {
  powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
}

$releaseDir = Join-Path $root "release"
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

$env:PYTHONPATH = Join-Path $root ".build_tools"
python .\tools\run_pyinstaller_fixed_temp.py `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "PMDG Livery Installer MSFS2024 Setup v0.1.1" `
  --icon ".\assets\pmdg_livery_installer_icon.ico" `
  --add-data ".\dist\PMDG Livery Installer MSFS2024.exe;payload" `
  --add-data ".\assets\pmdg_livery_installer_icon.ico;payload" `
  --distpath ".\release" `
  .\installer.py

Write-Host ""
Write-Host "Built: $releaseDir\PMDG Livery Installer MSFS2024 Setup v0.1.1.exe"
