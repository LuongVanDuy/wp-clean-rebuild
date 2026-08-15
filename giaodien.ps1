Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Buoc([string]$Text) {
    Write-Host "`n>> $Text" -ForegroundColor Cyan
}

function ThanhCong([string]$Text) {
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function CanhBao([string]$Text) {
    Write-Host "[!] $Text" -ForegroundColor Yellow
}

function Loi([string]$Text) {
    Write-Host "[X] $Text" -ForegroundColor Red
}

function Hoi-CoKhong([string]$Text) {
    $answer = Read-Host "$Text [Y/n]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $true }
    return $answer.Trim().ToLowerInvariant() -in @('y', 'yes', 'c', 'co')
}

function KiemTra-LenhNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $FilePath @Arguments *> $null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $oldPreference
    }
}

function Tim-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path $candidate) { return $candidate }
    return $null
}

function Ghi-StartupLog([string]$Text) {
    try {
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -Path $script:startupLog -Value "[$stamp] $Text" -Encoding UTF8
    }
    catch {
        # Logging must never block the launcher.
    }
}

function Chay-GuiEntry {
    param(
        [Parameter(Mandatory = $true)][string]$Module,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Buoc $Label
    Ghi-StartupLog "START $Module"
    $oldPreference = $ErrorActionPreference
    try {
        # Keep native stderr visible without turning it into a terminating
        # Windows PowerShell 5 error while ErrorActionPreference is Stop.
        $ErrorActionPreference = 'SilentlyContinue'
        & $script:uvExe run python -m $Module
        $code = $LASTEXITCODE
    }
    catch {
        $code = 1
        Loi "$Label failed: $($_.Exception.Message)"
        Ghi-StartupLog "EXCEPTION $Module :: $($_.Exception.ToString())"
    }
    finally {
        $ErrorActionPreference = $oldPreference
    }

    Ghi-StartupLog "EXIT $Module :: code=$code"
    return [int]$code
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$logDir = Join-Path $scriptDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$script:startupLog = Join-Path $logDir 'gui-startup.log'

Write-Host ''
Write-Host 'WP CLEAN REBUILD - LOCAL GUI' -ForegroundColor White
Write-Host '============================' -ForegroundColor DarkGray

Buoc 'STEP 1 - Check environment'
$uvExe = Tim-Uv
if (-not $uvExe) {
    CanhBao 'uv is not installed on this computer.'
    if (-not (Hoi-CoKhong 'Install uv automatically?')) {
        Loi 'Cannot continue without uv.'
        Read-Host 'Press Enter to close'
        exit 2
    }
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    catch {
        Loi "uv install failed: $($_.Exception.Message)"
        Ghi-StartupLog "uv install failed :: $($_.Exception.ToString())"
        Read-Host 'Press Enter to close'
        exit 2
    }
    $uvExe = Tim-Uv
    if (-not $uvExe) {
        Loi 'uv installer finished but uv.exe was not found.'
        Read-Host 'Press Enter to close'
        exit 2
    }
}
$script:uvExe = $uvExe
ThanhCong "uv: $uvExe"

$pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    CanhBao 'Python 3.13 managed by uv is not installed.'
    if (-not (Hoi-CoKhong 'Install Python 3.13 automatically?')) {
        Loi 'Cannot continue without Python 3.13.'
        Read-Host 'Press Enter to close'
        exit 2
    }
    Buoc 'Installing Python 3.13'
    & $uvExe python install 3.13
    if ($LASTEXITCODE -ne 0) {
        Loi 'Python 3.13 install failed.'
        Read-Host 'Press Enter to close'
        exit 2
    }
}

$pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    Loi 'Python 3.13 is still unavailable after installation.'
    Read-Host 'Press Enter to close'
    exit 2
}
ThanhCong 'Python 3.13 is ready.'

$doctorReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    CanhBao 'Project environment or Python dependencies are incomplete.'
    if (-not (Hoi-CoKhong 'Install/sync project dependencies automatically?')) {
        Loi 'Cannot continue while project dependencies are incomplete.'
        Read-Host 'Press Enter to close'
        exit 2
    }
    Buoc 'Installing and syncing project dependencies'
    & $uvExe sync
    if ($LASTEXITCODE -ne 0) {
        Loi 'Dependency sync failed.'
        Read-Host 'Press Enter to close'
        exit 2
    }
}

$doctorReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    Loi 'Environment self-check still failed.'
    CanhBao "Startup log: $startupLog"
    Read-Host 'Press Enter to close'
    exit 2
}
ThanhCong 'Runtime environment is ready.'

Buoc 'STEP 2 - Start local GUI'
Write-Host 'The browser will open automatically. Keep this window open while using the GUI.' -ForegroundColor Cyan

# Production entry: parallel project support + journal + FTP diagnostics.
$guiExit = Chay-GuiEntry -Module 'wpclean.gui_parallel_entry' -Label 'Starting multi-project GUI'
if ($guiExit -eq 0) {
    exit 0
}

Loi "Multi-project GUI stopped with exit code $guiExit."
CanhBao "Startup log: $startupLog"
CanhBao 'Trying the stable fallback GUI so work can continue.'

$stableExit = Chay-GuiEntry -Module 'wpclean.gui_ftp_logging_entry' -Label 'Starting stable fallback GUI'
if ($stableExit -eq 0) {
    exit 0
}

Loi "Fallback GUI also failed to start (exit code $stableExit)."
Loi 'Send logs\gui-startup.log to technical support.'
Read-Host 'Press Enter to close'
exit $stableExit
