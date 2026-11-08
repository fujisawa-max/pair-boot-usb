# Read-only evidence for a selected NTFS volume, including USB data volumes.
# Run manually in elevated Windows PowerShell. No encryption or key operations.
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z]:$')][string]$Volume,
    [Parameter(Mandatory=$true)][string]$OutputFile
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (Test-Path -LiteralPath $OutputFile) { throw 'Output already exists; choose a new evidence file.' }
$Volume = $Volume.ToUpperInvariant()
$selected = Get-Volume -DriveLetter $Volume.Substring(0,1)
if ($selected.FileSystem -ne 'NTFS') { throw 'Selected volume is not NTFS.' }
# Do not request key protectors, recovery passwords, or encryption keys.
$volumes = @(Get-CimInstance -Namespace 'root/CIMV2/Security/MicrosoftVolumeEncryption' `
    -ClassName Win32_EncryptableVolume -Filter "DriveLetter='$Volume'")
if ($volumes.Count -ne 1 -or $volumes[0].DriveLetter -ine $Volume) {
    throw 'BitLocker volume state is missing or ambiguous; no evidence was saved.'
}
$conversion = Invoke-CimMethod -InputObject $volumes[0] -MethodName GetConversionStatus -Arguments @{PrecisionFactor=[uint32]0}
$protection = Invoke-CimMethod -InputObject $volumes[0] -MethodName GetProtectionStatus
function Test-KnownZero($Value) {
    return (($Value -is [uint32] -or $Value -is [int] -or $Value -is [long]) -and $Value -eq 0)
}
if (-not (Test-KnownZero $conversion.ReturnValue) -or -not (Test-KnownZero $protection.ReturnValue)) {
    throw 'BitLocker state query failed; no evidence was saved.'
}
# WMI values differ from Shell property values. Protection off alone is not enough.
if (-not (Test-KnownZero $conversion.ConversionStatus) -or
    -not (Test-KnownZero $conversion.EncryptionPercentage) -or
    -not (Test-KnownZero $protection.ProtectionStatus)) {
    throw 'BitLocker is encrypted, transitioning, protected, or unknown; no evidence was saved.'
}
$info = & fsutil fsinfo ntfsinfo $Volume 2>&1
if ($LASTEXITCODE -ne 0) { throw 'Cannot obtain NTFS serial; run in elevated Windows PowerShell.' }
$serial = [regex]::Match(($info -join "`n"), '0x([0-9a-fA-F]{16})\b')
if (-not $serial.Success) { throw 'NTFS serial could not be parsed; do not guess the Linux UUID.' }
$hardware = ([guid](Get-CimInstance Win32_ComputerSystemProduct).UUID).ToString().ToLowerInvariant()
if ($hardware -in @('00000000-0000-0000-0000-000000000000','ffffffff-ffff-ffff-ffff-ffffffffffff')) {
    throw 'Invalid hardware UUID.'
}
$record = [ordered]@{
    schema = 1
    kind = 'bitlocker-admin-observation'
    hardware_uuid = $hardware
    windows_drive = $Volume
    ntfs_uuid = $serial.Groups[1].Value.ToUpperInvariant()
    checked_at_unix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    bitlocker_volume_status = 'FullyDecrypted'
    bitlocker_protection = 'Off'
    source = 'Win32_EncryptableVolume.GetConversionStatus/GetProtectionStatus'
    conversion_status = $conversion.ConversionStatus
    encryption_percentage = $conversion.EncryptionPercentage
    protection_status = $protection.ProtectionStatus
}
$bytes = [Text.UTF8Encoding]::new($false).GetBytes(($record | ConvertTo-Json -Depth 4) + "`n")
$stream = [IO.File]::Open($OutputFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
Write-Output 'Saved BitLocker evidence. Select this JSON as separate evidence in Fedora registration.'
