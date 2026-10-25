param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path $PSScriptRoot -Parent
$Vendor = Join-Path $Root 'vendor'
$Target = Join-Path $Vendor 'sing-box-1.12.9.exe'

$Url = 'https://github.com/SagerNet/sing-box/releases/download/v1.12.9/sing-box-1.12.9-windows-amd64.zip'
$ExpectedZipHash = 'F9F9B55D394FE08A8AFE1FD3BB1C037E687A12FBE701F16481BE6941D1766840'
$ExpectedExeHash = '5C94A0A25B1B61B2F50C00815E72D6F3E5E5D234E6ADF7D8A02526B5B173BC79'

if ((Test-Path $Target) -and -not $Force) {
    $ExistingHash = (Get-FileHash $Target -Algorithm SHA256).Hash
    if ($ExistingHash -eq $ExpectedExeHash) {
        Write-Host "sing-box 1.12.9 already present: $Target"
        return
    }
    throw "Existing sing-box binary has an unexpected SHA-256: $ExistingHash. Re-run with -Force to replace it."
}

New-Item -ItemType Directory -Force -Path $Vendor | Out-Null

$Temp = Join-Path $env:TEMP ('smartroute-singbox-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

try {
    $Zip = Join-Path $Temp 'sing-box.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host "Downloading $Url"
    $Curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($Curl) {
        & $Curl.Source -L --fail --silent --show-error --retry 3 --retry-delay 2 --connect-timeout 20 -o $Zip $Url
        if ($LASTEXITCODE -ne 0) {
            throw "curl failed with exit code $LASTEXITCODE"
        }
    }
    else {
        Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
    }

    $ActualZipHash = (Get-FileHash $Zip -Algorithm SHA256).Hash
    if ($ActualZipHash -ne $ExpectedZipHash) {
        throw "Archive SHA-256 mismatch. Expected $ExpectedZipHash, got $ActualZipHash"
    }

    Expand-Archive -Path $Zip -DestinationPath $Temp -Force
    $Exe = Get-ChildItem -Path $Temp -Recurse -Filter 'sing-box.exe' -File | Select-Object -First 1
    if (-not $Exe) {
        throw 'sing-box.exe was not found in the downloaded archive.'
    }

    $ActualExeHash = (Get-FileHash $Exe.FullName -Algorithm SHA256).Hash
    if ($ActualExeHash -ne $ExpectedExeHash) {
        throw "Binary SHA-256 mismatch. Expected $ExpectedExeHash, got $ActualExeHash"
    }

    Copy-Item $Exe.FullName $Target -Force
    Write-Host "Installed $Target"
}
finally {
    Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue
}
