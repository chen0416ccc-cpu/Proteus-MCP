param(
    [string]$Workspace = "",
    [string]$Python = "",
    [switch]$Doctor
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}
if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = Join-Path $PSScriptRoot 'workspaces'
}
$mode = if ($Doctor) { 'doctor' } else { 'serve' }
& $Python -X utf8 (Join-Path $PSScriptRoot 'run_server.py') --workspace $Workspace $mode
exit $LASTEXITCODE
