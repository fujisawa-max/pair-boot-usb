# Read-only disk/partition identity; never change disk signatures or layout.
function Get-RootPartitionIdentity {
    param($Partition, [switch]$UsbStorageTarget)
    $offset = [long]$Partition.Offset
    $size = [long]$Partition.Size
    if ($offset -le 0 -or $size -le 0 -or $offset % 512 -ne 0 -or $size % 512 -ne 0) { throw 'Invalid partition geometry.' }
    $gptProperty = $Partition.PSObject.Properties['GptType']
    $guidProperty = $Partition.PSObject.Properties['Guid']
    $gptType = if ($null -eq $gptProperty) { '' } else { [string]$gptProperty.Value }
    $guidText = if ($null -eq $guidProperty) { '' } else { [string]$guidProperty.Value }
    $guid = [Guid]::Empty
    if ([Guid]::TryParse($guidText, [ref]$guid) -and $guid -ne [Guid]::Empty) {
        if ($UsbStorageTarget -and $gptType.Trim('{}').ToLowerInvariant() -cne 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7') { throw 'USB root target must be GPT basic data; no formatting was performed.' }
        return [ordered]@{style='gpt'; partition_guid=$guid.ToString().ToLowerInvariant(); offset_bytes=$offset; size_bytes=$size}
    }
    if (-not $UsbStorageTarget -or -not [string]::IsNullOrWhiteSpace($gptType) -or -not [string]::IsNullOrWhiteSpace($guidText)) { throw 'Partition GUID unavailable/invalid. Review disk format; no layout was changed.' }
    foreach ($field in @('MbrType','DiskNumber','PartitionNumber')) {
        $property = $Partition.PSObject.Properties[$field]
        if ($null -eq $property -or ($property.Value -isnot [int] -and $property.Value -isnot [long] -and $property.Value -isnot [uint16] -and $property.Value -isnot [uint32] -and $property.Value -isnot [uint64])) { throw "Unknown MBR partition identity: $field" }
    }
    if ($Partition.MbrType -ne 7 -or $Partition.PartitionNumber -lt 1 -or $Partition.PartitionNumber -gt 4 -or $Partition.DiskNumber -lt 0) { throw 'MBR USB target must be a primary type 07 data partition; extended/hidden/system layouts are not supported.' }
    $index = [long]$Partition.DiskNumber
    $disks = @(Get-CimInstance -ClassName Win32_DiskDrive -Filter "Index=$index")
    if ($disks.Count -ne 1 -or $disks[0].Index -ne $index) { throw 'MBR disk signature is unavailable or ambiguous.' }
    $signature = $disks[0].Signature
    if (($signature -isnot [int] -and $signature -isnot [long] -and $signature -isnot [uint32] -and $signature -isnot [uint64]) -or $signature -le 0 -or $signature -ge 4294967295) { throw 'MBR disk signature is unavailable, zero, or invalid; no signature was changed.' }
    return [ordered]@{style='mbr'; disk_signature=('{0:x8}' -f [uint32]$signature); partition_number=[int]$Partition.PartitionNumber; offset_bytes=$offset; size_bytes=$size}
}
