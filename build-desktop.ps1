$ErrorActionPreference = 'Stop'

Write-Host 'Building the HABIX AI local service...'
python -m PyInstaller --noconfirm --clean --onefile --name habix-backend `
  --distpath backend/dist --workpath backend/build `
  --add-data 'habix/templates;habix/templates' `
  --add-data 'habix/static;habix/static' `
  backend_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

Write-Host 'Installing Electron build dependencies...'
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'npm install failed.' }
$electronCache = Join-Path $PSScriptRoot 'node_modules/.cache/electron'
$env:ELECTRON_CACHE = $electronCache
$env:electron_config_cache = $electronCache
$env:ELECTRON_BUILDER_CACHE = Join-Path $PSScriptRoot 'node_modules/.cache/electron-builder'
node ./node_modules/electron/install.js
$electronExe = Join-Path $PSScriptRoot 'node_modules/electron/dist/electron.exe'
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $electronExe)) {
  Write-Host 'Using the cached Electron archive with PowerShell extraction...'
  $electronVersion = node -p "require('./node_modules/electron/package.json').version"
  $archiveName = "electron-v$electronVersion-win32-x64.zip"
  $archive = Get-ChildItem -LiteralPath $electronCache -Filter $archiveName -Recurse -File | Select-Object -First 1
  if (-not $archive) { throw 'Electron runtime archive was not downloaded.' }
  $checksums = Get-Content (Join-Path $PSScriptRoot 'node_modules/electron/checksums.json') -Raw | ConvertFrom-Json
  $expectedHash = $checksums.PSObject.Properties[$archiveName].Value
  $actualHash = (Get-FileHash -LiteralPath $archive.FullName -Algorithm SHA256).Hash.ToLower()
  if ($actualHash -ne $expectedHash) { throw 'Electron runtime archive checksum did not match.' }
  $electronDist = Join-Path $PSScriptRoot 'node_modules/electron/dist'
  New-Item -ItemType Directory -Path $electronDist -Force | Out-Null
  Expand-Archive -LiteralPath $archive.FullName -DestinationPath $electronDist -Force
  Set-Content -LiteralPath (Join-Path $PSScriptRoot 'node_modules/electron/path.txt') -Value 'electron.exe' -NoNewline
  if (-not (Test-Path $electronExe)) { throw 'Electron runtime extraction did not produce electron.exe.' }
}

Write-Host 'Packaging the Windows desktop app...'
npm run build:windows
if ($LASTEXITCODE -ne 0) { throw 'Electron packaging failed.' }

Write-Host 'Finished. Keep the full win-unpacked folder; the app is desktop-dist/win-unpacked/HABIX AI.exe.'
