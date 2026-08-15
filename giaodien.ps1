Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Step([string]$Text) {
    Write-Host "`n>> $Text" -ForegroundColor Cyan
}

function Ok([string]$Text) {
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function Warn([string]$Text) {
    Write-Host "[!] $Text" -ForegroundColor Yellow
}

function Fail([string]$Text) {
    Write-Host "[X] $Text" -ForegroundColor Red
}

function Ask-YesNo([string]$Text) {
    $answer = Read-Host "$Text [Y/n]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $true }
    return $answer.Trim().ToLowerInvariant() -in @('y', 'yes', 'c', 'co')
}

function Test-NativeCommand {
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

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path $candidate) { return $candidate }
    return $null
}

function Write-StartupLog([string]$Text) {
    try {
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -Path $script:startupLog -Value "[$stamp] $Text" -Encoding UTF8
    }
    catch {
        # Startup logging must never block the launcher.
    }
}

function Run-GuiEntry {
    param(
        [Parameter(Mandatory = $true)][string]$Module,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Step $Label
    Write-StartupLog "START $Module"
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $script:uvExe run python -m $Module
        $code = $LASTEXITCODE
    }
    catch {
        $code = 1
        Fail "$Label failed: $($_.Exception.Message)"
        Write-StartupLog "EXCEPTION $Module :: $($_.Exception.ToString())"
    }
    finally {
        $ErrorActionPreference = $oldPreference
    }

    Write-StartupLog "EXIT $Module :: code=$code"
    return [int]$code
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$logDir = Join-Path $scriptDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$script:startupLog = Join-Path $logDir 'gui-startup.log'

# Force Python to use UTF-8 even when Windows PowerShell 5 / legacy console
# reports cp1252 or another narrow code page.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8:replace'
try {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $utf8
    [Console]::InputEncoding = $utf8
}
catch {
    Write-StartupLog "Console UTF-8 setup warning :: $($_.Exception.Message)"
}

Write-Host ''
Write-Host 'WP CLEAN REBUILD - LOCAL GUI' -ForegroundColor White
Write-Host '============================' -ForegroundColor DarkGray

Step 'STEP 1 - Check environment'
$uvExe = Find-Uv
if (-not $uvExe) {
    Warn 'uv is not installed on this computer.'
    if (-not (Ask-YesNo 'Install uv automatically?')) {
        Fail 'Cannot continue without uv.'
        Read-Host 'Press Enter to close'
        exit 2
    }
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    catch {
        Fail "uv install failed: $($_.Exception.Message)"
        Write-StartupLog "uv install failed :: $($_.Exception.ToString())"
        Read-Host 'Press Enter to close'
        exit 2
    }
    $uvExe = Find-Uv
    if (-not $uvExe) {
        Fail 'uv installer finished but uv.exe was not found.'
        Read-Host 'Press Enter to close'
        exit 2
    }
}
$script:uvExe = $uvExe
Ok "uv: $uvExe"

$pythonReady = Test-NativeCommand -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    Warn 'Python 3.13 managed by uv is not installed.'
    if (-not (Ask-YesNo 'Install Python 3.13 automatically?')) {
        Fail 'Cannot continue without Python 3.13.'
        Read-Host 'Press Enter to close'
        exit 2
    }
    Step 'Installing Python 3.13'
    & $uvExe python install 3.13
    if ($LASTEXITCODE -ne 0) {
        Fail 'Python 3.13 install failed.'
        Read-Host 'Press Enter to close'
        exit 2
    }
}

$pythonReady = Test-NativeCommand -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    Fail 'Python 3.13 is still unavailable after installation.'
    Read-Host 'Press Enter to close'
    exit 2
}
Ok 'Python 3.13 is ready.'

$doctorReady = Test-NativeCommand -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    Warn 'Project environment or Python dependencies are incomplete.'
    if (-not (Ask-YesNo 'Install/sync project dependencies automatically?')) {
        Fail 'Cannot continue while project dependencies are incomplete.'
        Read-Host 'Press Enter to close'
        exit 2
    }
    Step 'Installing and syncing project dependencies'
    & $uvExe sync
    if ($LASTEXITCODE -ne 0) {
        Fail 'Dependency sync failed.'
        Read-Host 'Press Enter to close'
        exit 2
    }
}

$doctorReady = Test-NativeCommand -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    Fail 'Environment self-check still failed.'
    Warn "Startup log: $startupLog"
    Read-Host 'Press Enter to close'
    exit 2
}
Ok 'Runtime environment is ready.'

Step 'STEP 2 - Start local GUI'
Write-Host 'The browser will open automatically. Keep this window open while using the GUI.' -ForegroundColor Cyan

# Both entries go through a UTF-8 runtime guard before importing the GUI stack.
$guiExit = Run-GuiEntry -Module 'wpclean.gui_runtime_entry' -Label 'Starting multi-project GUI'
if ($guiExit -eq 0) {
    exit 0
}

Fail "Multi-project GUI stopped with exit code $guiExit."
Warn "Startup log: $startupLog"
Warn 'Trying the stable fallback GUI so work can continue.'

$stableExit = Run-GuiEntry -Module 'wpclean.gui_stable_runtime_entry' -Label 'Starting stable fallback GUI'
if ($stableExit -eq 0) {
    exit 0
}

Fail "Fallback GUI also failed to start (exit code $stableExit)."
Fail 'Send logs\gui-startup.log and logs\gui-startup-python.log to technical support.'
Read-Host 'Press Enter to close'
exit $stableExit
