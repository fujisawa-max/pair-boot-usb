# Test-only stand-ins: no Windows storage API or real COM object is invoked.
param([string]$ScriptPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
function Get-CimInstance { [pscustomobject]@{UUID='01234567-89ab-cdef-0123-456789abcdef';Name='LLM-PC-01'} }
function Get-Volume { param($DriveLetter) [pscustomobject]@{FileSystem='NTFS';Size=[long]8589930496} }
function Get-Partition { param($DriveLetter) [pscustomobject]@{Guid='{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}';Offset=[long]1048576;Size=[long]8589934592;IsBoot=$false;IsSystem=$false;IsHidden=$false} }
function Get-ItemProperty { param($Path,$Name) [pscustomobject]@{HiberbootEnabled=[int]0} }
function New-Object {
    param($ComObject)
    if ($ComObject -ne 'Shell.Application') { throw 'Unexpected COM request' }
    $item = [pscustomobject]@{ObservedValue=[int]2}
    $item | Add-Member -MemberType ScriptMethod -Name ExtendedProperty -Value {param($Name) return $this.ObservedValue}
    $folder = [pscustomobject]@{Self=$item}
    $shell = [pscustomobject]@{Folder=$folder}
    $shell | Add-Member -MemberType ScriptMethod -Name NameSpace -Value {param($Path) return $this.Folder}
    return $shell
}
& $ScriptPath -Volume D: -OutputFile $OutputPath
