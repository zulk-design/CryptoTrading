param(
    [switch]$RunWhetherLoggedOnOrNot
)

$ErrorActionPreference = "Stop"

$taskName = "BTCUSD Paper Chandelier"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "scheduled_btcusd.ps1"

$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \`"$script\`""

$createArgs = @(
    "/Create",
    "/TN", $taskName,
    "/SC", "MINUTE",
    "/MO", "5",
    "/TR", $taskCommand,
    "/F"
)

if ($RunWhetherLoggedOnOrNot) {
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $credential = Get-Credential -UserName $user -Message "Enter the Windows password for the scheduled task account."
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credential.Password)
    try {
        $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $createArgs += @("/RU", $credential.UserName, "/RP", $password)
    }
    finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

& schtasks.exe @createArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task '$taskName'."
}

$task = Get-ScheduledTask -TaskName $taskName
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
$task.Settings.MultipleInstances = "IgnoreNew"
$task.Settings.ExecutionTimeLimit = "PT4M"
Set-ScheduledTask -InputObject $task | Out-Null

& schtasks.exe /Query /TN $taskName /V /FO LIST
if ($LASTEXITCODE -ne 0) {
    throw "Failed to query scheduled task '$taskName'."
}
