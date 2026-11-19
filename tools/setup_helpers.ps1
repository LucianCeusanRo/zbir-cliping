# Helper for setup.bat (no winget, no admin). Paths come in as parameters, so spaces/brackets are fine.
param(
  [Parameter(Mandatory=$true)][ValidateSet('download','extract','shortcut')][string]$Action,
  [string]$Url, [string]$Out, [string]$Zip, [string]$Dest, [string]$Names, [string]$Target, [string]$WorkDir, [string]$Icon
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # Invoke-WebRequest is 10x faster without the progress bar
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

function Download($u, $o) {
  $dir = Split-Path -Parent $o
  if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $tmp = "$o.part"
  for ($i = 1; $i -le 3; $i++) {
    try {
      Write-Host "    descarc (incercarea $i): $u"
      Invoke-WebRequest -Uri $u -OutFile $tmp -UseBasicParsing -TimeoutSec 600
      if ((Get-Item -LiteralPath $tmp).Length -gt 100000) { Move-Item -Force -LiteralPath $tmp -Destination $o; return }
    } catch { Write-Host "    Invoke-WebRequest a esuat: $($_.Exception.Message)" }
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $curl) {
      & $curl -L --fail --retry 3 -o $tmp $u
      if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $tmp) -and (Get-Item -LiteralPath $tmp).Length -gt 100000) {
        Move-Item -Force -LiteralPath $tmp -Destination $o; return
      }
    }
    Start-Sleep -Seconds (5 * $i)
  }
  throw "nu pot descarca $u (verifica internetul / antivirusul)"
}

function Extract($z, $d, $names) {
  # unzip to a temp folder, then copy only the wanted exe files (searched anywhere inside the zip) into $d
  $t = Join-Path ([IO.Path]::GetTempPath()) ("zbir_" + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force -Path $t | Out-Null
  try {
    try { Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory($z, $t) }
    catch { Expand-Archive -LiteralPath $z -DestinationPath $t -Force }
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    foreach ($n in $names) {
      $f = Get-ChildItem -LiteralPath $t -Recurse -Filter $n | Select-Object -First 1
      if (-not $f) { throw "nu gasesc $n in arhiva" }
      Copy-Item -Force -LiteralPath $f.FullName -Destination (Join-Path $d $n)
    }
  } finally { Remove-Item -Recurse -Force -LiteralPath $t -ErrorAction SilentlyContinue }
}

try {
  switch ($Action) {
    'download' { Download $Url $Out }
    'extract'  { Extract $Zip $Dest (($Names -split '[,;]') | Where-Object { $_ }) }
    'shortcut' {
      $desk = [Environment]::GetFolderPath('Desktop')
      $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk 'Zbir Cliping.lnk'))
      $s.TargetPath = $Target; $s.WorkingDirectory = $WorkDir; $s.IconLocation = $Icon; $s.Description = 'Zbir Cliping'; $s.Save()
    }
  }
  exit 0
} catch {
  Write-Host "[!] $($_.Exception.Message)"
  exit 1
}
