param([string]$GuiDirectory)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $GuiDirectory 'windows-gui.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
foreach($name in @('T','Update-UiLanguage')){
    $node=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true))
    . ([scriptblock]::Create($node[0].Extent.Text))
}
$script:UiText=Get-Content -LiteralPath (Join-Path $GuiDirectory 'ui-text.json') -Raw -Encoding UTF8|ConvertFrom-Json
$script:AutomaticLanguage='en';$script:ChangingLanguage=$false
function Control {return [pscustomobject]@{Text='';AccessibleName=''}}
$form=Control;$description=Control;$footer=Control
$fileButton=Control;$outputButton=Control;$run=Control;$help=Control;$detail=Control
$loadButton=Control;$candidateButton=Control;$usbStorage=Control;$diagnosticsButton=Control
$script:Rows=@{folder=[pscustomobject]@{Items=@((Control),(Control))}}
$script:Rows.folder.Items[1].Text='KEEP-FOLDER'
$tabs=[pscustomobject]@{TabPages=@([pscustomobject]@{Tag='registry';Text=''})}
$drive=[pscustomobject]@{Items=[Collections.Generic.List[object]]::new()};$drive.Items.Add((Control))
$definition=[pscustomobject]@{label='windows.Merge';description='windows.Merge.description'}
$actions=[pscustomobject]@{Items=[Collections.Generic.List[object]]::new();SelectedIndex=0};$actions.Items.Add([pscustomobject]@{Definition=$definition;Text=''})
$script:Current=$definition
$grid=[pscustomobject]@{Columns=@([pscustomobject]@{Name='name';HeaderText=''});Visible=$false}
$pcSelector=[pscustomobject]@{Items=[Collections.Generic.List[object]]::new();SelectedIndex=-1}
$script:Inputs=@('KEEP-REGISTRY.json');$script:NameDrafts=@{fixture='KEEP-NAME'}
foreach($choice in @('ja','en','auto')){
    $languageSelector=[pscustomobject]@{SelectedItem=[pscustomobject]@{Code=$choice}}
    Update-UiLanguage
    $expected=if($choice -eq 'auto'){'en'}else{$choice}
    if($script:UiLanguage -ne $expected -or $form.Text -cne $script:UiText.title.$expected){throw 'Language selection failed'}
    if($fileButton.Text -cne $script:UiText.browse.$expected){throw 'Button translation failed'}
    if($script:Inputs[0] -ne 'KEEP-REGISTRY.json' -or $script:NameDrafts.fixture -ne 'KEEP-NAME' -or $script:Rows.folder.Items[1].Text -ne 'KEEP-FOLDER'){throw 'Language change lost pending work'}
    if($script:ChangingLanguage){throw 'Language change guard was not reset'}
}
Write-Output 'Manual language selection and draft preservation passed.'
