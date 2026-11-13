param(
    [string]$Workspace = "",
    [string]$Python = "",
    [switch]$Doctor,
    [switch]$Setup,
    [switch]$VerifyNative
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}
if ($Doctor -and $Setup) {
    throw 'Choose either -Doctor or -Setup.'
}
if ($VerifyNative -and -not $Setup) {
    throw '-VerifyNative requires -Setup.'
}
$cliArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'run_server.py'))
if (-not [string]::IsNullOrWhiteSpace($Workspace)) {
    $cliArgs += @('--workspace', $Workspace)
}
$mode = if ($Doctor) { 'doctor' } elseif ($Setup) { 'setup' } else { 'serve' }
$cliArgs += $mode
if ($VerifyNative) { $cliArgs += '--verify-native' }
& $Python @cliArgs
exit $LASTEXITCODE
