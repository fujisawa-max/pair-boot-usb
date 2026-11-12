# Headless child: JSON stdin only; fixed scripts and structured splatting.
param([string]$ToolsPath = $(if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\tools')) { Join-Path $PSScriptRoot '..\tools' } else { Join-Path $PSScriptRoot '..\toolkit' }))
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line -or $line.Length -gt 65536) { throw '操作要求が空か、大きすぎます。' }
    $request = $line | ConvertFrom-Json
    $base = [IO.Path]::GetFullPath($ToolsPath)
    $parameters = @{}
    switch ([string]$request.operation) {
        'Environment' { $scriptName = 'get-windows-environment.ps1'; $parameters = @{Volume=[string]$request.volume} }
        { $_ -in @('Inventory','ReviewedInventory') } {
            $scriptName = if ($request.operation -eq 'ReviewedInventory') { 'export-inventory.ps1' } else { 'export-standard-inventory.ps1' }
            $parameters = @{Volume=[string]$request.volume;StorageDirectory=[string]$request.storage_directory;OutputFile=[string]$request.output_file}
            if ($request.personal_os_volume -isnot [bool] -or $request.usb_storage_target -isnot [bool]) { throw '確認フラグが不正です。' }
            if ($request.personal_os_volume) {
                $parameters.PersonalUseOsVolume = $true
                $parameters.PersonalUseConfirmation = [string]$request.personal_confirmation
            }
            if ($request.usb_storage_target) {
                if ($request.operation -eq 'ReviewedInventory') { throw '管理者向け確認ではUSB保存先を扱いません。通常の候補収集を使用してください。' }
                $parameters.UsbStorageTarget = $true
            }
        }
        'ShellObservation' {
            $scriptName = 'save-shell-bitlocker.ps1'
            $parameters = @{Volume=[string]$request.volume;OutputFile=[string]$request.output_file}
            if ($request.usb_storage_target -isnot [bool]) { throw 'USB指定が不正です。' }
            if ($request.usb_storage_target) { $parameters.UsbStorageTarget = $true }
        }
        { $_ -in @('Status','Collect','Merge','Deploy','Convert','Name') } {
            $scriptName = 'fleet.ps1'
            $parameters = @{Action=[string]$request.operation}
            if ($request.operation -in @('Status','Merge','Deploy','Convert','Name')) { $parameters.Inputs = [string[]]@($request.inputs) }
            if ($request.operation -in @('Collect','Merge','Deploy') -and $request.usb_root) { $parameters.UsbRoot = [string]$request.usb_root }
            if ($request.operation -in @('Collect','Merge','Convert','Name')) { $parameters.OutputFile = [string]$request.output_file }
            if ($request.operation -in @('Merge','Convert','Name')) { $parameters.Generation = [int]$request.generation }
            if ($request.operation -eq 'Name') {
                if ($null -ne $request.PSObject.Properties['name_updates']) {
                    if ($request.name_updates -isnot [array] -or $request.PSObject.Properties['host_uuid'] -or $request.PSObject.Properties['computer_name']) {throw 'Invalid PC name update batch.'}
                    $parameters.NameUpdates=[object[]]@($request.name_updates)
                } else {$parameters.HostUuid=[string]$request.host_uuid;$parameters.ComputerName=[string]$request.computer_name}
            }
            if ($request.operation -eq 'Status') { $parameters.AsJson=$true }
        }
        default { throw '対応していない操作です。' }
    }
    $global:LASTEXITCODE = 0
    & (Join-Path $base $scriptName) @parameters
    if (-not $?) { if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1 }
} catch {
    [Console]::Error.WriteLine('操作を完了できませんでした: ' + $_.Exception.Message)
    exit 1
}
