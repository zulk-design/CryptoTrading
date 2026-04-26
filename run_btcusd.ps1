$ErrorActionPreference = "Stop"

$bundledPython = "C:\Users\Zulk\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$script = Join-Path $PSScriptRoot "btcusd_chandelier_bot.py"

if (Test-Path $bundledPython) {
    & $bundledPython $script --once @args
} else {
    & python $script --once @args
}

exit $LASTEXITCODE
