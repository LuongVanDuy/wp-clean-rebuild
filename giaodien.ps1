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
    return $answer.Trim().ToLowerInvariant() -in @('y', 'yes', 'c', 'co', 'có')
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
        # Native stderr must remain visible, but must not become a PowerShell
        # terminating error under Windows PowerShell 5 + ErrorActionPreference=Stop.
        $ErrorActionPreference = 'SilentlyContinue'
        & $script:uvExe run python -m $Module
        $code = $LASTEXITCODE
    }
    catch {
        $code = 1
        Loi "$Label thất bại: $($_.Exception.Message)"
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
Write-Host 'WP CLEAN REBUILD - GIAO DIEN LOCAL' -ForegroundColor White
Write-Host '===================================' -ForegroundColor DarkGray

Buoc 'BƯỚC 1 - Kiểm tra môi trường'
$uvExe = Tim-Uv
if (-not $uvExe) {
    CanhBao 'Chưa có uv trên máy này.'
    if (-not (Hoi-CoKhong 'Bạn có muốn cài uv tự động không?')) {
        Loi 'Không thể tiếp tục khi chưa có uv.'
        Read-Host 'Nhấn Enter để đóng cửa sổ'
        exit 2
    }
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    catch {
        Loi "Cài uv thất bại: $($_.Exception.Message)"
        Ghi-StartupLog "Cài uv thất bại :: $($_.Exception.ToString())"
        Read-Host 'Nhấn Enter để đóng cửa sổ'
        exit 2
    }
    $uvExe = Tim-Uv
    if (-not $uvExe) {
        Loi 'Đã chạy trình cài uv nhưng vẫn chưa tìm thấy uv.exe.'
        Read-Host 'Nhấn Enter để đóng cửa sổ'
        exit 2
    }
}
$script:uvExe = $uvExe
ThanhCong "uv: $uvExe"

$pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    CanhBao 'Chưa có Python 3.13 do uv quản lý.'
    if (-not (Hoi-CoKhong 'Bạn có muốn cài Python 3.13 tự động không?')) {
        Loi 'Không thể tiếp tục khi chưa có Python 3.13.'
        Read-Host 'Nhấn Enter để đóng cửa sổ'
        exit 2
    }
    Buoc 'Đang cài Python 3.13'
    & $uvExe python install 3.13
    if ($LASTEXITCODE -ne 0) {
        Loi 'Cài Python 3.13 thất bại.'
        Read-Host 'Nhấn Enter để đóng cửa sổ'
        exit 2
    }
}

$pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    Loi 'Python 3.13 vẫn chưa sẵn sàng sau khi cài.'
    Read-Host 'Nhấn Enter để đóng cửa sổ'
    exit 2
}
ThanhCong 'Python 3.13 đã sẵn sàng.'

$doctorReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    CanhBao 'Môi trường dự án hoặc thư viện Python chưa đầy đủ.'
    if (-not (Hoi-CoKhong 'Bạn có muốn cài/đồng bộ thư viện dự án tự động không?')) {
        Loi 'Không thể tiếp tục khi thư viện dự án chưa đầy đủ.'
        Read-Host 'Nhấn Enter để đóng cửa sổ'
        exit 2
    }
    Buoc 'Đang cài và đồng bộ thư viện dự án'
    & $uvExe sync
    if ($LASTEXITCODE -ne 0) {
        Loi 'Đồng bộ thư viện dự án thất bại.'
        Read-Host 'Nhấn Enter để đóng cửa sổ'
        exit 2
    }
}

$doctorReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    Loi 'Tự kiểm tra môi trường vẫn thất bại. Vui lòng liên hệ kỹ thuật.'
    Read-Host 'Nhấn Enter để đóng cửa sổ'
    exit 2
}
ThanhCong 'Môi trường chạy đã đầy đủ.'

Buoc 'BƯỚC 2 - Mở giao diện local'
Write-Host 'Trình duyệt sẽ tự mở. Giữ cửa sổ này chạy trong lúc sử dụng giao diện.' -ForegroundColor Cyan

# Production entry: parallel project support + journal + FTP diagnostics.
$guiExit = Chay-GuiEntry -Module 'wpclean.gui_parallel_entry' -Label 'Khởi động giao diện nhiều dự án'
if ($guiExit -eq 0) {
    exit 0
}

Loi "Giao diện nhiều dự án đã dừng với mã lỗi $guiExit."
CanhBao "Chi tiết được lưu tại: $startupLog"
CanhBao 'Đang tự chuyển sang giao diện stable để bạn vẫn có thể tiếp tục công việc.'

$stableExit = Chay-GuiEntry -Module 'wpclean.gui_ftp_logging_entry' -Label 'Khởi động giao diện stable dự phòng'
if ($stableExit -eq 0) {
    exit 0
}

Loi "Giao diện dự phòng cũng không khởi động được (mã lỗi $stableExit)."
Loi "Hãy gửi file logs\gui-startup.log cho kỹ thuật."
Read-Host 'Nhấn Enter để đóng cửa sổ'
exit $stableExit
