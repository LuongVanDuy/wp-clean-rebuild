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

TieuDe 'WP CLEAN REBUILD - TRINH HUONG DAN TU DONG'
Write-Host 'Nhan su chi can lam theo cac cau hoi tren man hinh.'
Write-Host 'He thong se tu kiem tra moi truong, du an va tiep tuc dung buoc dang do.'

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

& $uvExe python find 3.13 *> $null
if ($LASTEXITCODE -ne 0) {
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
ThanhCong 'Python 3.13 đã sẵn sàng.'

& $uvExe run --no-sync wpclean doctor *> $null
if ($LASTEXITCODE -ne 0) {
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

& $uvExe run --no-sync wpclean doctor *> $null
if ($LASTEXITCODE -ne 0) {
    Loi 'Tự kiểm tra môi trường vẫn thất bại. Vui lòng liên hệ kỹ thuật.'
    exit 2
}
ThanhCong 'Môi trường chạy đã đầy đủ.'

Buoc 'BƯỚC 2 - Mở trình điều khiển dự án'
& $uvExe run python -m wpclean.operator_entry
exit $LASTEXITCODE
