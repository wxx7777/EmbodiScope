param(
  [int]$Port = 8876,
  [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = (Get-Command python -ErrorAction Stop).Source
$BaseUrl = "http://127.0.0.1:$Port"
$HealthUrl = "$BaseUrl/api/health"
$Stdout = Join-Path $Root "output/server-assessment.stdout.log"
$Stderr = Join-Path $Root "output/server-assessment.stderr.log"

function Test-EmbodiScopeHealth {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 3
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

if (-not (Test-EmbodiScopeHealth)) {
  $env:EMBODISCOPE_HOST = "127.0.0.1"
  $env:EMBODISCOPE_PORT = "$Port"
  $process = Start-Process `
    -FilePath $Python `
    -ArgumentList "run.py" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -WindowStyle Hidden `
    -PassThru

  $ready = $false
  for ($attempt = 0; $attempt -lt 40; $attempt += 1) {
    Start-Sleep -Milliseconds 500
    if (Test-EmbodiScopeHealth) {
      $ready = $true
      break
    }
    if ($process.HasExited) {
      break
    }
  }
  if (-not $ready) {
    Write-Host "EmbodiScope failed to start. Last stderr lines:" -ForegroundColor Red
    if (Test-Path $Stderr) {
      Get-Content $Stderr -Tail 40
    }
    exit 1
  }
  Write-Host "Started EmbodiScope process $($process.Id)." -ForegroundColor Green
} else {
  Write-Host "Reusing the healthy EmbodiScope server on port $Port." -ForegroundColor Green
}

$preflight = @("scripts/assessment_preflight.py", "--url", $BaseUrl)
if ($RunTests) {
  $preflight += "--run-tests"
}
& $Python @preflight
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

Write-Host "Assessment workspace ready: $BaseUrl" -ForegroundColor Cyan
