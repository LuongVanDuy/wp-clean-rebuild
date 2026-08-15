$ErrorActionPreference = 'Stop'

function TieuDe($text) {
    Write-Host "`n============================================================" -ForegroundColor DarkCyan
    Write-Host " $text" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor DarkCyan
}

function Buoc($text) {
    Write-Host "`n==> $text" -ForegroundColor Cyan
}

function ThanhCong($text) {
    Write-Host "[OK] $text" -ForegroundColor Green
}

function CanhBao($text) {
    Write-Host "[CANH BAO] $text" -ForegroundColor Yellow
}

function Loi($text) {
    Write-Host "[LOI] $text" -ForegroundColor Red
}

function Hoi-CoKhong($message, $defaultYes = $true) {
    $suffix = if ($defaultYes) { '[Y/n]' } else { '[y/N]' }
    $answer = Read-Host "$message $suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $defaultYes }
    return $answer.Trim().ToLowerInvariant() -in @('y', 'yes', 'c', 'co', 'có')
}

function Tim-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'uv\uv.exe')
    ) | Select-Object -Unique

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function KiemTra-LenhNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $FilePath @Arguments 1>$null 2>$null
        $exitCode = $LASTEXITCODE
    }
    catch {
        $exitCode = 1
    }
    finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    return ($exitCode -eq 0)
}

TieuDe 'WP CLEAN REBUILD - GIAO DIỆN ĐIỀU KHIỂN'
Write-Host 'Hệ thống sẽ kiểm tra môi trường rồi tự mở giao diện trong trình duyệt.'
Write-Host 'Không cần sử dụng terminal để vận hành dự án sau khi giao diện đã mở.'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Buoc 'BƯỚC 1 - Kiểm tra môi trường chạy'
$uvExe = Tim-Uv
if (-not $uvExe) {
    CanhBao 'Chưa có uv - công cụ quản lý Python/runtime của dự án.'
    if (-not (Hoi-CoKhong 'Bạn có muốn cài uv tự động từ nguồn chính thức không?')) {
        Loi 'Không thể tiếp tục khi chưa có uv.'
        exit 2
    }

    Buoc 'Đang tải bộ cài uv chính thức'
    $installer = Join-Path $env:TEMP 'wpclean-uv-install.ps1'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) {
        Loi 'Cài uv thất bại.'
        exit 2
    }
    $uvExe = Tim-Uv
    if (-not $uvExe) {
        Loi 'uv đã cài nhưng hệ thống chưa tìm thấy uv.exe.'
        exit 2
    }
}
ThanhCong "Đã có uv: $(& $uvExe --version)"

$pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
if (-not $pythonReady) {
    CanhBao 'Máy này chưa có Python 3.13 phù hợp cho WP Clean Rebuild.'
    if (-not (Hoi-CoKhong 'Bạn có muốn tải và cài Python 3.13 tự động bằng uv không?')) {
        Loi 'Không thể tiếp tục khi chưa có Python 3.13.'
        exit 2
    }

    Buoc 'Đang tải và cài Python 3.13 - vui lòng chờ'
    & $uvExe python install 3.13
    if ($LASTEXITCODE -ne 0) {
        Loi 'Cài Python 3.13 thất bại. Hãy kiểm tra kết nối Internet hoặc báo kỹ thuật.'
        exit 2
    }

    $pythonReady = KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')
    if (-not $pythonReady) {
        Loi 'uv báo đã cài nhưng vẫn không tìm thấy Python 3.13. Vui lòng báo kỹ thuật.'
        exit 2
    }
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
& $uvExe run python -m wpclean.gui_no_fresh_entry
exit $LASTEXITCODE
