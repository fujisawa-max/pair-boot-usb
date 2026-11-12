param([string]$GuiDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $GuiDirectory 'windows-gui.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($functionName in @('Update-NameSelection','Save-NameDraft','Get-NameUpdates','Fill-Hosts','New-OperationPlan')) {
    $matches=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName},$true))
    if($matches.Count -ne 1){throw "Expected real GUI function: $functionName"}
    . ([scriptblock]::Create($matches[0].Extent.Text))
}
function T([string]$Key){return $Key}
function Read-Registry {return $script:Snapshot}
$ToolsPath=Join-Path (Split-Path -Parent $GuiDirectory) 'toolkit'
$a='01234567-89ab-cdef-0123-456789abcdef';$b='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
$script:Snapshot=[pscustomobject]@{generation=7;records=@(
    [pscustomobject]@{hardware_uuid=$a;windows_computer_name='OLD-A'},
    [pscustomobject]@{hardware_uuid=$b;windows_computer_name=$null})}
$before=$script:Snapshot | ConvertTo-Json -Depth 12 -Compress
$script:Inputs=@('original.json');$script:Registry=$null;$script:NameDrafts=@{}
$script:NameSource='';$script:EditingHostId='';$script:LoadingName=$false
$pcSelector=[pscustomobject]@{Items=[Collections.Generic.List[object]]::new();SelectedItem=$null;SelectedIndex=-1}
$generationControl=[pscustomobject]@{Maximum=1000000000;Value=1}
$name=[pscustomobject]@{Value=''}
# Invoke the real TextChanged handler when the fake textbox is assigned, also
# during programmatic restoration, to detect cross-PC cache corruption.
$name | Add-Member -MemberType ScriptProperty -Name Text -Value {$this.Value} -SecondValue {param($value) $this.Value=[string]$value;Save-NameDraft}
Fill-Hosts
$pcSelector.SelectedItem=$pcSelector.Items[0];Update-NameSelection
$name.Text='EDIT-A'
$pcSelector.SelectedItem=$pcSelector.Items[1];Update-NameSelection
if($name.Text -cne ''){throw 'Unknown PC name must start empty'}
$name.Text='EDIT-B'
$pcSelector.SelectedItem=$pcSelector.Items[0];Update-NameSelection
if($name.Text -cne 'EDIT-A'){throw 'Switching PCs lost the first draft'}
$name.Text='CANDIDATE-A'
$pcSelector.SelectedItem=$pcSelector.Items[1];Update-NameSelection
if($name.Text -cne 'EDIT-B'){throw 'Restoring A overwrote B'}
Fill-Hosts
if($script:NameDrafts[$a] -cne 'CANDIDATE-A' -or $script:NameDrafts[$b] -cne 'EDIT-B'){throw 'Reloading the same registry lost drafts'}
$updates=@(Get-NameUpdates)
if($updates.Count -ne 2){throw 'Expected two pending updates'}
$plan=New-OperationPlan -Action Name -Files @('original.json') -Output 'new.json' -NewGeneration 8 -PcNameUpdates $updates
if(@($plan.request.name_updates).Count -ne 2 -or $plan.request.PSObject.Properties['host_uuid']){throw 'Batch plan collapsed to the selected PC'}
if(($script:Snapshot | ConvertTo-Json -Depth 12 -Compress) -cne $before){throw 'Editing or building a plan mutated the source registry'}
$script:NameDrafts[$b]=''
$one=@(Get-NameUpdates)
if($one.Count -ne 1){throw 'Unchanged empty name must not be submitted'}
$script:NameDrafts[$a]=''
try {$null=@(Get-NameUpdates);throw 'Accepted clearing a known name'} catch {if($_.Exception.Message -eq 'Accepted clearing a known name'){throw}}
$script:Snapshot=[pscustomobject]@{generation=8;records=@(
    [pscustomobject]@{hardware_uuid=$a;windows_computer_name='CANDIDATE-A'},
    [pscustomobject]@{hardware_uuid=$b;windows_computer_name='EDIT-B'})}
$script:Inputs=@('new.json');Fill-Hosts
if(@(Get-NameUpdates).Count -ne 0 -or $generationControl.Value -ne 9){throw 'Saved registry did not establish the new baseline'}
Write-Output 'PC switching, event restoration, batch planning, source preservation and saved baseline passed.'
