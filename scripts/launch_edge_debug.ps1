param(
    [string]$Browser = "edge",
    [int]$RemoteDebuggingPort = 9222,
    [string]$Url = "https://ieeexplore.ieee.org/"
)

$ErrorActionPreference = "Stop"

# 浏览器路径
if ($Browser -eq "chrome") {
    $BinaryPaths = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe"
    )
} else {
    $BinaryPaths = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
}

$Binary = $null
foreach ($p in $BinaryPaths) {
    if (Test-Path -LiteralPath $p) {
        $Binary = $p
        break
    }
}

if (-not $Binary) {
    throw "Browser binary not found. Tried: $($BinaryPaths -join ', ')"
}

# 独立用户数据目录 (不污染默认 profile)
$UserDataDir = Join-Path (Join-Path $PSScriptRoot "..") "runtime\debug_browser_profile"
New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null

Start-Process -FilePath $Binary -ArgumentList @(
    "--remote-debugging-port=$RemoteDebuggingPort",
    "--user-data-dir=$UserDataDir",
    "--profile-directory=Default",
    "--new-window",
    $Url
)

Write-Host ""
Write-Host "=========================================="
Write-Host " Browser launched with remote debugging"
Write-Host "=========================================="
Write-Host "  Port:     $RemoteDebuggingPort"
Write-Host "  Profile:  $UserDataDir"
Write-Host "  URL:      $Url"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Sign in to IEEE Xplore (institutional login)"
Write-Host "  2. Pass any bot verification manually"
Write-Host "  3. Run the test:"
Write-Host "     python test/test_pdf_download_standalone.py --only ieee --debug-port $RemoteDebuggingPort"
Write-Host ""
