# Read-only environment report; does not request elevation or change settings.
param([string]$Volume = '')
$ErrorActionPreference = 'Stop'
$secure = [ordered]@{state='unknown';reason=''}
try { $secure.state = if (Confirm-SecureBootUEFI) { 'enabled' } else { 'disabled' } }
catch { $secure.reason = '非管理者権限または環境の制約で取得できません。Fedora側で確認できます。' }
$startup = $null
try { $startup = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled).HiberbootEnabled } catch { }
$volumes = @()
foreach ($item in @(Get-Volume | Where-Object { $_.DriveLetter })) {
    $entry = [ordered]@{drive=([string]$item.DriveLetter+':');filesystem=$item.FileSystem;size_bytes=$item.Size;free_bytes=$item.SizeRemaining}
    try {
        $part = Get-Partition -DriveLetter $item.DriveLetter
        $entry.partition = [ordered]@{disk_number=$part.DiskNumber;partition_number=$part.PartitionNumber;guid=[string]$part.Guid;gpt_type=[string]$part.GptType;mbr_type=$part.MbrType;is_boot=$part.IsBoot;is_system=$part.IsSystem;is_hidden=$part.IsHidden}
    } catch { $entry.partition = '取得できませんでした' }
    $volumes += [pscustomobject]$entry
}
$observation = $null
if ($Volume -match '^[A-Za-z]:$') { $observation = & (Join-Path $PSScriptRoot 'get-shell-bitlocker.ps1') -Volume $Volume }
[ordered]@{hardware_uuid=(Get-CimInstance Win32_ComputerSystemProduct).UUID;secure_boot=$secure;fast_startup_setting=$startup;volumes=$volumes;selected_drive_bitlocker=$observation;note='観測情報だけです。保存先の登録や書込み許可ではありません。'} | ConvertTo-Json -Depth 8
