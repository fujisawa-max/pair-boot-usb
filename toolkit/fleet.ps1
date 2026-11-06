# File-only fleet administration. No formatting, Windows storage writes, or MOK operations.
param(
    [Parameter(Mandatory=$true)][ValidateSet('Status','Collect','Merge','Deploy')][string]$Action,
    [string[]]$Inputs,
    [string]$OutputFile,
    [string]$UsbRoot,
    [int]$Generation,
    [string]$RootVersion = '',
    [switch]$OfflineFixture
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$utf8 = [Text.UTF8Encoding]::new($false)
$UnlockRtx30Ids = @('10de:2203','10de:2204','10de:2206','10de:2207','10de:2208','10de:220a','10de:2216','10de:2414','10de:2482','10de:2484','10de:2486','10de:2487','10de:2488','10de:2489','10de:249c','10de:249d','10de:24a0','10de:24c7','10de:24c9','10de:24dc','10de:24dd','10de:24e0','10de:2503','10de:2504','10de:2507','10de:2508','10de:2544')
$SupportedRtx30Ids = @('10de:2203','10de:2204','10de:2206','10de:2207','10de:2208','10de:220a','10de:2216','10de:2414','10de:2420','10de:2460','10de:2482','10de:2484','10de:2486','10de:2487','10de:2488','10de:2489','10de:249c','10de:249d','10de:24a0','10de:24c7','10de:24c9','10de:24dc','10de:24dd','10de:24e0','10de:2503','10de:2504','10de:2507','10de:2508','10de:2520','10de:2521','10de:2523','10de:2544','10de:2560','10de:2563','10de:2582','10de:2584','10de:25a0','10de:25a2','10de:25a5','10de:25ab','10de:25ac','10de:25e0','10de:25e2','10de:25e5','10de:25ec','10de:2822','10de:28a3','10de:28e3')
function Hash-Bytes([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() } finally { $hash.Dispose() }
}
function Check-Host($Value) {
    $required = @('schema','hardware_uuid','ntfs_uuid','gpu_bdf','gpu_pci_id','checked_at_unix','bitlocker_volume_status','bitlocker_protection','fast_startup_enabled','secure_boot_enabled','partition_is_boot','partition_is_system','partition_is_hidden','storage_directory')
    foreach ($key in $required) { if ($null -eq $Value.PSObject.Properties[$key]) { throw "Missing field: $key" } }
    $hostId = ([Guid]::Parse($Value.hardware_uuid)).ToString().ToLowerInvariant()
    if ($hostId -ne $Value.hardware_uuid.ToLowerInvariant() -or $hostId -eq [Guid]::Empty.ToString() -or $hostId -eq 'ffffffff-ffff-ffff-ffff-ffffffffffff') { throw 'Invalid hardware UUID.' }
    if ($Value.schema -ne 1 -or $Value.ntfs_uuid -cnotmatch '^[A-Fa-f0-9]{16}$' -or $Value.gpu_bdf -cnotmatch '^[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]$' -or $Value.gpu_pci_id -cnotin $SupportedRtx30Ids) { throw 'Invalid host/NTFS/GPU record; register from Fedora.' }
    if ($Value.checked_at_unix -isnot [long] -and $Value.checked_at_unix -isnot [int] -and $Value.checked_at_unix -isnot [double]) { throw 'Invalid timestamp type.' }
    $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$Value.checked_at_unix
    if ([double]::IsNaN($age) -or [double]::IsInfinity($age) -or $age -lt 0 -or $age -gt 2592000) { throw 'Inventory expired or future dated.' }
    if ($Value.bitlocker_volume_status -cne 'FullyDecrypted' -or $Value.bitlocker_protection -cne 'Off') { throw 'BitLocker encrypted/protected/unknown.' }
    foreach ($key in @('fast_startup_enabled','partition_is_system','partition_is_hidden')) {
        if ($Value.$key -isnot [bool] -or $Value.$key -ne $false) { throw "Unsafe or unknown: $key" }
    }
    if ($Value.partition_is_boot -isnot [bool]) { throw 'Unknown boot partition flag.' }
    $consentProperty = $Value.PSObject.Properties['personal_os_volume_consent']
    if ($Value.partition_is_boot -or $null -ne $consentProperty) {
        if ($null -eq $consentProperty) { throw 'Personal OS consent required.' }
        $consent = $consentProperty.Value
        if ($null -eq $consent -or @($consent.PSObject.Properties).Count -ne 4 -or $consent.policy -cne 'personal-os-v1' -or $consent.hardware_uuid -cne $Value.hardware_uuid -or $consent.target -cne $Value.ntfs_uuid -or $consent.storage_directory -cne $Value.storage_directory) { throw 'Personal OS consent does not match target.' }
    }
    if ($Value.secure_boot_enabled -isnot [bool] -or $Value.secure_boot_enabled -ne $true) { throw 'Secure Boot must be confirmed.' }
    $name = $Value.storage_directory
    if (-not ($name -is [string] -and $name -cmatch '^[A-Za-z][A-Za-z0-9._-]{0,63}\z' -and -not $name.EndsWith('.') -and ($name.Split('.')[0] -inotmatch '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]|Windows|Users|ProgramData|Boot|Recovery|EFI|PerfLogs|MSOCache)$'))) { throw 'Invalid or reserved storage directory.' }
    $usbTargetProperty = $Value.PSObject.Properties['storage_target']
    if ($null -ne $usbTargetProperty) {
        $target = $usbTargetProperty.Value
        if ($Value.partition_is_boot -or $null -ne $consentProperty) { throw 'USB target must be a data partition without OS exception.' }
        if ($null -eq $target -or @($target.PSObject.Properties).Count -ne 5) { throw 'Invalid USB storage target.' }
        if ($target.kind -ceq 'usb-ntfs-v1') {
            foreach ($key in @('partition_guid','disk_guid')) {
                $id = ([Guid]::Parse($target.$key)).ToString().ToLowerInvariant()
                if ($id -cne $target.$key -or $id -eq [Guid]::Empty.ToString() -or $id -eq 'ffffffff-ffff-ffff-ffff-ffffffffffff') { throw 'Invalid USB target GUID.' }
            }
        } elseif ($target.kind -ceq 'usb-ntfs-mbr-v1') {
            if ($target.disk_signature -isnot [string] -or $target.disk_signature -cnotmatch '^[0-9a-f]{8}$' -or $target.disk_signature -in @('00000000','ffffffff')) { throw 'Invalid MBR signature.' }
            if (($target.partition_number -isnot [int] -and $target.partition_number -isnot [long]) -or $target.partition_number -lt 1 -or $target.partition_number -gt 4) { throw 'Expected primary MBR partition 1-4.' }
        } else { throw 'Unknown USB storage kind.' }
        foreach ($key in @('offset_bytes','size_bytes')) {
            if (($target.$key -isnot [long] -and $target.$key -isnot [int]) -or $target.$key -le 0 -or $target.$key % 512 -ne 0) { throw 'Invalid USB target geometry.' }
        }
    }
    $clean = [ordered]@{}
    foreach ($key in $required) { $clean[$key] = $Value.$key }
    if ($null -ne $consentProperty) { $clean['personal_os_volume_consent'] = $consentProperty.Value }
    if ($null -ne $usbTargetProperty) { $clean['storage_target'] = $usbTargetProperty.Value }
    $clean.hardware_uuid = $hostId
    $clean.ntfs_uuid = $Value.ntfs_uuid.ToUpperInvariant()
    $clean.checked_at_unix = [double]$Value.checked_at_unix
    return [pscustomobject]$clean
}
function Encode-Registry($Value) {
    $normalized = [ordered]@{schema=1; generation=$Value.generation; root_version=$Value.root_version; records=@($Value.records | Sort-Object hardware_uuid)}
    $raw = $utf8.GetBytes(($normalized | ConvertTo-Json -Depth 12 -Compress))
    $envelope = [ordered]@{ schema = 1; payload_base64 = [Convert]::ToBase64String($raw); sha256 = (Hash-Bytes $raw) }
    return $utf8.GetBytes(($envelope | ConvertTo-Json -Depth 3) + "`n")
}
function Read-Input([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 1048576) { throw 'Expected bounded regular input file.' }
    $value = [IO.File]::ReadAllText($item.FullName, $utf8) | ConvertFrom-Json
    if ($null -ne $value.PSObject.Properties['payload_base64']) {
        if ($value.schema -ne 1) { throw 'Unknown registry envelope.' }
        $raw = [Convert]::FromBase64String($value.payload_base64)
        if ((Hash-Bytes $raw) -cne $value.sha256) { throw 'Registry checksum mismatch.' }
        $value = $utf8.GetString($raw) | ConvertFrom-Json
        if ($value.schema -ne 1 -or $value.generation -lt 1 -or $value.root_version -cnotmatch '^[a-zA-Z0-9._-]{1,80}$') { throw 'Invalid registry metadata.' }
        $ids = @{}
        $records = @()
        foreach ($record in @($value.records)) {
            $record = Check-Host $record
            if ($ids.ContainsKey($record.hardware_uuid)) { throw 'Duplicate registry host.' }
            $ids[$record.hardware_uuid] = $true
            $records += $record
        }
        if ($records.Count -gt 1000) { throw 'Too many records.' }
        $value.records = @($records)
        return $value
    }
    return (Check-Host $value)
}
function New-Registry($Values, [int]$NewGeneration, [string]$Version) {
    $records = @{}
    $prior = 0
    foreach ($value in @($Values)) {
        if ($null -ne $value.PSObject.Properties['records']) {
            if ($value.root_version -cne $Version -and -not ($value.root_version -ceq '0.7.8-shared' -and $Version -ceq '0.8.0-shared')) { throw 'Registry versions conflict.' }
            $prior = [Math]::Max($prior, $value.generation)
            $items = @($value.records)
        } else { $items = @($value) }
        foreach ($item in $items) {
            $item = Check-Host $item
            $id = $item.hardware_uuid
            if ($records.ContainsKey($id)) {
                foreach ($property in $item.PSObject.Properties) {
                    if ($property.Name -ne 'checked_at_unix' -and $property.Value -cne $records[$id].($property.Name)) { throw "Conflicting configuration for host $id" }
                }
                $item.checked_at_unix = [Math]::Max($item.checked_at_unix, $records[$id].checked_at_unix)
            }
            $records[$id] = $item
        }
    }
    if ($NewGeneration -le $prior -or $Version -cnotmatch '^[a-zA-Z0-9._-]{1,80}$') { throw 'New generation must exceed every input generation; valid version required.' }
    return [pscustomobject][ordered]@{schema=1; generation=$NewGeneration; root_version=$Version; records=@($records.Keys | Sort-Object | ForEach-Object { $records[$_] })}
}
function Assert-OrdinaryDirectory([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Expected ordinary directory.' }
}
function Atomic-Write([string]$Path, [byte[]]$Bytes) {
    $Path = [IO.Path]::GetFullPath($Path)
    Assert-OrdinaryDirectory ([IO.Path]::GetDirectoryName($Path))
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe destination.' }
    }
    $temp = $Path + '.tmp-' + [Guid]::NewGuid().ToString('N')
    $stream = [IO.File]::Open($temp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    if ((Hash-Bytes ([IO.File]::ReadAllBytes($temp))) -cne (Hash-Bytes $Bytes)) { throw 'Write readback failed.' }
    if ($env:OS -eq 'Windows_NT') {
        if (-not ('FleetNativeMove' -as [type])) {
            Add-Type 'using System; using System.Runtime.InteropServices; public class FleetNativeMove { [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool MoveFileEx(string source, string target, uint flags); }'
        }
        if (-not [FleetNativeMove]::MoveFileEx($temp, $Path, 9)) { throw 'Atomic rename failed; temporary file retained.' }
    } else {
        if (-not $OfflineFixture) { throw 'Non-Windows deployment requires explicit fixture mode.' }
        [IO.File]::Move($temp, $Path, $true)
    }
}
function Check-Usb([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    Assert-OrdinaryDirectory $full
    if (-not $OfflineFixture) {
        if ($env:OS -ne 'Windows_NT' -or $full -notmatch '^[A-Za-z]:[\\/]?$') { throw 'Explicit Windows USB drive root required.' }
        $letter = $full.Substring(0,1)
        $partition = Get-Partition -DriveLetter $letter
        $disk = $partition | Get-Disk
        $volume = Get-Volume -DriveLetter $letter
        foreach ($object in @($disk, $partition)) {
            if ($object.IsBoot -isnot [bool] -or $object.IsSystem -isnot [bool]) { throw 'Unknown USB safety flags.' }
        }
        if ($disk.BusType -ne 'USB' -or $disk.IsBoot -or $disk.IsSystem -or $partition.IsBoot -or $partition.IsSystem -or $volume.FileSystem -ne 'FAT32' -or $volume.FileSystemLabel -ne 'GPU-LOGS') { throw 'Refusing target that is not a dedicated GPU-LOGS USB FAT32 volume.' }
    } elseif ($env:OS -eq 'Windows_NT') { throw 'OfflineFixture is for Linux fixture tests only.' }
    $markerPath = Join-Path $full 'usb-config.json'
    $marker = Get-Content -Raw -LiteralPath $markerPath | ConvertFrom-Json
    if ($marker.schema -ne 1 -or $marker.kind -cne 'gpu-linux-config' -or $marker.root_version -cnotmatch '^[a-zA-Z0-9._-]{1,80}$') { throw 'USB marker/version mismatch.' }
    if ($RootVersion -and $marker.root_version -cne $RootVersion) { throw 'USB marker/version mismatch.' }
    if (-not $RootVersion) { $script:RootVersion = $marker.root_version }
    return $full
}
if ($Action -eq 'Merge') {
    if (-not $Inputs -or -not $OutputFile -or (Test-Path -LiteralPath $OutputFile)) { throw 'Inputs and a fresh output filename required.' }
    $values = @($Inputs | ForEach-Object { Read-Input $_ })
    if (-not $RootVersion) {
        $versions = @($values | Where-Object { $null -ne $_.PSObject.Properties['root_version'] } | ForEach-Object { $_.root_version } | Select-Object -Unique)
        if ($versions.Count -ne 1) { throw 'Specify one compatible root version, or use -RootVersion for raw records.' }
        $RootVersion = $versions[0]
    }
    $registry = New-Registry $values $Generation $RootVersion
    Atomic-Write $OutputFile (Encode-Registry $registry)
    Write-Output "Merged $(@($registry.records).Count) hosts; generation $Generation"
} elseif ($Action -eq 'Status') {
    foreach ($path in $Inputs) {
        $registry = Read-Input $path
        Write-Output "$path : generation=$($registry.generation) version=$($registry.root_version) hosts=$(@($registry.records).Count)"
    }
} elseif ($Action -eq 'Collect') {
    $target = Check-Usb $UsbRoot
    if (-not $OutputFile -or (Test-Path -LiteralPath $OutputFile)) { throw 'Fresh output filename required.' }
    $path = Join-Path $target 'gpu-config/registry.json'
    $registry = Read-Input $path
    if ($registry.root_version -cne $RootVersion) { throw 'Collected registry version differs from USB marker.' }
    Atomic-Write $OutputFile (Encode-Registry $registry)
    Write-Output 'Registry collected.'
} else {
    if (@($Inputs).Count -ne 1) { throw 'Exactly one merged registry input required.' }
    $registry = Read-Input $Inputs[0]
    if ($null -eq $registry.PSObject.Properties['records']) { throw 'Deploy requires a registry.' }
    $destinationMarker = [IO.File]::ReadAllText((Join-Path $UsbRoot 'usb-config.json'), $utf8) | ConvertFrom-Json
    if ($registry.root_version -ceq '0.7.8-shared' -and $destinationMarker.root_version -ceq '0.8.0-shared') {
        if ($RootVersion -and $RootVersion -cne '0.8.0-shared') { throw 'Explicit target root version mismatch.' }
        $registry = New-Registry @($registry) ($registry.generation + 1) '0.8.0-shared'
    }
    if ($RootVersion -and $RootVersion -cne $registry.root_version) { throw 'Explicit root version differs from input.' }
    $RootVersion = $registry.root_version
    $target = Check-Usb $UsbRoot
    $directory = Join-Path $target 'gpu-config'
    if (-not (Test-Path -LiteralPath $directory)) { [void][IO.Directory]::CreateDirectory($directory) }
    Assert-OrdinaryDirectory $directory
    $current = Join-Path $directory 'registry.json'
    if (Test-Path -LiteralPath $current) {
        $old = Read-Input $current
        if ($old.generation -gt $registry.generation) { throw 'Refusing registry rollback.' }
        if ($old.generation -eq $registry.generation -and (Hash-Bytes (Encode-Registry $old)) -ne (Hash-Bytes (Encode-Registry $registry))) { throw 'Same generation has different contents.' }
        foreach ($item in @($old.records)) {
            $matching = @($registry.records | Where-Object { $_.hardware_uuid -eq $item.hardware_uuid })
            if ($matching.Count -ne 1) { throw 'Deployment would remove a host.' }
            foreach ($property in $item.PSObject.Properties) {
                if ($property.Name -ne 'checked_at_unix' -and $property.Value -cne $matching[0].($property.Name)) { throw 'Deployment would change an existing host mapping.' }
            }
        }
    }
    $hostsDirectory = Join-Path $directory 'hosts'
    if (-not (Test-Path -LiteralPath $hostsDirectory)) { [void][IO.Directory]::CreateDirectory($hostsDirectory) }
    Assert-OrdinaryDirectory $hostsDirectory
    foreach ($item in @($registry.records)) {
        $targetBdf = if ($item.gpu_pci_id -cin $UnlockRtx30Ids) { $item.gpu_bdf } else { "unsupported" }
        $defaultEntry = if ($item.gpu_pci_id -ceq '10de:2504') { '1' } else { '0' }
        $text = "# GRUB Environment Block`ngpu_targets=$targetBdf`ngpu_default=$defaultEntry`n"
        $text = $text.PadRight(1024, '#')
        Atomic-Write (Join-Path $hostsDirectory ($item.hardware_uuid + '.env')) ($utf8.GetBytes($text))
    }
    Atomic-Write $current (Encode-Registry $registry)
    Write-Output "Deployed generation $($registry.generation) to $target"
}
