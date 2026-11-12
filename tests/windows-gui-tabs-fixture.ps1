param([string]$GuiDirectory)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $GuiDirectory 'windows-gui.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw ($errors | Out-String)}
$functions=@($ast.FindAll({param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Update-Actions'
},$true))
if ($functions.Count -ne 1) {throw 'Expected the actual GUI action selection function'}
. ([scriptblock]::Create($functions[0].Extent.Text))
function T([string]$Key) {return $Key}
$definitions=(Get-Content -LiteralPath (Join-Path $GuiDirectory 'operations.json') -Raw -Encoding UTF8 | ConvertFrom-Json).windows
# Before the native handle is created, SelectedTab may be null. Test the real
# selection function without Windows Forms or any inventory/worker execution.
$tabs=[pscustomobject]@{
    SelectedIndex=-1
    SelectedTab=$null
    TabPages=@('pc-info','registry','diagnostics' | ForEach-Object {[pscustomobject]@{Tag=$_}})
}
$actions=[pscustomobject]@{Items=[Collections.Generic.List[object]]::new();SelectedIndex=-1}
foreach ($index in @(-1,0,1,2)) {
    $tabs.SelectedIndex=$index
    $tabs.SelectedTab=$null
    Update-Actions
    $category=$tabs.TabPages[[Math]::Max(0,$index)].Tag
    $expected=@($definitions | Where-Object {$_.category -eq $category} | ForEach-Object {$_.id})
    $actual=@($actions.Items | ForEach-Object {$_.Definition.id})
    if (($expected -join ',') -cne ($actual -join ',')) {throw "Wrong operations for tab $index"}
    if ($actions.SelectedIndex -ne 0) {throw 'Expected the first operation to be selected'}
}
$tabs.TabPages=@(); $tabs.SelectedIndex=-1
Update-Actions
if ($actions.Items.Count) {throw 'Operations remain without tabs'}
Write-Output 'Initial handle-free selection, all categories, and empty tabs passed.'
