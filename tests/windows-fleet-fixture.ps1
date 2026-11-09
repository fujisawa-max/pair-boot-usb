# File-only fixture adapter. Windows device inspection is never used here.
param([string]$ScriptPath, [string]$RequestFile)
$ErrorActionPreference = 'Stop'
$value = Get-Content -Raw -LiteralPath $RequestFile | ConvertFrom-Json
$parameters = @{Action=[string]$value.action; OfflineFixture=$true}
foreach ($key in @('Inputs','OutputFile','UsbRoot','Generation','RootVersion')) {
    $property = $value.PSObject.Properties[$key]
    if ($null -ne $property) { $parameters[$key] = $property.Value }
}
& $ScriptPath @parameters
