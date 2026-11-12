# File-only fleet administration. No formatting, Windows storage writes, or MOK operations.
param(
    [Parameter(Mandatory=$true)][ValidateSet('Status','Collect','Merge','Deploy','Convert','Name')][string]$Action,
    [string[]]$Inputs,
    [string]$OutputFile,
    [string]$UsbRoot,
    [int]$Generation,
    [string]$RootVersion = '',
    [string]$HostUuid,
    [string]$ComputerName,
    [object[]]$NameUpdates = @(),
    [switch]$AsJson,
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
function Test-Integer($Value) { return ($Value -is [int] -or $Value -is [long]) }
function Canonical-Json($Value) {
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [Collections.IDictionary]) {
        $parts = @($Value.Keys | Sort-Object -CaseSensitive | ForEach-Object { (ConvertTo-Json -InputObject ([string]$_) -Compress) + ':' + (Canonical-Json $Value[$_]) })
        return '{' + ($parts -join ',') + '}'
    }
    if ($Value -is [pscustomobject]) {
        $parts = @($Value.PSObject.Properties.Name | Sort-Object -CaseSensitive | ForEach-Object { (ConvertTo-Json -InputObject $_ -Compress) + ':' + (Canonical-Json $Value.$_) })
        return '{' + ($parts -join ',') + '}'
    }
    if ($Value -is [array]) { return '[' + (@($Value | ForEach-Object { Canonical-Json $_ }) -join ',') + ']' }
    return (ConvertTo-Json -InputObject $Value -Compress -Depth 12)
}
function Host-Identity($Value) {
    $fields = @{}
    foreach ($property in $Value.PSObject.Properties) {
        if ($property.Name -notin @('checked_at_unix','windows_computer_name')) { $fields[$property.Name] = $property.Value }
    }
    return (Canonical-Json $fields)
}
function Check-ComputerName($Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -isnot [string] -or $Value.Length -lt 1 -or $Value.Length -gt 255 -or $Value -cne $Value.Trim() -or $Value -match '[\p{C}\p{Zl}\p{Zp}]') { throw 'Invalid Windows PC name.' }
    return $Value
}
function Check-Host($Value) {
    $required = @('schema','hardware_uuid','ntfs_uuid','gpu_bdf','gpu_pci_id','checked_at_unix','bitlocker_volume_status','bitlocker_protection','fast_startup_enabled','secure_boot_enabled','partition_is_boot','partition_is_system','partition_is_hidden','storage_directory')
    foreach ($key in $required) { if ($Value.PSObject.Properties.Name -cnotcontains $key) { throw "Missing field: $key" } }
    $hostId = ([Guid]::Parse($Value.hardware_uuid)).ToString().ToLowerInvariant()
    if ($hostId -ne $Value.hardware_uuid.ToLowerInvariant() -or $hostId -eq [Guid]::Empty.ToString() -or $hostId -eq 'ffffffff-ffff-ffff-ffff-ffffffffffff') { throw 'Invalid hardware UUID.' }
    if (-not (Test-Integer $Value.schema) -or $Value.schema -ne 1 -or $Value.ntfs_uuid -isnot [string] -or $Value.ntfs_uuid -cnotmatch '^[A-Fa-f0-9]{16}$') { throw 'Invalid host/NTFS record.' }
    $mode = $Value.PSObject.Properties['boot_mode']
    $test = $null -ne $mode -and $mode.Value -is [string] -and $mode.Value -ceq 'no-gpu-test'
    if ($null -ne $mode -and -not $test) { throw 'Unknown boot mode.' }
    if ($test) {
        if ($null -ne $Value.gpu_bdf -or $null -ne $Value.gpu_pci_id -or $null -eq $Value.PSObject.Properties['storage_target']) { throw 'No-GPU test requires explicit USB storage and null GPU identity.' }
    } elseif ($Value.gpu_bdf -isnot [string] -or $Value.gpu_bdf -cnotmatch '^[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]$' -or $Value.gpu_pci_id -isnot [string] -or $Value.gpu_pci_id -cnotin $SupportedRtx30Ids) {
        throw 'Invalid GPU record; register from Fedora.'
    }
    if ($Value.checked_at_unix -isnot [long] -and $Value.checked_at_unix -isnot [int] -and $Value.checked_at_unix -isnot [double]) { throw 'Invalid timestamp type.' }
    $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$Value.checked_at_unix
    if ([double]::IsNaN($age) -or [double]::IsInfinity($age) -or [double]$Value.checked_at_unix -lt 0 -or $age -lt 0) { throw 'Invalid or future-dated inventory timestamp.' }
    if ($Value.bitlocker_volume_status -cne 'FullyDecrypted' -or $Value.bitlocker_protection -cne 'Off') { throw 'BitLocker encrypted/protected/unknown.' }
    if ($Value.fast_startup_enabled -isnot [bool]) { throw 'Invalid Fast Startup observation.' }
    foreach ($key in @('partition_is_system','partition_is_hidden')) {
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
    if ($test) { $clean['boot_mode'] = 'no-gpu-test' }
    if ($null -ne $consentProperty) { $clean['personal_os_volume_consent'] = $consentProperty.Value }
    if ($null -ne $usbTargetProperty) { $clean['storage_target'] = $usbTargetProperty.Value }
    $pcName = $Value.PSObject.Properties['windows_computer_name']
    if ($null -ne $pcName) { $clean.windows_computer_name = Check-ComputerName $pcName.Value }
    $clean.hardware_uuid = $hostId
    $clean.ntfs_uuid = $Value.ntfs_uuid.ToUpperInvariant()
    $clean.checked_at_unix = [double]$Value.checked_at_unix
    return [pscustomobject]$clean
}
function Encode-Registry($Value) {
    $normalized = [ordered]@{schema=$Value.schema; generation=$Value.generation; root_version=$Value.root_version; records=@($Value.records | Sort-Object hardware_uuid)}
    $raw = $utf8.GetBytes(($normalized | ConvertTo-Json -Depth 12 -Compress))
    $envelope = [ordered]@{ schema = $Value.schema; payload_base64 = [Convert]::ToBase64String($raw); sha256 = (Hash-Bytes $raw) }
    return $utf8.GetBytes(($envelope | ConvertTo-Json -Depth 3) + "`n")
}
function Read-Input([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 1048576) { throw 'Expected bounded regular input file.' }
    $value = [IO.File]::ReadAllText($item.FullName, $utf8) | ConvertFrom-Json
    if ($null -ne $value.PSObject.Properties['payload_base64']) {
        if (-not (Test-Integer $value.schema) -or $value.schema -notin @(1,2)) { throw 'Unknown registry envelope.' }
        $envelopeSchema = $value.schema
        $raw = [Convert]::FromBase64String($value.payload_base64)
        if ((Hash-Bytes $raw) -cne $value.sha256) { throw 'Registry checksum mismatch.' }
        $value = $utf8.GetString($raw) | ConvertFrom-Json
        if (-not (Test-Integer $value.schema) -or $value.schema -ne $envelopeSchema -or -not (Test-Integer $value.generation) -or $value.generation -lt 1 -or $value.root_version -isnot [string] -or $value.root_version -cnotmatch '^[a-zA-Z0-9._-]{1,80}$') { throw 'Invalid registry metadata.' }
        $ids = @{}
        $records = @()
        foreach ($record in @($value.records)) {
            if ($value.schema -eq 2 -and $null -eq $record.PSObject.Properties['windows_computer_name']) { throw 'Schema 2 requires a Windows PC name field.' }
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
            if (-not (Test-Integer $value.schema) -or $value.schema -notin @(1,2)) { throw 'Unsupported registry schema.' }
            $prior = [Math]::Max($prior, $value.generation)
            $items = @($value.records)
        } else { $items = @($value) }
        foreach ($item in $items) {
            $item = Check-Host $item
            if ($null -eq $item.PSObject.Properties['windows_computer_name']) { $item | Add-Member -NotePropertyName windows_computer_name -NotePropertyValue $null }
            $id = $item.hardware_uuid
            if ($records.ContainsKey($id)) {
                if ((Host-Identity $item) -cne (Host-Identity $records[$id])) { throw "Conflicting configuration for host $id" }
                $leftName = $records[$id].windows_computer_name
                $rightName = $item.windows_computer_name
                if ($leftName -and $rightName -and $leftName -cne $rightName) { throw "Conflicting PC names; update the display name explicitly for host $id" }
                if (-not $rightName) { $item.windows_computer_name = $leftName }
                $item.checked_at_unix = [Math]::Max($item.checked_at_unix, $records[$id].checked_at_unix)
            }
            $records[$id] = $item
        }
    }
    if ($NewGeneration -le $prior -or $Version -cnotmatch '^[a-zA-Z0-9._-]{1,80}$') { throw 'New generation must exceed every input generation; valid version required.' }
    return [pscustomobject][ordered]@{schema=2; generation=$NewGeneration; root_version=$Version; records=@($records.Keys | Sort-Object | ForEach-Object { $records[$_] })}
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
    if (-not (Test-Integer $marker.schema) -or $marker.schema -ne 1 -or $marker.kind -cne 'gpu-linux-config' -or $marker.root_version -isnot [string] -or $marker.root_version -cnotmatch '^[a-zA-Z0-9._-]{1,80}$') { throw 'Invalid USB configuration marker.' }
    $schemaProperty = $marker.PSObject.Properties['registry_schema']
    $script:UsbRegistrySchema = if ($null -eq $schemaProperty) { 1 } else { $schemaProperty.Value }
    if (-not (Test-Integer $script:UsbRegistrySchema) -or $script:UsbRegistrySchema -notin @(1,2)) { throw 'Unknown USB registry schema capability.' }
    if (-not $RootVersion) { $script:RootVersion = $marker.root_version }
    return $full
}
if ($Action -in @('Merge','Convert','Name')) {
    if (-not $Inputs -or -not $OutputFile -or (Test-Path -LiteralPath $OutputFile)) { throw 'Inputs and a fresh output filename required.' }
    if ($UsbRoot) { $null = Check-Usb $UsbRoot }
    $values = @($Inputs | ForEach-Object { Read-Input $_ })
    if (-not $RootVersion) {
        $versions = @($values | Where-Object { $null -ne $_.PSObject.Properties['root_version'] } | ForEach-Object { $_.root_version } | Select-Object -Unique)
        $RootVersion = if ($versions.Count -gt 0) { $versions[0] } else { 'registry-v1' }
    }
    if ($Action -ne 'Merge' -and ($values.Count -ne 1 -or $null -eq $values[0].PSObject.Properties['records'])) { throw 'Exactly one registry required.' }
    $registry = New-Registry $values $Generation $RootVersion
    if ($Action -eq 'Name') {
        if ($PSBoundParameters.ContainsKey('NameUpdates')) {
            if ($HostUuid -or $ComputerName -or $NameUpdates.Count -lt 1 -or $NameUpdates.Count -gt @($registry.records).Count) { throw 'Provide a nonempty batch or a single PC, not both.' }
            $updates=$NameUpdates
        } else {$updates=@([pscustomobject]@{hardware_uuid=$HostUuid;windows_computer_name=$ComputerName})}
        $pending=@{}
        foreach ($update in $updates) {
            if ($null -eq $update) {throw 'Invalid PC name update.'}
            $keys=if($update -is [Collections.IDictionary]){@($update.Keys)}else{@($update.PSObject.Properties.Name)}
            if ($keys.Count -ne 2 -or $keys -cnotcontains 'hardware_uuid' -or $keys -cnotcontains 'windows_computer_name') {throw 'Only PC UUID and name may be updated.'}
            if ($update.hardware_uuid -isnot [string]) {throw 'Invalid PC UUID.'}
            $id=([guid]$update.hardware_uuid).ToString().ToLowerInvariant()
            if ($pending.ContainsKey($id)) {throw 'Duplicate PC name update.'}
            $name=Check-ComputerName $update.windows_computer_name
            if (-not $name) {throw 'A PC name is required.'}
            if (@($registry.records | Where-Object {$_.hardware_uuid -ceq $id}).Count -ne 1) {throw 'Selected PC is not registered.'}
            $pending[$id]=$name
        }
        # Validate the whole batch before changing records or publishing output.
        foreach ($item in $registry.records) {
            if ($pending.ContainsKey($item.hardware_uuid)) {$item.windows_computer_name=$pending[$item.hardware_uuid]}
        }
    }
    Atomic-Write $OutputFile (Encode-Registry $registry)
    if ($Action -eq 'Name') {Write-Output "Updated $($pending.Count) PC names; schema 2; generation $Generation"}
    else {Write-Output "Merged $(@($registry.records).Count) hosts; schema 2; generation $Generation"}
} elseif ($Action -eq 'Status') {
    foreach ($path in $Inputs) {
        $registry = Read-Input $path
        if ($AsJson) { $registry | ConvertTo-Json -Depth 12; continue }
        Write-Output "$path : schema=$($registry.schema) generation=$($registry.generation) hosts=$(@($registry.records).Count)"
        $registry.records | Select-Object @{Name='PC';Expression={if ($_.PSObject.Properties['windows_computer_name'] -and $_.windows_computer_name) {$_.windows_computer_name} else {'(unnamed)'}}},hardware_uuid,storage_directory,gpu_bdf | Format-Table -AutoSize | Out-String | Write-Output
    }
} elseif ($Action -eq 'Collect') {
    $target = Check-Usb $UsbRoot
    if (-not $OutputFile -or (Test-Path -LiteralPath $OutputFile)) { throw 'Fresh output filename required.' }
    $path = Join-Path $target 'gpu-config/registry.json'
    $registry = Read-Input $path
    Atomic-Write $OutputFile (Encode-Registry $registry)
    Write-Output 'Registry collected.'
} else {
    if (@($Inputs).Count -ne 1) { throw 'Exactly one merged registry input required.' }
    $registry = Read-Input $Inputs[0]
    if ($null -eq $registry.PSObject.Properties['records']) { throw 'Deploy requires a registry.' }
    # Validate the selected physical GPU-LOGS USB before reading its marker.
    $target = Check-Usb $UsbRoot
    if ($registry.schema -gt $script:UsbRegistrySchema) { throw 'USB boot files do not support registry schema 2; update the USB first.' }
    $directory = Join-Path $target 'gpu-config'
    if (-not (Test-Path -LiteralPath $directory)) { [void][IO.Directory]::CreateDirectory($directory) }
    Assert-OrdinaryDirectory $directory
    $current = Join-Path $directory 'registry.json'
    if (Test-Path -LiteralPath $current) {
        $old = Read-Input $current
        if ($old.generation -gt $registry.generation) { throw 'Refusing registry rollback.' }
        if ($old.generation -eq $registry.generation -and (Canonical-Json $old.records) -cne (Canonical-Json $registry.records)) { throw 'Same generation has different contents.' }
        foreach ($item in @($old.records)) {
            $matching = @($registry.records | Where-Object { $_.hardware_uuid -eq $item.hardware_uuid })
            if ($matching.Count -ne 1) { throw 'Deployment would remove a host.' }
            if ((Host-Identity $item) -cne (Host-Identity $matching[0])) { throw 'Deployment would change an existing host mapping.' }
        }
    }
    $hostsDirectory = Join-Path $directory 'hosts'
    if (-not (Test-Path -LiteralPath $hostsDirectory)) { [void][IO.Directory]::CreateDirectory($hostsDirectory) }
    Assert-OrdinaryDirectory $hostsDirectory
    foreach ($item in @($registry.records)) {
        $isTest = $null -ne $item.PSObject.Properties['boot_mode'] -and $item.boot_mode -ceq 'no-gpu-test'
        $targetBdf = if ($isTest) { 'unconfigured' } elseif ($item.gpu_pci_id -cin $UnlockRtx30Ids) { $item.gpu_bdf } else { "unsupported" }
        $defaultEntry = if (-not $isTest -and $item.gpu_pci_id -ceq '10de:2504') { '1' } else { '0' }
        $text = "# GRUB Environment Block`ngpu_targets=$targetBdf`ngpu_default=$defaultEntry`n"
        $text = $text.PadRight(1024, '#')
        Atomic-Write (Join-Path $hostsDirectory ($item.hardware_uuid + '.env')) ($utf8.GetBytes($text))
    }
    Atomic-Write $current (Encode-Registry $registry)
    Write-Output "Deployed generation $($registry.generation) to $target"
}
