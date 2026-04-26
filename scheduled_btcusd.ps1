$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "logs"
$logFile = Join-Path $logDir "scheduler.log"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$timestamp = (Get-Date).ToUniversalTime().ToString("o")
Add-Content -Path $logFile -Value "[$timestamp] starting scheduled BTCUSD paper run"

try {
    Push-Location $root
    $output = & ".\run_btcusd.ps1" 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) {
        Add-Content -Path $logFile -Value ($output | Out-String).TrimEnd()
    }
    $done = (Get-Date).ToUniversalTime().ToString("o")
    Add-Content -Path $logFile -Value "[$done] finished exit_code=$exitCode"
    exit $exitCode
}
catch {
    $failed = (Get-Date).ToUniversalTime().ToString("o")
    Add-Content -Path $logFile -Value "[$failed] failed: $($_.Exception.Message)"
    exit 1
}
finally {
    Pop-Location
}
