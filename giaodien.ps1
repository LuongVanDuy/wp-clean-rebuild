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

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host ''
Write-Host 'WP CLEAN REBUILD - GIAO DIEN LOCAL' -ForegroundColor White
Write-Host '===================================' -ForegroundColor DarkGray

Buoc 'BƯỚC 1 - Kiểm tra môi trường'
$uvExe = Tim-Uv
if (-not $uvExe) {
    CanhBao 'Chưa có uv trên máy này.'
    if (-not (Hoi-CoKhong 'Bạn có muốn cài uv tự động không?')) {
        Loi 'Không thể tiếp tục khi chưa có uv.'
        exit 2
    }
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    catch {
        Loi "Cài uv thất bại: $($_.Exception.Message)"
        exit 2
    }
    $uvExe = Tim-Uv
    if (-not $uvExe) {
        Loi 'Đã chạy trình cài uv nhưng vẫn chưa tìm thấy uv.exe.'
        exit 2
    }
}
ThanhCong "uv: $uvExe"

$pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    CanhBao 'Chưa có Python 3.13 do uv quản lý.'
    if (-not (Hoi-CoKhong 'Bạn có muốn cài Python 3.13 tự động không?')) {
        Loi 'Không thể tiếp tục khi chưa có Python 3.13.'
        exit 2
    }
    Buoc 'Đang cài Python 3.13'
    & $uvExe python install 3.13
    if ($LASTEXITCODE -ne 0) {
        Loi 'Cài Python 3.13 thất bại.'
        exit 2
    }
}

$pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    Loi 'Python 3.13 vẫn chưa sẵn sàng sau khi cài.'
    exit 2
}
ThanhCong 'Python 3.13 đã sẵn sàng.'

$doctorReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    CanhBao 'Môi trường dự án hoặc thư viện Python chưa đầy đủ.'
    if (-not (Hoi-CoKhong 'Bạn có muốn cài/đồng bộ thư viện dự án tự động không?')) {
        Loi 'Không thể tiếp tục khi thư viện dự án chưa đầy đủ.'
        exit 2
    }
    Buoc 'Đang cài và đồng bộ thư viện dự án'
    & $uvExe sync
    if ($LASTEXITCODE -ne 0) {
        Loi 'Đồng bộ thư viện dự án thất bại.'
        exit 2
    }
}

$doctorReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')
if (-not $doctorReady) {
    Loi 'Tự kiểm tra môi trường vẫn thất bại. Vui lòng liên hệ kỹ thuật.'
    exit 2
}
ThanhCong 'Môi trường chạy đã đầy đủ.'

Buoc 'BƯỚC 2 - Mở giao diện local'
Write-Host 'Trình duyệt sẽ tự mở. Giữ cửa sổ này chạy trong lúc sử dụng giao diện.' -ForegroundColor Cyan
# gui_parallel_entry giữ Clean/Rebuild + journal + FTP diagnostics và cho phép nhiều website khác nhau chạy đồng thời.
& $uvExe run python -m wpclean.gui_parallel_entry
exit $LASTEXITCODE
