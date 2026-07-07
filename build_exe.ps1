$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$iconPath = Join-Path $root "assets\pmdg_livery_installer_icon.ico"
if (-not (Test-Path -LiteralPath $iconPath)) {
  python .\build_icon.py
  if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed. Install Pillow or provide assets\pmdg_livery_installer_icon.ico."
  }
}

$env:PYTHONPATH = Join-Path $root ".build_tools"
python .\tools\run_pyinstaller_fixed_temp.py `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "PMDG Livery Installer MSFS2024" `
  --icon ".\assets\pmdg_livery_installer_icon.ico" `
  --add-data ".\assets\pmdg_livery_installer_icon.ico;assets" `
  --add-data ".\assets\MSFSLayoutGenerator.exe;assets" `
  .\pmdg_livery_installer.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed."
}

Write-Host ""
Write-Host "Built: $root\dist\PMDG Livery Installer MSFS2024.exe"
