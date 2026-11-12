# Windows Forms frontend for the existing CLI; safety checks remain in the CLI.
param(
    [string]$ToolsPath = $(if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\tools')) { Join-Path $PSScriptRoot '..\tools' } else { Join-Path $PSScriptRoot '..\toolkit' }),
    [switch]$PlanOnly,
    [switch]$UsbStorageTarget,
    [switch]$PersonalUseOsVolume,
    [string]$PersonalUseConfirmation,
    [ValidateSet('Inventory','ShellObservation','Status','Collect','Merge','Deploy','Environment','ReviewedInventory','AdminBitLocker','Convert','Name')][string]$Operation = 'Inventory',
    [string]$Volume = 'D:',
    [string]$StorageDirectory = 'GPU-Linux',
    [string]$UsbRoot = 'F:\',
    [string[]]$Inputs = @(),
    [string]$OutputFile = 'F:\data\inventory-pc01.json',
    [int]$Generation = 1,
    [string]$HostUuid = '',
    [string]$ComputerName = '',
    [object[]]$NameUpdates = @()
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function New-OperationPlan {
    param([string]$Action, [string]$Drive, [string]$Folder, [string]$Usb,
          [string[]]$Files, [string]$Output, [int]$NewGeneration, [bool]$Personal = $false, [string]$Confirmation = '', [bool]$UsbStorage = $false, [string]$SelectedHost = '', [string]$PcName = '', [object[]]$PcNameUpdates = @())
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
        'AdminBitLocker' {
            if ($Drive -notmatch '^[A-Za-z]:$') { throw 'NTFSドライブを選択してください。' }
            $command += @((Join-Path $base 'save-admin-bitlocker.ps1'),'-Volume',$Drive,'-OutputFile',$Output)
        }
        'ShellObservation' {
            if ($Drive -notmatch '^[A-Za-z]:$') { throw '一覧からドライブを選択してください。' }
            $command += @((Join-Path $base 'save-shell-bitlocker.ps1'),'-Volume',$Drive,'-OutputFile',$Output)
        }
        { $_ -in @('Convert','Name') } {
            if ($Files.Count -ne 1 -or $NewGeneration -lt 1) { throw 'Select one registry and a new generation.' }
            $command += @((Join-Path $base 'fleet.ps1'),'-Action',$Action,'-Inputs',$Files[0],'-Generation',[string]$NewGeneration,'-OutputFile',$Output)
            if ($Action -eq 'Name') {
                if ($PcNameUpdates.Count) {$command += '-NameUpdates';$command += $PcNameUpdates}
                else {$command += @('-HostUuid',$SelectedHost,'-ComputerName',$PcName)}
            }
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
            if ($Usb) { $command += @('-UsbRoot',$Usb) }
        }
        'Deploy' {
            if ($Files.Count -ne 1 -or -not $Usb) { throw '統合台帳と設定USBを選択してください。' }
            $command += @((Join-Path $base 'fleet.ps1'),'-Action','Deploy','-Inputs',$Files[0],'-UsbRoot',$Usb)
        }
        default { throw 'Unknown operation.' }
    }
    if ($UsbStorage) {
        if ($Action -notin @('Inventory','ShellObservation','AdminBitLocker') -or $Personal) { throw 'USB保存先と個人利用OS領域の例外は併用できません。' }
        if ($Action -ne 'AdminBitLocker') { $command += '-UsbStorageTarget' }
    }
    if ($Personal) {
        if ($Action -notin @('Inventory','ReviewedInventory') -or $Confirmation -cne '個人利用で許可') { throw '個人利用の確認が必要です。' }
        $command += @('-PersonalUseOsVolume','-PersonalUseConfirmation',$Confirmation)
    }
    $request = [ordered]@{operation=$Action}
    switch ($Action) {
        'Environment' { $request.volume=$Drive }
        { $_ -in @('Inventory','ReviewedInventory','ShellObservation','AdminBitLocker') } {
            $request.volume=$Drive; $request.output_file=$Output
            if ($Action -in @('Inventory','ReviewedInventory')) { $request.storage_directory=$Folder; $request.personal_os_volume=$Personal; $request.personal_confirmation=$Confirmation; $request.usb_storage_target=$UsbStorage }
            if ($Action -eq 'ShellObservation') { $request.usb_storage_target=$UsbStorage }
        }
        default {
            if ($Action -in @('Status','Merge','Deploy','Convert','Name')) { $request.inputs=@($Files) }
            if ($Action -in @('Collect','Deploy') -or ($Action -eq 'Merge' -and $Usb)) { $request.usb_root=$Usb }
            if ($Action -in @('Collect','Merge','Convert','Name')) { $request.output_file=$Output }
            if ($Action -in @('Merge','Convert','Name')) { $request.generation=$NewGeneration }
            if ($Action -eq 'Name') {
                if ($PcNameUpdates.Count) {$request.name_updates=@($PcNameUpdates)}
                else {$request.host_uuid=$SelectedHost;$request.computer_name=$PcName}
            }
        }
    }
    return [pscustomobject][ordered]@{
        prototype = $false
        execution_enabled = $true
        operation = $Action
        argv = @($command)
        required_checks = @('実機受入前','既存CLIの安全性・競合・版照合を維持','保存/配布前に最終確認')
        request = $request
    }
}
if ($PlanOnly) {
    New-OperationPlan $Operation $Volume $StorageDirectory $UsbRoot $Inputs $OutputFile $Generation ([bool]$PersonalUseOsVolume) $PersonalUseConfirmation ([bool]$UsbStorageTarget) $HostUuid $ComputerName $NameUpdates | ConvertTo-Json -Depth 6
    return
}
function Get-GuiInputDirectory([string]$Action,[string]$UsbPath,[string]$GuiDirectory=$PSScriptRoot) {
    if (-not $UsbPath) {
        $parent=Split-Path -Parent $GuiDirectory
        if (Test-Path -LiteralPath (Join-Path $parent 'usb-config.json') -PathType Leaf) {$UsbPath=$parent}
    }
    if (-not $UsbPath) {return ''}
    $folder=if ($Action -in @('Status','Merge','Deploy','Convert','Name')) {'gpu-config'} else {'data'}
    $directory=Join-Path $UsbPath $folder
    if (Test-Path -LiteralPath $directory -PathType Container) {return $directory}
    return $UsbPath
}
function Get-GuiOutputDirectory([string]$Action,[string]$UsbPath,[string]$GuiDirectory=$PSScriptRoot) {
    if ($Action -in @('Merge','Convert','Name')) {
        return Get-GuiInputDirectory $Action $UsbPath $GuiDirectory
    }
    if (-not $UsbPath) {
        $parent=Split-Path -Parent $GuiDirectory
        if (Test-Path -LiteralPath (Join-Path $parent 'usb-config.json') -PathType Leaf) {$UsbPath=$parent}
    }
    return Get-CollectionDirectory $UsbPath
}
function Get-GuiOperationResult([int]$ExitCode,[string]$StandardOutput,[string]$StandardError) {
    # Only stdout is the operation payload. EncodedCommand may serialize
    # progress/diagnostics to stderr; those bytes must never enter JSON parsing.
    $text=$StandardOutput.Trim()
    if ($ExitCode -ne 0) {
        if ($StandardError.Trim() -and $StandardError -notmatch '(?s)^\s*#?<\s*CLIXML') {$text=$StandardError.Trim()}
        elseif ($StandardError -match '(?s)^\s*#?<\s*CLIXML' -or -not $text) {$text=T 'failure'}
    }
    return [pscustomobject]@{
        exit_code=$ExitCode
        text=$text
        diagnostics=("Exit code: $ExitCode`r`n--- stdout ---`r`n$StandardOutput`r`n--- stderr ---`r`n$StandardError")
    }
}
function Invoke-GuiOperation($Request) {
    if ($Request.operation -in @('AdminBitLocker','ReviewedInventory')) {
        if ($Request.volume -notmatch '^[A-Za-z]:$') { throw 'Invalid drive selection.' }
        $scriptName = if ($Request.operation -eq 'AdminBitLocker') { 'save-admin-bitlocker.ps1' } else { 'export-inventory.ps1' }
        $script = (Join-Path ([IO.Path]::GetFullPath($ToolsPath)) $scriptName).Replace("'", "''")
        $drive = ([string]$Request.volume).Replace("'", "''")
        $target = ([string]$Request.output_file).Replace("'", "''")
        # Encoded fixed invocation: filenames remain literal PowerShell strings.
        # Elevate only this read-only collector, not the entire GUI/worker.
        $extra = ''
        if ($Request.operation -eq 'ReviewedInventory') {
            $extra = " -StorageDirectory '" + ([string]$Request.storage_directory).Replace("'", "''") + "'"
            if ($Request.personal_os_volume) { $extra += " -PersonalUseOsVolume -PersonalUseConfirmation '個人利用で許可'" }
        }
        $code = "try { & '$script' -Volume '$drive' -OutputFile '$target'$extra; exit 0 } catch { Write-Error -ErrorRecord `$_; exit 1 }"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
        $executable = Join-Path $PSHOME 'powershell.exe'
        if (-not (Test-Path -LiteralPath $executable)) { $executable = Join-Path $PSHOME 'pwsh.exe' }
        $process = Start-Process -FilePath $executable -Verb RunAs -PassThru -Wait -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded)
        try {
            if ($process.ExitCode -ne 0) { throw '管理者による確認情報の取得に失敗しました。不明な状態は許可しません。管理者PowerShellのCLIで詳細を確認できます。' }
            return [pscustomobject]@{exit_code=0;text=(T 'success')}
        } finally { $process.Dispose() }
    }

    $worker = (Join-Path $PSScriptRoot 'windows-worker.ps1').Replace("'", "''")
    $base = ([IO.Path]::GetFullPath($ToolsPath)).Replace("'", "''")
    $code = "`$ProgressPreference='SilentlyContinue'; & '$worker' -ToolsPath '$base'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = (Join-Path $PSHOME 'powershell.exe')
    if (-not (Test-Path -LiteralPath $info.FileName)) { $info.FileName = (Join-Path $PSHOME 'pwsh.exe') }
    $info.Arguments = '-NoProfile -NonInteractive -OutputFormat Text -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
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
        $standardOutput=$stdout.GetAwaiter().GetResult();$standardError=$stderr.GetAwaiter().GetResult()
        $result=Get-GuiOperationResult $process.ExitCode $standardOutput $standardError
        $script:LastDiagnostics=$result.diagnostics
        $diagnosticsButton.Enabled=$true
        return $result
    } finally { $process.Dispose() }
}
. (Join-Path $PSScriptRoot 'storage-paths.ps1')
# The same operation and message definitions are used by Fedora and the preview.
$script:UiText = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'ui-text.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$script:UiLanguage = if ([Globalization.CultureInfo]::CurrentUICulture.TwoLetterISOLanguageName -eq 'ja') { 'ja' } else { 'en' }
function T([string]$Key) {
    $entry = $script:UiText.PSObject.Properties[$Key]
    if ($null -eq $entry) { return $Key }
    $translated = $entry.Value.PSObject.Properties[$script:UiLanguage]
    if ($null -eq $translated) { return $entry.Value.en }
    return [string]$translated.Value
}
$definitions = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'operations.json') -Raw -Encoding UTF8 | ConvertFrom-Json).windows
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$form = [Windows.Forms.Form]::new()
$form.Text = T 'title'; $form.ClientSize = [Drawing.Size]::new(940,780)
$form.MinimumSize = [Drawing.Size]::new(850,700); $form.AutoScaleMode = 'Dpi'
$form.Font = [Drawing.Font]::new('Segoe UI',10); $form.StartPosition='CenterScreen'
$main = [Windows.Forms.TableLayoutPanel]::new(); $main.Dock='Fill'; $main.ColumnCount=1; $main.RowCount=7
[void]$main.RowStyles.Add([Windows.Forms.RowStyle]::new('Absolute',46))
[void]$main.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize'))
[void]$main.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize'))
[void]$main.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize'))
[void]$main.RowStyles.Add([Windows.Forms.RowStyle]::new('Absolute',46))
[void]$main.RowStyles.Add([Windows.Forms.RowStyle]::new('Percent',100))
[void]$main.RowStyles.Add([Windows.Forms.RowStyle]::new('Absolute',32))
$main.Padding=[Windows.Forms.Padding]::new(12); $form.Controls.Add($main)
$tabs=[Windows.Forms.TabControl]::new(); $tabs.Dock='Fill'
foreach ($category in @('pc-info','registry','diagnostics')) { $tab=[Windows.Forms.TabPage]::new();$tab.Text=T $category;$tab.Tag=$category;[void]$tabs.TabPages.Add($tab) }
$tabs.SelectedIndex=0
$main.Controls.Add($tabs,0,0)
$actions=[Windows.Forms.ComboBox]::new();$actions.DropDownStyle='DropDownList';$actions.Dock='Top';$actions.DisplayMember='Text';$main.Controls.Add($actions,0,1)
$description=[Windows.Forms.Label]::new();$description.AutoSize=$true;$description.MaximumSize=[Drawing.Size]::new(880,0);$description.Padding=[Windows.Forms.Padding]::new(0,8,0,8);$main.Controls.Add($description,0,2)
$fields=[Windows.Forms.TableLayoutPanel]::new();$fields.AutoSize=$true;$fields.Dock='Top';$fields.ColumnCount=3
[void]$fields.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Absolute',210));[void]$fields.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Percent',100));[void]$fields.ColumnStyles.Add([Windows.Forms.ColumnStyle]::new('Absolute',180))
$main.Controls.Add($fields,0,3)
$script:Rows=@{};$script:Controls=@{};$script:Inputs=@();$script:Current=$null;$script:Registry=$null
$script:NameDrafts=@{};$script:EditingHostId='';$script:LoadingName=$false;$script:NameSource=''
function Field([string]$Key,$Control,$Button=$null) {
    $row=$fields.RowCount; $fields.RowCount++
    $label=[Windows.Forms.Label]::new();$label.Text=T $Key;$label.AutoSize=$true;$label.Dock='Fill';$label.Padding=[Windows.Forms.Padding]::new(0,6,0,0)
    $Control.Dock='Top';$Control.Margin=[Windows.Forms.Padding]::new(3,3,3,7)
    $fields.Controls.Add($label,0,$row);$fields.Controls.Add($Control,1,$row)
    $items=@($label,$Control)
    if ($null -ne $Button) {$Button.Dock='Top';$Button.AutoSize=$true;$fields.Controls.Add($Button,2,$row);$items+= $Button}
    $script:Rows[$Key]=[pscustomobject]@{Index=$row;Items=$items};$script:Controls[$Key]=$Control
}
function Combo { $c=[Windows.Forms.ComboBox]::new();$c.DropDownStyle='DropDownList';$c.DisplayMember='Text';return $c }
function TextBox { return [Windows.Forms.TextBox]::new() }
$drive=Combo; [void]$drive.Items.Add([pscustomobject]@{Text=(T 'all_volumes');Volume='' }); Field 'drive' $drive
$folder=TextBox;$folder.Text='GPU-Linux';Field 'folder' $folder
$usb=Combo;Field 'usb' $usb
$usbStorage=[Windows.Forms.CheckBox]::new();$usbStorage.Text=T 'usb_storage_checkbox';Field 'usb_storage' $usbStorage
$files=TextBox;$files.ReadOnly=$true;$fileButton=[Windows.Forms.Button]::new();$fileButton.Text=T 'browse';Field 'files' $files $fileButton
$pcSelector=Combo;$loadButton=[Windows.Forms.Button]::new();$loadButton.Text=T 'load_hosts';Field 'host' $pcSelector $loadButton
$name=TextBox;$candidateButton=[Windows.Forms.Button]::new();$candidateButton.Text=T 'name_candidate';Field 'name' $name $candidateButton
$generationControl=[Windows.Forms.NumericUpDown]::new();$generationControl.Minimum=1;$generationControl.Maximum=1000000000;$generationControl.Value=1;Field 'generation' $generationControl
$output=TextBox;$output.ReadOnly=$true;$outputButton=[Windows.Forms.Button]::new();$outputButton.Text=T 'browse';Field 'output' $output $outputButton
$buttons=[Windows.Forms.FlowLayoutPanel]::new();$buttons.Dock='Fill'
$run=[Windows.Forms.Button]::new();$run.Text=T 'run';$run.AutoSize=$true;$buttons.Controls.Add($run)
$help=[Windows.Forms.Button]::new();$help.Text=T 'help';$help.AutoSize=$true;$buttons.Controls.Add($help);$main.Controls.Add($buttons,0,4)
$resultPanel=[Windows.Forms.Panel]::new();$resultPanel.Dock='Fill';$main.Controls.Add($resultPanel,0,5)
$preview=[Windows.Forms.TextBox]::new();$preview.Multiline=$true;$preview.ReadOnly=$true;$preview.ScrollBars='Both';$preview.Dock='Fill';$resultPanel.Controls.Add($preview)
$grid=[Windows.Forms.DataGridView]::new();$grid.Dock='Fill';$grid.ReadOnly=$true;$grid.AllowUserToAddRows=$false;$grid.AllowUserToDeleteRows=$false;$grid.AutoSizeColumnsMode='Fill';$grid.SelectionMode='FullRowSelect';$grid.MultiSelect=$false;$grid.Visible=$false;$resultPanel.Controls.Add($grid)
$detail=[Windows.Forms.Button]::new();$detail.Text=T 'details';$detail.AutoSize=$true;$buttons.Controls.Add($detail);$detail.Visible=$false
$script:LastDiagnostics=''
$diagnosticsButton=[Windows.Forms.Button]::new();$diagnosticsButton.Text=T 'copy_diagnostics';$diagnosticsButton.AutoSize=$true;$diagnosticsButton.Enabled=$false;$buttons.Controls.Add($diagnosticsButton)
$footer=[Windows.Forms.Label]::new();$footer.Text=T 'copy_details';$footer.Dock='Fill';$main.Controls.Add($footer,0,6)
function Show-Message([string]$Text,[string]$Title) { [void][Windows.Forms.MessageBox]::Show($form,$Text,$Title,'OK','Information') }
function Update-Fields {
    if (-not $actions.SelectedItem) {return}
    $script:Current=$actions.SelectedItem.Definition
    $description.Text=(T $script:Current.description)
    if ($script:Current.PSObject.Properties['admin'] -and $script:Current.admin) { $description.Text += "`r`n"+(T 'admin_notice') }
    $fields.SuspendLayout()
    foreach ($key in $script:Rows.Keys) {
        $visible=($script:Current.fields -contains $key)
        foreach ($control in $script:Rows[$key].Items) {$control.Visible=$visible}
        $row=$script:Rows[$key].Index
        while ($fields.RowStyles.Count -le $row) { [void]$fields.RowStyles.Add([Windows.Forms.RowStyle]::new()) }
        $fields.RowStyles[$row].SizeType=if ($visible) {'AutoSize'} else {'Absolute'}
        $fields.RowStyles[$row].Height=0
    }
    $fields.ResumeLayout($true)
    $script:LoadingName=$true;$script:EditingHostId='';$script:NameDrafts=@{};$script:NameSource=''
    try {$script:Inputs=@();$files.Text='';$output.Text='';$name.Text='';$pcSelector.Items.Clear();$script:Registry=$null}
    finally {$script:LoadingName=$false}
    $usbStorage.Checked=$false;$preview.Clear();$preview.Visible=$true;$grid.Visible=$false;$detail.Visible=$false
    $script:LastDiagnostics='';$diagnosticsButton.Enabled=$false
}
function Update-Actions {
    $actions.Items.Clear()
    if ($tabs.TabPages.Count -eq 0) {return}
    # SelectedTab can be null until the native window handle has been created.
    $index=$tabs.SelectedIndex
    if ($index -lt 0) {$index=0}
    if ($index -ge $tabs.TabPages.Count) {return}
    $selectedCategory=$tabs.TabPages[$index].Tag
    foreach ($definition in @($definitions | Where-Object {$_.category -eq $selectedCategory})) {
        [void]$actions.Items.Add([pscustomobject]@{Text=(T $definition.label);Definition=$definition})
    }
    if ($actions.Items.Count) {$actions.SelectedIndex=0}
}
function Ask-OsConsent {
    $dialog=[Windows.Forms.Form]::new();$dialog.Text=T 'os_consent';$dialog.ClientSize=[Drawing.Size]::new(540,160);$dialog.StartPosition='CenterParent';$dialog.Font=$form.Font
    $phrase=if($script:UiLanguage -eq 'ja'){'個人利用で許可'}else{'ALLOW PERSONAL USE'}
    $label=[Windows.Forms.Label]::new();$label.Text=(T 'os_question')+"`r`n"+$phrase;$label.Location=[Drawing.Point]::new(16,12);$label.Size=[Drawing.Size]::new(508,54);$dialog.Controls.Add($label)
    $entry=[Windows.Forms.TextBox]::new();$entry.Location=[Drawing.Point]::new(16,72);$entry.Size=[Drawing.Size]::new(508,28);$dialog.Controls.Add($entry)
    $yes=[Windows.Forms.Button]::new();$yes.Text='OK';$yes.DialogResult='OK';$yes.Location=[Drawing.Point]::new(330,118);$dialog.Controls.Add($yes)
    $no=[Windows.Forms.Button]::new();$no.Text=T 'back';$no.DialogResult='Cancel';$no.Location=[Drawing.Point]::new(430,118);$dialog.Controls.Add($no);$dialog.CancelButton=$no;$dialog.AcceptButton=$no
    try {return ($dialog.ShowDialog($form) -eq 'OK' -and $entry.Text -ceq $phrase)} finally {$dialog.Dispose()}
}
function Read-Registry([string]$Path = '') {
    $selectedInputs=@(if($Path){$Path}else{$script:Inputs})
    if ($selectedInputs.Count -ne 1) {throw (T 'required')}
    $result=Invoke-GuiOperation ([ordered]@{operation='Status';inputs=@($selectedInputs)})
    if ($result.exit_code -ne 0) {throw $result.text}
    return ($result.text | ConvertFrom-Json)
}
function Fill-Hosts {
    $value=Read-Registry
    $source=$script:Inputs[0]+"`n"+($value | ConvertTo-Json -Depth 12 -Compress)
    if ($source -cne $script:NameSource) {
        $script:NameDrafts=@{}
        foreach ($item in @($value.records)) {
            $script:NameDrafts[$item.hardware_uuid]=if($item.PSObject.Properties['windows_computer_name']){[string]$item.windows_computer_name}else{''}
        }
        $script:NameSource=$source
    }
    $script:Registry=$value;$script:LoadingName=$true;$script:EditingHostId=''
    try {
        $pcSelector.Items.Clear()
        foreach ($item in @($script:Registry.records)) {
            $label=if ($script:NameDrafts[$item.hardware_uuid]) {$script:NameDrafts[$item.hardware_uuid]} else {T 'unnamed'}
            [void]$pcSelector.Items.Add([pscustomobject]@{Text=($label+'  ['+$item.hardware_uuid+']');Record=$item})
        }
    } finally {$script:LoadingName=$false}
    if ($pcSelector.Items.Count) {$pcSelector.SelectedIndex=0}
    Update-NameSelection
    $generationControl.Value=[Math]::Min($generationControl.Maximum,([long]$script:Registry.generation+1))
}
function Update-NameSelection {
    if ($script:LoadingName) {return}
    $script:LoadingName=$true
    try {
        $script:EditingHostId=if($pcSelector.SelectedItem){[string]$pcSelector.SelectedItem.Record.hardware_uuid}else{''}
        $name.Text=if($script:EditingHostId){[string]$script:NameDrafts[$script:EditingHostId]}else{''}
    } finally {$script:LoadingName=$false}
}
function Save-NameDraft {
    if (-not $script:LoadingName -and $script:EditingHostId) {$script:NameDrafts[$script:EditingHostId]=$name.Text}
}
function Get-NameUpdates {
    if ($null -eq $script:Registry) {throw (T 'required')}
    foreach ($item in @($script:Registry.records)) {
        $original=if($item.PSObject.Properties['windows_computer_name']){[string]$item.windows_computer_name}else{''}
        $draft=[string]$script:NameDrafts[$item.hardware_uuid]
        if ($draft -cne $original) {
            if (-not $draft) {throw (T 'name_missing')}
            [pscustomobject]@{hardware_uuid=$item.hardware_uuid;windows_computer_name=$draft}
        }
    }
}
$fileButton.Add_Click({
    try {
        $picker=[Windows.Forms.OpenFileDialog]::new();$picker.Filter='JSON (*.json)|*.json';$picker.Multiselect=[bool]$script:Current.multiple
        $root=if($usb.SelectedItem){$usb.SelectedItem.Volume}else{''}
        $directory=Get-GuiInputDirectory $script:Current.id $root
        if($directory){$picker.InitialDirectory=$directory}
        if ($picker.ShowDialog($form) -ne 'OK') {return}
        $script:Inputs=@($picker.FileNames);$files.Text=($script:Inputs -join '; ')
        if ($script:Current.id -eq 'Name') {Fill-Hosts}
        elseif ($script:Current.id -eq 'Merge') {
            $highest=0
            foreach ($path in $script:Inputs) {$value=Read-Registry $path;$highest=[Math]::Max($highest,[long]$value.generation)}
            $generationControl.Value=[Math]::Min($generationControl.Maximum,$highest+1)
        }
        elseif ($script:Current.id -eq 'Convert') {$script:Registry=Read-Registry;$generationControl.Value=[Math]::Min($generationControl.Maximum,([long]$script:Registry.generation+1))}
    } catch {Show-Message $_.Exception.Message (T 'failure')}
})
$loadButton.Add_Click({try {Fill-Hosts} catch {Show-Message $_.Exception.Message (T 'failure')}})
$pcSelector.Add_SelectedIndexChanged({Update-NameSelection})
$name.Add_TextChanged({Save-NameDraft})
$candidateButton.Add_Click({
    try {
        if (-not $pcSelector.SelectedItem) {throw (T 'required')}
        $picker=[Windows.Forms.OpenFileDialog]::new();$picker.Filter='JSON (*.json)|*.json'
        if ($picker.ShowDialog($form) -ne 'OK') {return}
        $item=Get-Item -LiteralPath $picker.FileName
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 1048576) {throw (T 'required')}
        $candidate=[IO.File]::ReadAllText($item.FullName)|ConvertFrom-Json
        if ([string]$candidate.hardware_uuid -ine [string]$pcSelector.SelectedItem.Record.hardware_uuid) {throw (T 'name_mismatch')}
        if (-not $candidate.PSObject.Properties['windows_computer_name'] -or -not $candidate.windows_computer_name) {throw (T 'name_missing')}
        $name.Text=[string]$candidate.windows_computer_name
    } catch {Show-Message $_.Exception.Message (T 'failure')}
})
$outputButton.Add_Click({
    try {
        $root=if($usb.SelectedItem){$usb.SelectedItem.Volume}else{''}
        $directory=Get-GuiOutputDirectory $script:Current.id $root
        $picker=[Windows.Forms.SaveFileDialog]::new();$picker.Filter='JSON (*.json)|*.json';$picker.Title=T 'output'
        $picker.FileName=$script:Current.id.ToLowerInvariant()+'-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.json'
        if($directory){$picker.InitialDirectory=$directory}
        if($picker.ShowDialog($form)-eq 'OK'){$output.Text=$picker.FileName}
    } catch {Show-Message $_.Exception.Message (T 'failure')}
})
$run.Add_Click({
    $script:LastDiagnostics='';$diagnosticsButton.Enabled=$false
    try {
        $id=[string]$script:Current.id;$needed=@($script:Current.fields)
        $selectedDrive=if ($needed -contains 'drive' -and $drive.SelectedItem) {$drive.SelectedItem.Volume} else {''}
        $selectedUsb=if ($needed -contains 'usb' -and $usb.SelectedItem) {$usb.SelectedItem.Volume} else {''}
        $targetFolder=if ($needed -contains 'folder') {$folder.Text} else {''}
        $targetOutput=if ($needed -contains 'output') {$output.Text} else {''}
        if (($needed -contains 'drive' -and $id -ne 'Environment' -and -not $selectedDrive) -or ($needed -contains 'output' -and -not $targetOutput) -or ($needed -contains 'files' -and -not $script:Inputs.Count)) {throw (T 'required')}
        $personal=$false;$confirmation=''
        if ($id -in @('Inventory','ReviewedInventory')) {
            $partition=Get-Partition -DriveLetter $selectedDrive.Substring(0,1)
            if ($partition.IsBoot -isnot [bool] -or $partition.IsSystem -isnot [bool] -or $partition.IsHidden -isnot [bool]) {
                # Standard USB candidates may defer the unknown hidden flag to Fedora.
                if (-not ($id -eq 'Inventory' -and $usbStorage.Checked -and $partition.IsBoot -is [bool] -and $partition.IsSystem -is [bool] -and $null -eq $partition.IsHidden)) {throw (T 'required')}
            }
            if ($partition.IsSystem -or $partition.IsHidden) {throw (T 'required')}
            if ($partition.IsBoot) {
                if ($usbStorage.Checked) {throw (T 'required')}
                $question=(T 'os_warning')+"`r`n"+$selectedDrive+'\'+$targetFolder+"`r`n"+(T 'os_question')
                if ([Windows.Forms.MessageBox]::Show($form,$question,(T 'os_consent'),'YesNo','Warning','Button2') -ne 'Yes') {return}
                if (-not (Ask-OsConsent)) {return}
                $personal=$true;$confirmation='個人利用で許可'
            }
        }
        $updates=@()
        if ($id -eq 'Name') {
            Save-NameDraft
            $updates=@(Get-NameUpdates)
            if (-not $updates.Count) {throw (T 'no_name_changes')}
        }
        $plan=New-OperationPlan $id $selectedDrive $targetFolder $selectedUsb $script:Inputs $targetOutput ([int]$generationControl.Value) $personal $confirmation ([bool]($needed -contains 'usb_storage' -and $usbStorage.Checked)) '' '' $updates
        $lines=@((T $script:Current.label),(T $script:Current.description))
        foreach ($key in $needed) {
            if ($id -eq 'Name' -and $key -in @('host','name')) {continue}
            $value=switch($key) {'drive' {$selectedDrive} 'folder' {$targetFolder} 'usb' {$selectedUsb} 'files' {$script:Inputs -join "`r`n"} 'output' {$targetOutput} 'generation' {$generationControl.Value} 'host' {$selectedHost} 'name' {$pcName} 'usb_storage' {$usbStorage.Checked}}
            $lines += (T $key)+': '+$value
        }
        if ($id -eq 'Name') {
            foreach ($update in $updates) {
                $lines += $update.hardware_uuid+': '+$update.windows_computer_name
            }
        }
        if ($id -notin @('Status','Environment') -and [Windows.Forms.MessageBox]::Show($form,($lines -join "`r`n")+"`r`n`r`n"+(T 'confirm_question'),(T 'confirm'),'YesNo','Question','Button2') -ne 'Yes') {return}
        $run.Enabled=$false;$fields.Enabled=$false;$tabs.Enabled=$false;$actions.Enabled=$false;$form.UseWaitCursor=$true
        $grid.Visible=$false;$preview.Visible=$true;$detail.Visible=$false;$preview.Text=T 'working'
        $answer=Invoke-GuiOperation $plan.request
        if ($id -eq 'Name' -and $answer.exit_code -eq 0) {
            $script:Inputs=@($targetOutput);$files.Text=$targetOutput;Fill-Hosts;$output.Text=''
        }
        if ($id -eq 'Status' -and $answer.exit_code -eq 0) {
            $script:Registry=$answer.text|ConvertFrom-Json
            $description.Text=(T $script:Current.description)+"`r`n"+(T 'schema')+': '+$script:Registry.schema+'    '+(T 'current_generation')+': '+$script:Registry.generation+'    '+(T 'host_count')+': '+@($script:Registry.records).Count
            $grid.Columns.Clear();$grid.Rows.Clear()
            foreach ($key in @('name','mode','folder','uuid')) {[void]$grid.Columns.Add($key,(T $key))}
            foreach ($item in @($script:Registry.records)) {
                $label=if ($item.PSObject.Properties['windows_computer_name'] -and $item.windows_computer_name) {$item.windows_computer_name} else {T 'unnamed'}
                $mode=if ($item.PSObject.Properties['boot_mode'] -and $item.boot_mode -eq 'no-gpu-test') {T 'test'} else {T 'normal'}
                [void]$grid.Rows.Add([object[]]@($label,$mode,$item.storage_directory,$item.hardware_uuid))
            }
            $preview.Visible=$false;$grid.Visible=$true;$grid.BringToFront();$detail.Visible=$true
        } else {$preview.Text=if ($answer.exit_code -eq 0) {(T 'success')+"`r`n"+$answer.text} else {(T 'failure')+"`r`n"+$answer.text}}
    } catch {
        $script:LastDiagnostics+="`r`n--- GUI exception ---`r`n"+$_.Exception.ToString();$diagnosticsButton.Enabled=$true
        $preview.Visible=$true;$preview.Text=(T 'failure')+"`r`n"+$_.Exception.Message;Show-Message $_.Exception.Message (T 'failure')
    }
    finally {$run.Enabled=$true;$fields.Enabled=$true;$tabs.Enabled=$true;$actions.Enabled=$true;$form.UseWaitCursor=$false}
})
$detail.Add_Click({if ($grid.SelectedRows.Count -and $script:Registry) {$index=$grid.SelectedRows[0].Index;Show-Message ($script:Registry.records[$index]|ConvertTo-Json -Depth 12) (T 'details')}})
$diagnosticsButton.Add_Click({
    if($script:LastDiagnostics){
        try {[Windows.Forms.Clipboard]::SetText($script:LastDiagnostics);Show-Message (T 'diagnostics_copied') (T 'copy_diagnostics')}
        catch {Show-Message $_.Exception.Message (T 'failure')}
    }
})
$help.Add_Click({$path=Join-Path $PSScriptRoot ('usage.'+$script:UiLanguage+'.txt');if(-not(Test-Path -LiteralPath $path)){$path=Join-Path $PSScriptRoot 'usage.txt'};Show-Message ([IO.File]::ReadAllText($path)) (T 'help')})
$tabs.Add_SelectedIndexChanged({Update-Actions});$actions.Add_SelectedIndexChanged({Update-Fields})
try {
    foreach ($item in @(Get-Volume | Where-Object {$_.DriveLetter})) {
        $letter=[string]$item.DriveLetter+':'
        $label="$letter  $($item.FileSystemLabel)  $([Math]::Round($item.Size/1GB,1)) GiB"
        if ($item.FileSystem -eq 'NTFS') {[void]$drive.Items.Add([pscustomobject]@{Text=$label;Volume=$letter})}
        if ($item.FileSystem -eq 'FAT32' -and $item.FileSystemLabel -eq 'GPU-LOGS') {[void]$usb.Items.Add([pscustomobject]@{Text=$label;Volume=($letter+'\')})}
    }
    if ($drive.Items.Count) {$drive.SelectedIndex=0};if($usb.Items.Count -eq 1){$usb.SelectedIndex=0}
} catch {$preview.Text=$_.Exception.Message}
$form.Add_Resize({$description.MaximumSize=[Drawing.Size]::new([Math]::Max(300,$main.ClientSize.Width-32),0)})
Update-Actions
[void]$form.ShowDialog()
