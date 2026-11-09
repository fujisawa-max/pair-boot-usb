# Synthetic APIs only. Never loads Windows storage or encryption providers.
param([string]$ScriptPath, [string]$OutputPath, [string]$Scenario='ok')
$ErrorActionPreference = 'Stop'
function Get-Volume { param($DriveLetter) [pscustomobject]@{FileSystem='NTFS'} }
function Get-CimInstance {
    param($ClassName,$Namespace,$Filter)
    if ($ClassName -eq 'Win32_ComputerSystemProduct') {
        return [pscustomobject]@{UUID='01234567-89ab-cdef-0123-456789abcdef'}
    }
    if ($ClassName -ne 'Win32_EncryptableVolume' -or $Filter -ne "DriveLetter='F:'") { throw 'Unexpected query' }
    if ($Scenario -eq 'missing') { return }
    return [pscustomobject]@{DriveLetter='F:'}
}
function Invoke-CimMethod {
    param($InputObject,$MethodName,$Arguments)
    if ($MethodName -eq 'GetConversionStatus') {
        $state = if ($Scenario -eq 'encrypted') { 1 } elseif ($Scenario -eq 'transition') { 3 } else { 0 }
        $percentage = if ($Scenario -eq 'nonzero') { 5 } else { 0 }
        return [pscustomobject]@{ReturnValue=0;ConversionStatus=$state;EncryptionPercentage=$percentage}
    }
    if ($MethodName -ne 'GetProtectionStatus') { throw 'Unexpected method' }
    $state = if ($Scenario -eq 'protected') { 1 } elseif ($Scenario -eq 'unknown') { 2 } elseif ($Scenario -eq 'null') { $null } else { 0 }
    $code = if ($Scenario -eq 'failed') { 5 } else { 0 }
    return [pscustomobject]@{ReturnValue=$code;ProtectionStatus=$state}
}
function fsutil {
    $global:LASTEXITCODE = if ($Scenario -eq 'serial-error') { 1 } else { 0 }
    'NTFS serial: 0x0123456789abcdef'
}
& $ScriptPath -Volume F: -OutputFile $OutputPath
