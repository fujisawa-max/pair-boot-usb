# Read-only inventory. Run manually in an elevated Windows PowerShell.
# Does not change Fast Startup, BitLocker, boot settings, disks, or models.
param(
    [switch]$PersonalUseOsVolume,
    [string]$PersonalUseConfirmation,
    [ValidatePattern('^[A-Za-z]:$')][string]$Volume = 'D:',
    [Parameter(Mandatory=$true)][string]$OutputFile,
    [ValidateScript({ $_ -is [string] -and $_ -cmatch '^[A-Za-z][A-Za-z0-9._-]{0,63}\z' -and -not $_.EndsWith('.') -and ($_.Split('.')[0] -inotmatch '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]|Windows|Users|ProgramData|Boot|Recovery|EFI|PerfLogs|MSOCache)$') })][string]$StorageDirectory = 'GPU-Linux'
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputFile) { throw 'Output already exists; choose a new inventory file.' }
# Query only state methods, never KeyProtector objects or recovery-key methods.
$encryptedVolume = Get-CimInstance -Namespace 'root/CIMV2/Security/MicrosoftVolumeEncryption' `
    -ClassName Win32_EncryptableVolume -Filter "DriveLetter='$Volume'"
if ($null -eq $encryptedVolume) { throw 'BitLocker state is unknown.' }
$conversion = Invoke-CimMethod -InputObject $encryptedVolume -MethodName GetConversionStatus -Arguments @{ PrecisionFactor = [uint32]0 }
$protection = Invoke-CimMethod -InputObject $encryptedVolume -MethodName GetProtectionStatus
if ($conversion.ReturnValue -ne 0 -or $protection.ReturnValue -ne 0) { throw 'BitLocker state query failed.' }
$volumeStatus = if ($conversion.ConversionStatus -eq 0) { 'FullyDecrypted' } else { 'EncryptedOrInProgress' }
$protectionStatus = switch ($protection.ProtectionStatus) { 0 { 'Off' } 1 { 'On' } default { 'Unknown' } }
$power = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
if ($power.HiberbootEnabled -isnot [int] -or $power.HiberbootEnabled -notin @(0,1)) { throw 'Fast Startup state is unknown.' }
$info = & fsutil fsinfo ntfsinfo $Volume 2>&1
if ($LASTEXITCODE -ne 0) { throw 'Cannot obtain NTFS volume serial number.' }
# First 64-bit hex value in ntfsinfo is the NTFS volume serial (locale-independent format).
$match = [regex]::Match(($info -join "`n"), '0x([0-9a-fA-F]{16})\b')
if (-not $match.Success) { throw 'NTFS serial could not be parsed; do not guess the Linux UUID.' }
$hardware = (Get-CimInstance Win32_ComputerSystemProduct).UUID.ToLowerInvariant()
$secureBoot = Confirm-SecureBootUEFI
$partition = Get-Partition -DriveLetter $Volume.Substring(0,1)
$volumeInfo = Get-Volume -DriveLetter $Volume.Substring(0,1)
$disk = $partition | Get-Disk
if ($volumeInfo.FileSystem -ne 'NTFS') { throw 'Selected volume is not NTFS.' }
foreach ($flag in @('IsBoot','IsSystem','IsHidden')) {
    if ($partition.$flag -isnot [bool]) { throw "Unknown partition safety flag: $flag" }
}
if ($partition.IsSystem -or $partition.IsHidden) { throw 'Refusing boot/system/hidden partition.' }
if ($disk.BusType -ne 'NVMe' -and $disk.BusType -ne 'SATA') { throw 'Expected explicit internal NVMe/SATA target.' }
if ($volumeStatus -ne 'FullyDecrypted' -or $protectionStatus -ne 'Off') { throw 'Windows safety checks failed.' }
if ($secureBoot -ne $true) { throw 'This signed USB release requires confirmed Secure Boot.' }
$record = [ordered]@{
    schema = 1
    partition_is_boot = [bool]$partition.IsBoot
    partition_is_system = [bool]$partition.IsSystem
    partition_is_hidden = [bool]$partition.IsHidden
    storage_directory = $StorageDirectory
    hardware_uuid = $hardware
    ntfs_uuid = $match.Groups[1].Value.ToUpperInvariant()
    bitlocker_volume_status = $volumeStatus
    bitlocker_protection = $protectionStatus
    fast_startup_enabled = [bool]$power.HiberbootEnabled
    secure_boot_enabled = [bool]$secureBoot
    checked_at_unix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}
if ($partition.IsBoot -or $PersonalUseOsVolume) {
    if (-not $PersonalUseOsVolume -or $PersonalUseConfirmation -cne '個人利用で許可') { throw 'OS volume requires -PersonalUseOsVolume and explicit -PersonalUseConfirmation. Windows data loss/boot failure risk; back up first.' }
    $record.personal_os_volume_consent = [ordered]@{
        policy = 'personal-os-v1'; hardware_uuid = $hardware
        target = $match.Groups[1].Value.ToUpperInvariant(); storage_directory = $StorageDirectory
    }
}
# Save only a small non-secret inventory to the explicitly chosen new output file.
$encoded = [Text.UTF8Encoding]::new($false).GetBytes(($record | ConvertTo-Json) + "`n")
$stream = [IO.File]::Open($OutputFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try { $stream.Write($encoded, 0, $encoded.Length); $stream.Flush($true) } finally { $stream.Dispose() }
Write-Output "Inventory saved. Verify NTFS UUID against Linux blkid before deployment. Host file: $hardware.json"
