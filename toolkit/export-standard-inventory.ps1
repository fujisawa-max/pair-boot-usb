# Read-only candidate inventory for a standard Windows user. No elevation requested.
param(
    [switch]$ShowDialogs,
    [switch]$UsbStorageTarget,
    [ValidateRange(16777216,1099511627776)][long]$RootImageSizeBytes = 6442450944,
    [switch]$PersonalUseOsVolume,
    [string]$PersonalUseConfirmation,
    [ValidatePattern('^[A-Za-z]:$')][string]$Volume = 'D:',
    [Parameter(Mandatory=$true)][string]$OutputFile,
    [string]$BitlockerObservation,
    [ValidateScript({ $_ -is [string] -and $_ -cmatch '^[A-Za-z][A-Za-z0-9._-]{0,63}\z' -and -not $_.EndsWith('.') -and ($_.Split('.')[0] -inotmatch '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]|Windows|Users|ProgramData|Boot|Recovery|EFI|PerfLogs|MSOCache)$') })][string]$StorageDirectory = 'GPU-Linux'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
try {
if ($UsbStorageTarget -and $PersonalUseOsVolume) { throw 'USB target must not use the personal OS volume exception.' }
if (Test-Path -LiteralPath $OutputFile) { throw 'Choose a new output filename.' }
$hardware = ([Guid]::Parse((Get-CimInstance Win32_ComputerSystemProduct).UUID)).ToString().ToLowerInvariant()
if ($hardware -eq [Guid]::Empty.ToString() -or $hardware -eq 'ffffffff-ffff-ffff-ffff-ffffffffffff') { throw 'Invalid PC UUID.' }
$letter = $Volume.Substring(0,1)
$partitions = @(Get-Partition -DriveLetter $letter)
$volumes = @(Get-Volume -DriveLetter $letter)
if ($partitions.Count -ne 1 -or $volumes.Count -ne 1) { throw 'Selected drive must resolve uniquely.' }
$partition = $partitions[0]
$volumeInfo = $volumes[0]
if ($volumeInfo.FileSystem -cne 'NTFS') { throw 'Selected volume must be NTFS.' }
$hiddenPending = $false
$hidden = $false
foreach ($name in @('IsBoot','IsSystem','IsHidden')) {
    $property = $partition.PSObject.Properties[$name]
    if ($name -eq 'IsHidden' -and $UsbStorageTarget -and ($null -eq $property -or $null -eq $property.Value)) {
        $hiddenPending = $true; $hidden = $null; continue
    }
    if ($null -eq $property) { throw "Unknown partition flag: $name (property missing)" }
    $flag = $property.Value
    if ($flag -isnot [bool]) {
        $observed = if ($null -eq $flag) { '<null>' } else { [string]$flag }
        $type = if ($null -eq $flag) { '<null>' } else { $flag.GetType().FullName }
        throw "Unknown partition flag: $name (value=$observed; type=$type)"
    }
    if ($name -ne 'IsBoot' -and $flag) { throw "Unsafe partition flag: $name=True" }
}
. (Join-Path $PSScriptRoot 'volume-identity.ps1')
$identity = Get-RootPartitionIdentity -Partition $partition -UsbStorageTarget:$UsbStorageTarget
$guid = if ($identity.style -eq 'gpt') { $identity.partition_guid } else { $null }
if ($UsbStorageTarget -and $partition.IsBoot) { throw 'USB root target must be a data partition, not an OS volume.' }
$capacity = $null
if ($UsbStorageTarget) {
    $freeProperty = $volumeInfo.PSObject.Properties['SizeRemaining']
    if ($null -eq $freeProperty) { throw 'Free space is unknown. SizeRemaining property is missing.' }
    $freeValue = $freeProperty.Value
    if ($freeValue -isnot [long] -and $freeValue -isnot [int] -and $freeValue -isnot [uint64] -and $freeValue -isnot [uint32]) {
        $freeType = if ($null -eq $freeValue) { '<null>' } else { $freeValue.GetType().FullName }
        throw "Free space is unknown. Unsupported SizeRemaining type: $freeType"
    }
    if ($freeValue -is [uint64] -and $freeValue -gt [uint64][long]::MaxValue) { throw 'Free space is unknown. SizeRemaining exceeds supported byte range.' }
    $free = [long]$freeValue
    if ($free -lt 0) { throw 'Free space is unknown. Negative SizeRemaining is invalid.' }
    $required = $RootImageSizeBytes + [Math]::Max([long]1073741824, [long][Math]::Floor($RootImageSizeBytes / 10))
    $existingSize = $null
    $imagePath = Join-Path ($Volume + '\') ($StorageDirectory + '\root.img')
    if (Test-Path -LiteralPath $imagePath) {
        $image = Get-Item -LiteralPath $imagePath
        if ($image.PSIsContainer -or ($image.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Existing root.img is not an ordinary file.' }
        $existingSize = [long]$image.Length
        if ($existingSize -ne $RootImageSizeBytes) { throw 'Existing root.img size differs; never overwrite it.' }
        $required = 0
    }
    if ($free -lt 0 -or $free -lt $required) { throw "Insufficient USB space: required=$required bytes; available=$free bytes. root.img=$RootImageSizeBytes bytes plus reserve; models need additional space." }
    $capacity = [ordered]@{root_image_bytes=$RootImageSizeBytes; available_bytes=$free; existing_image_bytes=$existingSize; required_bytes=$required}
}
$power = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled
if ($power.HiberbootEnabled -isnot [int] -or $power.HiberbootEnabled -notin @(0,1)) { throw 'Fast Startup observation is unknown.' }
$checkedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if ($BitlockerObservation) {
    $shellEvidence = [IO.File]::ReadAllText([IO.Path]::GetFullPath($BitlockerObservation)) | ConvertFrom-Json
    if ($shellEvidence.kind -cne 'bitlocker-shell-observation' -or $shellEvidence.hardware_uuid -ine $hardware -or ($identity.style -eq 'gpt' -and $shellEvidence.partition_guid -ine $guid)) { throw 'Saved Shell observation does not match this PC and partition.' }
    if ($identity.style -eq 'mbr') {
        $savedProperty = $shellEvidence.PSObject.Properties['partition_identity']
        if ($null -eq $savedProperty -or $null -eq $savedProperty.Value -or @($savedProperty.Value.PSObject.Properties).Count -ne $identity.Count) { throw 'Saved Shell observation does not match this MBR partition.' }
        foreach ($key in $identity.Keys) {
            if ($savedProperty.Value.$key -cne $identity[$key]) { throw 'Saved Shell observation does not match this MBR partition.' }
        }
    }
} else {
    $observation = & (Join-Path $PSScriptRoot 'get-shell-bitlocker.ps1') -Volume $Volume
    $shellEvidence = [ordered]@{
        schema = 1
        kind = 'bitlocker-shell-observation'
        hardware_uuid = $hardware
        partition_guid = $guid
        checked_at_unix = $checkedAt
        observation = $observation
    }
    if ($identity.style -eq 'mbr') { $shellEvidence.partition_identity = $identity }
}
$value = [ordered]@{
    schema = 1
    kind = 'windows-standard-candidate'
    hardware_uuid = $hardware
    windows_drive = $Volume.ToUpperInvariant()
    filesystem = 'NTFS'
    partition_guid = $guid
    partition_offset_bytes = [long]$partition.Offset
    partition_size_bytes = [long]$partition.Size
    volume_size_bytes = [long]$volumeInfo.Size
    partition_is_boot = [bool]$partition.IsBoot
    partition_is_system = $false
    partition_is_hidden = $hidden
    fast_startup_enabled = [bool]$power.HiberbootEnabled
    storage_directory = $StorageDirectory
    checked_at_unix = $checkedAt
    bitlocker_observation = $shellEvidence
    bitlocker_volume_status = 'Unknown'
    bitlocker_protection = 'Unknown'
    secure_boot_enabled = $null
}
if ($UsbStorageTarget) {
    $value.usb_target_requested = $true
    $value.partition_identity = $identity
    $value.partition_flags_pending = $hiddenPending
    $value.capacity_observation = $capacity
}
if ($partition.IsBoot -or $PersonalUseOsVolume) {
    if (-not $PersonalUseOsVolume -or $PersonalUseConfirmation -cne '個人利用で許可') { throw 'OS volume requires -PersonalUseOsVolume and explicit -PersonalUseConfirmation. Windows data loss/boot failure risk; back up first.' }
    $value.personal_os_volume_consent = [ordered]@{
        policy = 'personal-os-v1'; hardware_uuid = $hardware
        target = $guid; storage_directory = $StorageDirectory
    }
}
$bytes = [Text.UTF8Encoding]::new($false).GetBytes(($value | ConvertTo-Json -Depth 8) + "`n")
$stream = [IO.File]::Open($OutputFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
$messages = @(
    ('候補情報を保存しました: ' + [IO.Path]::GetFullPath($OutputFile)),
    ("対象: $Volume\$StorageDirectory（登録候補）"),
    '次はFedoraで保存先の識別情報、NTFS、Secure Boot、BitLockerの観測情報を照合して登録してください。',
    '保存先としての登録はまだ完了していません。この操作ではWindowsの設定変更やroot.imgの展開は行っていません。'
)
if ($UsbStorageTarget) {
    $messages += 'USBのGPT/MBR情報と空き容量もFedoraで照合します。実際の空き容量は初回コピー直前にも再確認します。'
}
$successMessage = $messages -join "`r`n"
Write-Output $successMessage
if ($ShowDialogs) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][Windows.Forms.MessageBox]::Show($successMessage, 'GPU Linux: 候補情報を保存しました', 'OK', 'Information')
    } catch {
        [Console]::Error.WriteLine('候補JSONは保存済みですが、案内ダイアログを表示できませんでした。上記の保存先と次の手順を確認してください。')
    }
}

} catch {
    $detail = $_.Exception.Message
    $reason = switch -Wildcard ($detail) {
        '*MBR disk signature*' { 'MBR形式USBのディスク署名を取得できないか、値が無効です。領域を安全に特定できないため受付できません。Win32_DiskDriveのSignatureを確認してください。署名の変更やフォーマットはしていません。'; break }
        '*Unknown MBR partition identity*' { 'MBR領域の識別情報を取得できません。Get-PartitionのMbrType/DiskNumber/PartitionNumberを確認してください。'; break }
        '*MBR USB target must*' { '今回対応するMBR保存先は、通常のNTFSプライマリ領域（type 07）です。拡張・論理・非表示・特殊領域は受付できません。'; break }
        '*USB GPT identity unavailable*' { 'USBのGPTパーティション情報を取得できません。MBR形式か、形式が未確認の可能性があります。この版のUSB保存先はGPTの通常データ領域に対応しています。NTFSへのフォーマットだけではGPT形式にはなりません。Get-PartitionのType/GptType/MbrType/Guidを確認してください。形式変換や再フォーマットは行っていません。'; break }
        '*Partition GUID unavailable/invalid*' { '保存先のGPTパーティションGUIDを取得できません。この版ではGUIDが必要です。MBR形式か情報を取得できない可能性があるため、領域の形式を確認してください。'; break }
        '*Insufficient USB space*' { 'USBの空き容量が不足しています。現在の6GiB root.imgには初回コピー時に合計7GiB以上の空きが必要です（モデル分は別）。より大きいUSB/SSDまたは別の保存先を選択してください。'; break }
        '*Free space is unknown*' { 'USBの空き容量を確認できないため、保存先を受け入れられません。'; break }
        '*USB root target must be GPT basic data*' { '通常のGPTデータ領域であることを確認できません。EFI/回復/予約領域は保存先にできません。'; break }
        '*USB target must not use*' { 'USBのデータ領域では個人利用OS領域の例外は不要です。-PersonalUseOsVolume と -PersonalUseConfirmation を外し、-UsbStorageTarget を指定してください。'; break }
        '*Fast Startup*' { '高速スタートアップの設定値を確認できないため、観測情報を保存できません。'; break }
        '*HiberbootEnabled*' { '高速スタートアップの状態を確認できないため、この保存先を受け入れられません。'; break }
        '*Unsafe partition flag: IsHidden=True*' { 'Windowsがこのパーティションを非表示領域（IsHidden=True）と報告しています。NTFS内の隠しファイルとは別の属性です。安全のため保存先として受け入れません。ドライブ文字が付いていても、この属性の検査は省略しません。'; break }
        '*Unsafe partition flag: IsSystem=True*' { 'Windowsの起動に使用するシステム領域です。個人利用の確認があっても保存先として受け入れません。'; break }
        '*Unknown partition flag:*' { 'パーティションの安全属性を取得できないか、値の形式が想定と異なります。非表示領域と断定はできませんが、安全性を確認できないため保存先として受け入れません。Get-Partitionで詳細を確認してください。'; break }
        '*OS volume requires*' { 'WindowsのOS領域です。個人利用の明示確認がないため、この保存先は利用できません。'; break }
        '*Selected volume must be NTFS*' { '選択した保存先がNTFSではありません。現在のツールはNTFSだけに対応しています。'; break }
        '*Choose a new output filename*' { '出力先ファイルがすでに存在します。上書きを避けるため、新しいファイル名を指定してください。'; break }
        '*Shell observation does not match*' { 'BitLockerの確認情報が、このPCまたは選択した領域と一致しません。対応する確認情報を用意してください。'; break }
        default { '必要な情報を確認できなかったため、処理を中止しました。' }
    }
    $message = "受付できませんでした（対象: $Volume）。`r`n$reason`r`nWindowsの設定変更やLinux用rootイメージの展開は行っていません。`r`n詳細: $detail"
    [Console]::Error.WriteLine($message)
    if ($ShowDialogs) {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            [void][Windows.Forms.MessageBox]::Show($message, 'GPU Linux: 受付できません', 'OK', 'Warning')
        } catch { [Console]::Error.WriteLine('ダイアログを表示できませんでした。上記の説明を確認してください。') }
    }
    exit 1
}
