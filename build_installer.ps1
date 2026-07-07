$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$appExe = Join-Path $root "dist\PMDG Livery Installer MSFS2024.exe"
$issPath = Join-Path $root "installer.iss"

if (-not (Test-Path -LiteralPath $issPath)) {
  throw "Inno Setup script not found: $issPath"
}

powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
if ($LASTEXITCODE -ne 0) {
  throw "Application executable build failed."
}

if (-not (Test-Path -LiteralPath $appExe)) {
  throw "Application executable was not built: $appExe"
}

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
  $candidatePaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "D:\Software\Inno Setup 7\ISCC.exe",
    "D:\Software\Inno Setup 6\ISCC.exe"
  )
  foreach ($candidate in $candidatePaths) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
      $iscc = Get-Item -LiteralPath $candidate
      break
    }
  }
}

if (-not $iscc) {
  throw "ISCC.exe was not found. Install Inno Setup 6 or 7, or add ISCC.exe to PATH."
}

$isccPath = if ($iscc -is [System.IO.FileInfo]) { $iscc.FullName } else { $iscc.Source }
& $isccPath $issPath

$setupExe = Join-Path $root "release\PMDG Livery Installer MSFS2024 Setup v0.1.5.exe"
if (-not (Test-Path -LiteralPath $setupExe)) {
  throw "Inno Setup did not produce the expected installer: $setupExe"
}

Write-Host ""
Write-Host "Built application: $appExe"
Write-Host "Built installer:   $setupExe"
