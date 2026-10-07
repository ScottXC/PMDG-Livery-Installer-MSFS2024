$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$env:PYTHONPATH = Join-Path $root ".build_tools"
# Avoid putting the local build time in the executable's PE header.
$env:SOURCE_DATE_EPOCH = '946684800'
python -c "import PIL; import PyInstaller"
if ($LASTEXITCODE -ne 0) {
  throw "Build dependencies missing. Run: python -m pip install --target .build_tools -r requirements-build.txt"
}
python .\build_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }
python .\tools\write_version_info.py
if ($LASTEXITCODE -ne 0) { throw "Version metadata generation failed." }
python .\tools\run_pyinstaller_fixed_temp.py `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "PMDG Livery Installer MSFS2024" `
  --icon ".\assets\pmdg_livery_installer_icon.ico" `
  --version-file ".\build\version_info.txt" `
  --add-data ".\assets;assets" `
  .\pmdg_livery_installer.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed."
}

Write-Host ""
Write-Host "Built: $root\dist\PMDG Livery Installer MSFS2024.exe"
