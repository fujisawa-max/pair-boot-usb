param([string]$GuiDirectory)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $GuiDirectory 'windows-gui.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($name in @('Get-GuiInputDirectory','Get-GuiOutputDirectory')) {
    $nodes=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true))
    if($nodes.Count -ne 1){throw 'Expected actual GUI function'}
    . ([scriptblock]::Create($nodes[0].Extent.Text))
}
$base=Join-Path ([IO.Path]::GetTempPath()) ('pair-manager-fixture-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory((Join-Path $base 'gui'))
try {
    [IO.File]::WriteAllText((Join-Path $base 'manager-config.json'),'{}')
    $gui=Join-Path $base 'gui'
    foreach($usb in @('',(Join-Path $base 'unrelated-usb'))) {
        foreach($action in @('Merge','Convert','Name')) {
            if((Get-GuiOutputDirectory $action $usb $gui) -cne (Join-Path $base 'gpu-config')){throw 'Merged registry output escaped PC workspace'}
        }
        if((Get-GuiInputDirectory 'Deploy' $usb $gui) -cne (Join-Path $base 'gpu-config')){throw 'Deployment must read PC registry'}
        if((Get-GuiOutputDirectory 'Collect' $usb $gui) -cne (Join-Path $base 'backups')){throw 'Collection must default to PC backups'}
        if((Get-GuiOutputDirectory 'Inventory' $usb $gui) -cne (Join-Path $base 'data')){throw 'Inventory must default to PC data'}
    }
    Write-Output 'Portable manager paths remain on PC with or without a target USB.'
} finally {Remove-Item -LiteralPath $base -Recurse -Force}
