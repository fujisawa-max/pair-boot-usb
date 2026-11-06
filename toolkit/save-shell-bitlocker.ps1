# Save a read-only Shell observation without nested powershell -Command quoting.
param(
    [switch]$UsbStorageTarget,    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z]:$')][string]$Volume,
    [Parameter(Mandatory=$true)][string]$OutputFile
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'volume-identity.ps1')
$partitions = @(Get-Partition -DriveLetter $Volume.Substring(0,1))
if ($partitions.Count -ne 1) { throw 'Partition selection is ambiguous.' }
$identity = Get-RootPartitionIdentity -Partition $partitions[0] -UsbStorageTarget:$UsbStorageTarget
$observation = & (Join-Path $PSScriptRoot 'get-shell-bitlocker.ps1') -Volume $Volume
$record = [ordered]@{
    schema = 1
    kind = 'bitlocker-shell-observation'
    hardware_uuid = ([guid](Get-CimInstance Win32_ComputerSystemProduct).UUID).ToString().ToLowerInvariant()
    partition_guid = if ($identity.style -eq 'gpt') { $identity.partition_guid } else { $null }
    checked_at_unix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    observation = $observation
}
if ($identity.style -eq 'mbr') { $record.partition_identity = $identity }
$bytes = [Text.UTF8Encoding]::new($false).GetBytes(($record | ConvertTo-Json -Depth 5) + "`n")
$stream = [IO.File]::Open($OutputFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
Write-Output "Saved: $OutputFile"
