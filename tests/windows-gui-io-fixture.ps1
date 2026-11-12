param([string]$GuiDirectory,[string]$WorkDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $GuiDirectory 'windows-gui.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw ($errors | Out-String)}
. (Join-Path $GuiDirectory 'storage-paths.ps1')
foreach ($name in @('Get-GuiInputDirectory','Get-GuiOutputDirectory','Get-GuiOperationResult')) {
    $functions=@($ast.FindAll({param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    },$true))
    if ($functions.Count -ne 1) {throw "Missing GUI function: $name"}
    . ([scriptblock]::Create($functions[0].Extent.Text))
}
function T([string]$Key) {return $Key}
$xml='#< CLIXML' + "`n" + '<Objs><Obj S="progress"><MS>module preparation</MS></Obj></Objs>'
$json='{"schema":2,"records":[]}'
$result=Get-GuiOperationResult 0 $json $xml
if (($result.text | ConvertFrom-Json).schema -ne 2) {throw 'stderr contaminated registry JSON'}
if ($result.text.Contains('CLIXML') -or -not $result.diagnostics.Contains($xml)) {throw 'Diagnostics must be separate and retained'}
$result=Get-GuiOperationResult 1 '' 'Synthetic validation failure'
if ($result.text -cne 'Synthetic validation failure') {throw 'Readable operation error was lost'}
$result=Get-GuiOperationResult 1 '' $xml
if ($result.text.Contains('CLIXML') -or $result.text -ne 'failure') {throw 'Serialized diagnostics leaked into normal errors'}

$usb=Join-Path $WorkDirectory 'GPU-LOGS'
$config=Join-Path $usb 'gpu-config';$data=Join-Path $usb 'data';$gui=Join-Path $usb 'gui'
foreach ($directory in @($config,$data,$gui)) {[void][IO.Directory]::CreateDirectory($directory)}
[IO.File]::WriteAllText((Join-Path $usb 'usb-config.json'),'{}')
if ((Get-GuiInputDirectory 'Status' $usb $gui) -ne $config) {throw 'Status must start in gpu-config'}
if ((Get-GuiInputDirectory 'Status' '' $gui) -ne $config) {throw 'Status must find its own USB without a visible USB selector'}
foreach ($action in @('Status','Merge','Deploy','Convert','Name')) {
    if ((Get-GuiInputDirectory $action $usb $gui) -ne $config) {throw "Registry input must start in gpu-config: $action"}
    if ((Get-GuiInputDirectory $action '' $gui) -ne $config) {throw "Registry input must find its own USB: $action"}
}
$registry=Join-Path $config 'registry.json'
[IO.File]::WriteAllText($registry,'synthetic existing active registry')
foreach ($action in @('Merge','Convert','Name')) {
    if ((Get-GuiOutputDirectory $action $usb $gui) -ne $config) {throw "Registry output must start in gpu-config: $action"}
    if ((Get-GuiOutputDirectory $action '' $gui) -ne $config) {throw "Registry output must find its own USB: $action"}
}
foreach ($action in @('Inventory','ShellObservation','AdminBitLocker','ReviewedInventory','Collect')) {
    if ((Get-GuiOutputDirectory $action $usb $gui) -ne $data) {throw "Collection output default changed: $action"}
}
if ([IO.File]::ReadAllText($registry) -cne 'synthetic existing active registry') {throw 'Opening a dialog changed the active registry'}
$empty=Join-Path $WorkDirectory 'empty';[void][IO.Directory]::CreateDirectory($empty)
if ((Get-GuiInputDirectory 'Status' $empty $gui) -ne $empty) {throw 'Missing gpu-config must fall back to the selected root'}
if ((Get-GuiInputDirectory 'Status' '' (Join-Path $empty 'gui')) -ne '') {throw 'Unmarked locations must not be assumed to be a USB'}
Write-Output 'Registry picker defaults and separated result/diagnostic streams passed.'
