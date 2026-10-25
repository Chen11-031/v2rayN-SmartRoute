$ErrorActionPreference = 'Stop'

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
$PyInstaller = Join-Path $Root '.venv\Scripts\pyinstaller.exe'
if (-not (Test-Path $PyInstaller)) {
    throw "找不到 PyInstaller：$PyInstaller"
}

$VendorCore = Join-Path $Root 'vendor\sing-box-1.12.9.exe'
if (-not (Test-Path $VendorCore)) {
    Write-Host "未找到内置 sing-box，开始下载..."
    & (Join-Path $Root 'tools\fetch_singbox.ps1')
}

Write-Host "清理旧的构建目录..."
Remove-Item -Recurse -Force (Join-Path $Root 'build') -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $Root 'dist') -ErrorAction SilentlyContinue

Write-Host "开始打包..."
& $PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name V2rayN-SmartRoute `
    --icon (Join-Path $Root 'assets\app.ico') `
    --add-data "vendor;vendor" `
    --add-data "assets;assets" `
    --version-file (Join-Path $Root 'tools\version_info.txt') `
    (Join-Path $Root 'app.py')

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败，退出码 $LASTEXITCODE"
}

$Out = Join-Path $Root 'dist\V2rayN-SmartRoute'
Copy-Item (Join-Path $Root 'README.md') (Join-Path $Out 'README.md') -Force
$QuickStart = Get-ChildItem -Path $Root -Filter '*.txt' -File |
    Where-Object { $_.Name -notlike 'requirements*' } |
    Select-Object -First 1
if ($QuickStart) {
    Copy-Item $QuickStart.FullName (Join-Path $Out $QuickStart.Name) -Force
}

Write-Host ""
Write-Host "打包完成：$Out"
Write-Host "可执行文件：$(Join-Path $Out 'V2rayN-SmartRoute.exe')"
