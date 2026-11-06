# Windows Forms frontend for the existing CLI; safety checks remain in the CLI.
param(
    [string]$ToolsPath = $(if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\tools')) { Join-Path $PSScriptRoot '..\tools' } else { Join-Path $PSScriptRoot '..\toolkit' }),
    [switch]$PlanOnly,
    [switch]$UsbStorageTarget,
    [switch]$PersonalUseOsVolume,
    [string]$PersonalUseConfirmation,
    [ValidateSet('Inventory','ShellObservation','Status','Collect','Merge','Deploy','Environment','ReviewedInventory')][string]$Operation = 'Inventory',
    [string]$Volume = 'D:',
    [string]$StorageDirectory = 'GPU-Linux',
    [string]$UsbRoot = 'F:\',
    [string[]]$Inputs = @(),
    [string]$OutputFile = 'F:\inventory-pc01.json',
    [int]$Generation = 1
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function New-OperationPlan {
    param([string]$Action, [string]$Drive, [string]$Folder, [string]$Usb,
          [string[]]$Files, [string]$Output, [int]$NewGeneration, [bool]$Personal = $false, [string]$Confirmation = '', [bool]$UsbStorage = $false)
    $base = [IO.Path]::GetFullPath($ToolsPath)
    $command = @('powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File')
    switch ($Action) {
        'Environment' { $command += @((Join-Path $base 'get-windows-environment.ps1'),'-Volume',$Drive) }
        { $_ -in @('Inventory','ReviewedInventory') } {
            if ($Drive -notmatch '^[A-Za-z]:$') { throw '一覧からドライブを選択してください。' }
            $metadata = Get-Command (Join-Path $base 'export-standard-inventory.ps1')
            $predicate = @($metadata.Parameters['StorageDirectory'].Attributes | Where-Object { $_ -is [System.Management.Automation.ValidateScriptAttribute] })[0].ScriptBlock
            if (-not ($Folder | ForEach-Object $predicate)) { throw '保存先フォルダ名が無効です。' }
            $scriptName = if ($Action -eq 'ReviewedInventory') { 'export-inventory.ps1' } else { 'export-standard-inventory.ps1' }
            $command += @((Join-Path $base $scriptName),'-Volume',$Drive,'-StorageDirectory',$Folder,'-OutputFile',$Output)
        }
        'ShellObservation' {
            if ($Drive -notmatch '^[A-Za-z]:$') { throw '一覧からドライブを選択してください。' }
            $command += @((Join-Path $base 'save-shell-bitlocker.ps1'),'-Volume',$Drive,'-OutputFile',$Output)
        }
        'Status' {
            if ($Files.Count -ne 1) { throw '台帳JSONを1つ選択してください。' }
            $command += @((Join-Path $base 'fleet.ps1'),'-Action','Status','-Inputs',$Files[0])
        }
        'Collect' {
            if (-not $Usb) { throw '設定USBを選択してください。' }
            $command += @((Join-Path $base 'fleet.ps1'),'-Action','Collect','-UsbRoot',$Usb,'-OutputFile',$Output)
        }
        'Merge' {
            if ($Files.Count -lt 2 -or $NewGeneration -lt 1) { throw '複数の台帳と世代番号を指定してください。' }
            # A future executor must pass arrays by structured PowerShell invocation,
            # not join filenames into a command string or pass a native -File array.
            $command += @((Join-Path $base 'fleet.ps1'),'-Action','Merge','-Inputs')
            $command += $Files
            $command += @('-Generation',[string]$NewGeneration,'-OutputFile',$Output)
        }
        'Deploy' {
            if ($Files.Count -ne 1 -or -not $Usb) { throw '統合台帳と設定USBを選択してください。' }
            $command += @((Join-Path $base 'fleet.ps1'),'-Action','Deploy','-Inputs',$Files[0],'-UsbRoot',$Usb)
        }
        default { throw 'Unknown operation.' }
    }
    if ($UsbStorage) {
        if ($Action -notin @('Inventory','ShellObservation') -or $Personal) { throw 'USB保存先と個人利用OS領域の例外は併用できません。' }
        $command += '-UsbStorageTarget'
    }
    if ($Personal) {
        if ($Action -notin @('Inventory','ReviewedInventory') -or $Confirmation -cne '個人利用で許可') { throw '個人利用の確認が必要です。' }
        $command += @('-PersonalUseOsVolume','-PersonalUseConfirmation',$Confirmation)
    }
    return [pscustomobject][ordered]@{
        prototype = $false
        execution_enabled = $true
        operation = $Action
        argv = @($command)
        required_checks = @('実機受入前','既存CLIの安全性・競合・版照合を維持','保存/配布前に最終確認')
        request = [ordered]@{operation=$Action;volume=$Drive;storage_directory=$Folder;usb_root=$Usb;inputs=@($Files);output_file=$Output;generation=$NewGeneration;personal_os_volume=$Personal;personal_confirmation=$Confirmation;usb_storage_target=$UsbStorage}
    }
}
if ($PlanOnly) {
    New-OperationPlan $Operation $Volume $StorageDirectory $UsbRoot $Inputs $OutputFile $Generation ([bool]$PersonalUseOsVolume) $PersonalUseConfirmation ([bool]$UsbStorageTarget) | ConvertTo-Json -Depth 6
    return
}
function Invoke-GuiOperation($Request) {
    $worker = (Join-Path $PSScriptRoot 'windows-worker.ps1').Replace("'", "''")
    $base = ([IO.Path]::GetFullPath($ToolsPath)).Replace("'", "''")
    $code = "& '$worker' -ToolsPath '$base'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = (Join-Path $PSHOME 'powershell.exe')
    if (-not (Test-Path -LiteralPath $info.FileName)) { $info.FileName = (Join-Path $PSHOME 'pwsh.exe') }
    $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true; $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        $inputBytes = [Text.UTF8Encoding]::new($false).GetBytes(($Request | ConvertTo-Json -Depth 8 -Compress) + "`n")
        $process.StandardInput.BaseStream.Write($inputBytes,0,$inputBytes.Length); $process.StandardInput.BaseStream.Flush(); $process.StandardInput.Close()
        while (-not $process.HasExited) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 50 }
        $process.WaitForExit()
        return [pscustomobject]@{exit_code=$process.ExitCode;text=($stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult()).Trim()}
    } finally { $process.Dispose() }
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$form = [Windows.Forms.Form]::new()
$form.Text = 'GPU Linux 管理GUI 0.7.5'
$form.ClientSize = [Drawing.Size]::new(650,440)
$form.AutoScaleMode = 'Dpi'
$form.MinimumSize = [Drawing.Size]::new(666,479)
$form.Font = [Drawing.Font]::new('Segoe UI',11)
$form.StartPosition = 'CenterScreen'
function Label-At([string]$Text,[int]$Y) {
    $label = [Windows.Forms.Label]::new()
    $label.Text = $Text; $label.Location = [Drawing.Point]::new(16,$Y)
    $label.Size = [Drawing.Size]::new(170,28); $form.Controls.Add($label)
}
function Combo-At([int]$Y) {
    $combo = [Windows.Forms.ComboBox]::new()
    $combo.DropDownStyle = 'DropDownList'
    $combo.Location = [Drawing.Point]::new(185,$Y)
    $combo.Size = [Drawing.Size]::new(445,28); $form.Controls.Add($combo)
    return $combo
}
Label-At '操作' 20
$actions = Combo-At 20
$actionCodes = @('Inventory','ShellObservation','Status','Collect','Merge','Deploy','Environment','ReviewedInventory')
$actions.Items.AddRange([object[]]@('候補情報を保存','BitLocker観測を保存','PC登録一覧（台帳）を表示','USBから台帳回収','台帳をマージ','USBへ台帳配布','環境確認（読取専用）','管理者向け確認情報を保存'))
$actions.SelectedIndex = 0
Label-At '保存先NTFSドライブ' 58
$drives = Combo-At 58
$drives.DisplayMember = 'Text'
Label-At '設定USB（GPU-LOGS）' 96
$usbs = Combo-At 96
$usbs.DisplayMember = 'Text'
try {
    foreach ($item in @(Get-Volume | Where-Object { $_.DriveLetter })) {
        $letter = [string]$item.DriveLetter + ':'
        $text = "$letter  $($item.FileSystemLabel)  $($item.FileSystem)  $([Math]::Round($item.Size/1GB,1)) GiB"
        if ($item.FileSystem -eq 'NTFS') {
            # Discovery is not authorization. Keep NTFS drives visible even when
            # partition metadata is unavailable; the existing CLI validates safety.
            $note = ''
            try {
                $part = Get-Partition -DriveLetter $item.DriveLetter
                if ($part.IsSystem -or $part.IsHidden) {
                    $note = ' [システム/非表示: 利用不可]'
                } elseif ($part.IsBoot) {
                    $note = ' [Windows OS: 個人利用の確認が必要]'
                } elseif ($part.IsHidden -isnot [bool]) {
                    $note = ' [属性未確認: USB指定ならFedoraで確認]'
                }
            } catch { $note = ' [パーティション情報未確認]' }
            $index = $drives.Items.Add([pscustomobject]@{Text=($text+$note);Volume=$letter})
            if ($letter -eq 'D:') { $drives.SelectedIndex = $index }
        }
        if ($item.FileSystemLabel -eq 'GPU-LOGS' -and $item.FileSystem -eq 'FAT32') {
            [void]$usbs.Items.Add([pscustomobject]@{Text=$text;Volume=($letter+'\')})
        }
    }
    if ($drives.SelectedIndex -lt 0 -and $drives.Items.Count -gt 0) { $drives.SelectedIndex = 0 }
    if ($usbs.Items.Count -eq 1) { $usbs.SelectedIndex = 0 }
} catch { [void][Windows.Forms.MessageBox]::Show('ドライブ一覧を取得できませんでした: '+$_.Exception.Message) }
Label-At '保存先フォルダ' 134
$folderBox = [Windows.Forms.TextBox]::new()
$folderBox.Text = 'GPU-Linux'; $folderBox.Location = [Drawing.Point]::new(185,134)
$folderBox.Size = [Drawing.Size]::new(445,28); $form.Controls.Add($folderBox)
Label-At 'マージ後の世代番号' 172
$generationBox = [Windows.Forms.NumericUpDown]::new()
$generationBox.Minimum = 1; $generationBox.Maximum = 1000000000; $generationBox.Value = 1
$generationBox.Location = [Drawing.Point]::new(185,172); $form.Controls.Add($generationBox)
$usbTargetBox = [Windows.Forms.CheckBox]::new()
$usbTargetBox.Text = 'USBをroot保存先に指定'
$usbTargetBox.Location = [Drawing.Point]::new(340,172); $usbTargetBox.Size = [Drawing.Size]::new(290,28)
$form.Controls.Add($usbTargetBox)
$actions.Add_SelectedIndexChanged({
    $usbTargetBox.Enabled = ($actionCodes[$actions.SelectedIndex] -in @('Inventory','ShellObservation'))
    if (-not $usbTargetBox.Enabled) { $usbTargetBox.Checked = $false }
})
$preview = [Windows.Forms.TextBox]::new()
$preview.Multiline = $true; $preview.ReadOnly = $true; $preview.ScrollBars = 'Both'
$preview.Location = [Drawing.Point]::new(16,258); $preview.Size = [Drawing.Size]::new(614,164)
$preview.Text = '操作と保存先を選択してください。確認後、既存CLIを実行して結果を表示します。Windows設定を自動変更することはありません。'
$form.Controls.Add($preview)
$button = [Windows.Forms.Button]::new()
$button.Text = '選択・確認して実行'; $button.Location = [Drawing.Point]::new(185,210)
$button.Size = [Drawing.Size]::new(220,35); $form.Controls.Add($button)
$button.Add_Click({
    try {
        $action = $actionCodes[$actions.SelectedIndex]
        $drive = if ($drives.SelectedItem) { $drives.SelectedItem.Volume } else { '' }
        $usb = if ($usbs.SelectedItem) { $usbs.SelectedItem.Volume } else { '' }
        $files = @(); $output = ''
        $personal = $false; $confirmation = ''
        if ($action -in @('Inventory','ReviewedInventory') -and $drive) {
            $selectedPartition = Get-Partition -DriveLetter $drive.Substring(0,1)
            if ($selectedPartition.IsSystem -or $selectedPartition.IsHidden) { throw 'システム/非表示領域は許可できません。' }
            if ($selectedPartition.IsBoot -isnot [bool]) { throw '起動領域の状態が不明です。' }
            if ($usbTargetBox.Checked -and $selectedPartition.IsBoot) { throw 'USB保存先にはOS領域を指定できません。' }
            if ($selectedPartition.IsBoot) {
                $approval = [Windows.Forms.Form]::new()
                $approval.Text = '警告: Windows OS領域への保存'
                $approval.ClientSize = [Drawing.Size]::new(580,300)
                $approval.Font = $form.Font; $approval.StartPosition = 'CenterParent'
                $warning = [Windows.Forms.Label]::new()
                $warning.Location = [Drawing.Point]::new(16,16); $warning.Size = [Drawing.Size]::new(548,190)
                $warning.Text = "対象: $drive\$($folderBox.Text)`r`nパーティション: $($selectedPartition.Guid)`r`nWindows領域をLinuxから読み書きします。障害や誤操作によりWindowsが起動できなくなったり、データが失われるおそれがあります。個人所有PCでバックアップ済みの場合だけ進めてください。BitLocker・休止状態等の検査は省略できません。`r`n続行する場合は「個人利用で許可」と入力してください。"
                $approval.Controls.Add($warning)
                $entry = [Windows.Forms.TextBox]::new(); $entry.Location = [Drawing.Point]::new(16,210)
                $entry.Size = [Drawing.Size]::new(548,28); $approval.Controls.Add($entry)
                $yes = [Windows.Forms.Button]::new(); $yes.Text = '確認'; $yes.DialogResult = 'OK'
                $yes.Location = [Drawing.Point]::new(350,252); $approval.Controls.Add($yes)
                $no = [Windows.Forms.Button]::new(); $no.Text = '取消'; $no.DialogResult = 'Cancel'
                $no.Location = [Drawing.Point]::new(460,252); $approval.Controls.Add($no)
                $approval.CancelButton = $no; $approval.AcceptButton = $no
                if ($approval.ShowDialog($form) -ne 'OK') { $approval.Dispose(); return }
                $confirmation = $entry.Text; $approval.Dispose()
                if ($confirmation -cne '個人利用で許可') { throw '確認入力が一致しません。操作を中止しました。' }
                $personal = $true
            }
        }

        if ($action -in @('Inventory','ReviewedInventory','ShellObservation') -and -not $drive) { throw 'NTFSドライブが見つかりません。Windowsでドライブ文字が付いたNTFS領域を確認してください。' }
        if ($action -in @('Status','Merge','Deploy')) {
            $picker = [Windows.Forms.OpenFileDialog]::new(); $picker.Filter = 'JSON (*.json)|*.json'
            $picker.Multiselect = ($action -eq 'Merge')
            if ($picker.ShowDialog($form) -ne 'OK') { return }
            $files = @($picker.FileNames)
        }
        if ($action -in @('Inventory','ReviewedInventory','ShellObservation','Collect','Merge')) {
            $picker = [Windows.Forms.SaveFileDialog]::new(); $picker.Title = '新しいJSONの保存先を選択'; $picker.Filter = 'JSON (*.json)|*.json'
            $picker.FileName = if ($action -eq 'Inventory') { 'inventory-pc01.json' } else { 'observation-or-registry.json' }
            if ($usb) { $picker.InitialDirectory = $usb }
            if ($picker.ShowDialog($form) -ne 'OK') { return }
            $output = $picker.FileName
        }
        $value = New-OperationPlan $action $drive $folderBox.Text $usb $files $output ([int]$generationBox.Value) $personal $confirmation ([bool]$usbTargetBox.Checked)
        $preview.Text = $value | ConvertTo-Json -Depth 6
        $summary = "操作: $action`r`n対象: $drive\$($folderBox.Text)`r`n設定USB: $usb`r`n入力: $($files -join ', ')`r`n出力: $output`r`nこの内容で実行しますか？"
        if ($action -notin @('Environment','Status') -and [Windows.Forms.MessageBox]::Show($form,$summary,'実行内容の確認','YesNo','Question','Button2') -ne 'Yes') { return }
        $button.Enabled = $false; $form.UseWaitCursor = $true
        $preview.Text = '実行中です。処理が終わるまでお待ちください。'
        $result = Invoke-GuiOperation $value.request
        $preview.Text = $result.text
        $title = if ($result.exit_code -eq 0) { '処理が完了しました' } else { '処理を完了できませんでした' }
        Show-ReadableMessage $result.text $title

    } catch { Show-ReadableMessage $_.Exception.Message '操作を確認してください' } finally { $button.Enabled = $true; $form.UseWaitCursor = $false }
})
$helpButton = [Windows.Forms.Button]::new()
$helpButton.Text = '使い方'; $helpButton.Location = [Drawing.Point]::new(16,210)
$helpButton.Size = [Drawing.Size]::new(155,35); $form.Controls.Add($helpButton)
$script:UsageWindow = $null
$helpButton.Add_Click({
    if ($null -ne $script:UsageWindow -and -not $script:UsageWindow.IsDisposed) {
        $script:UsageWindow.Activate(); return
    }
    try {
        $usage = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'usage.txt'), [Text.Encoding]::UTF8)
        $script:UsageWindow = [Windows.Forms.Form]::new()
        $script:UsageWindow.Text = 'GPU Linux — 使い方（操作を続けられます）'
        $script:UsageWindow.Font = [Drawing.Font]::new('Segoe UI',12)
        $script:UsageWindow.ClientSize = [Drawing.Size]::new(650,550)
        $script:UsageWindow.StartPosition = 'CenterScreen'
        $body = [Windows.Forms.TextBox]::new()
        $body.Multiline = $true; $body.ReadOnly = $true; $body.ScrollBars = 'Vertical'
        $body.Dock = 'Fill'; $body.Text = $usage.Replace("`r`n","`n").Replace("`n","`r`n")
        $script:UsageWindow.Controls.Add($body)
        $script:UsageWindow.Show($form)
    } catch { Show-ReadableMessage $_.Exception.Message '使い方を開けませんでした' }
})
$mok = [Windows.Forms.Button]::new(); $mok.Text = 'MOK操作について'
$mok.Location = [Drawing.Point]::new(415,210); $mok.Size = [Drawing.Size]::new(180,35)
$form.Controls.Add($mok)
function Show-ReadableMessage([string]$Text, [string]$Title) {
    $dialog = [Windows.Forms.Form]::new()
    $dialog.Text = $Title; $dialog.Font = [Drawing.Font]::new('Segoe UI',12)
    $dialog.AutoScaleMode = 'Dpi'; $dialog.ClientSize = [Drawing.Size]::new(510,220)
    $dialog.StartPosition = 'CenterParent'
    $body = [Windows.Forms.TextBox]::new()
    $body.Multiline = $true; $body.ReadOnly = $true; $body.BorderStyle = 'None'
    $body.Text = $Text; $body.Location = [Drawing.Point]::new(18,18)
    $body.ScrollBars = 'Vertical'; $body.Size = [Drawing.Size]::new(474,145); $dialog.Controls.Add($body)
    $close = [Windows.Forms.Button]::new(); $close.Text = '閉じる'
    $close.Location = [Drawing.Point]::new(390,175); $close.Size = [Drawing.Size]::new(100,32)
    $close.DialogResult = 'OK'; $dialog.Controls.Add($close)
    $dialog.AcceptButton = $close; $dialog.CancelButton = $close
    [void]$dialog.ShowDialog($form); $dialog.Dispose()
}
$mok.Add_Click({ Show-ReadableMessage 'MOKの証明書確認・登録申請・削除申請はFedora側のGPU Linux管理GUIで行えます。Windowsからは申請しません。最終承認は再起動後のUEFI MokManagerで所有者が行います。' 'MOK操作について' })
$form.Add_FormClosing({ if (-not $button.Enabled) { $_.Cancel = $true } })
[void]$form.ShowDialog()
