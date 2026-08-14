$ErrorActionPreference = 'Stop'

function Write-Step($message) {
    Write-Host "`n==> $message" -ForegroundColor Cyan
}

function Fail($message) {
    Write-Host "`nERROR: $message" -ForegroundColor Red
    exit 1
}

Write-Host "WP Clean Rebuild - Windows Bootstrap" -ForegroundColor Green
Write-Host "This setup does NOT require Python to be preinstalled."

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "`nMissing runtime manager: uv" -ForegroundColor Yellow
    Write-Host "Official source: https://astral.sh/uv/install.ps1"
    $answer = Read-Host "Download and install uv now? [Y/n]"
    if ($answer -and $answer.ToLower() -notin @('y','yes')) {
        Fail "Installation cancelled."
    }

    Write-Step "Downloading official uv installer"
    $installer = Join-Path $env:TEMP "wpclean-uv-install.ps1"
    Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/install.ps1" -OutFile $installer

    Write-Step "Installing uv"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) {
        Fail "uv installation failed."
    }

    $candidatePaths = @(
        (Join-Path $HOME ".local\bin"),
        (Join-Path $env:USERPROFILE ".local\bin")
    ) | Select-Object -Unique

    foreach ($candidate in $candidatePaths) {
        if (Test-Path (Join-Path $candidate "uv.exe")) {
            $env:Path = "$candidate;$env:Path"
            break
        }
    }

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Fail "uv was installed but is not available in the current shell. Close this window, open PowerShell again, and run START.bat."
    }
}

Write-Step "Checking uv"
& uv --version
if ($LASTEXITCODE -ne 0) { Fail "uv is not working." }

Write-Step "Installing managed Python 3.13"
& uv python install 3.13
if ($LASTEXITCODE -ne 0) { Fail "Python runtime installation failed." }

Write-Step "Creating project environment and installing dependencies"
& uv sync
if ($LASTEXITCODE -ne 0) { Fail "Project dependency installation failed." }

Write-Step "Running self-check"
& uv run wpclean doctor
if ($LASTEXITCODE -ne 0) { Fail "wpclean self-check failed." }

Write-Host "`nREADY" -ForegroundColor Green
Write-Host "Use: .\wpclean.bat doctor"
Write-Host "Or:  .\wpclean.bat --help"
