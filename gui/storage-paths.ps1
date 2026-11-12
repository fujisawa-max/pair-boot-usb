# A dedicated default export folder; never use the active registry directory.
function Get-CollectionDirectory([string]$UsbPath) {
    if (-not $UsbPath) { return '' }
    $root = Get-Item -LiteralPath ([IO.Path]::GetFullPath($UsbPath)) -Force
    if (-not $root.PSIsContainer -or ($root.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Expected an ordinary USB directory.' }
    $directory = Join-Path $root.FullName 'data'
    $existing = Get-Item -LiteralPath $directory -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if (-not $existing.PSIsContainer -or ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Data output directory is not an ordinary directory.' }
    } else {
        [void][IO.Directory]::CreateDirectory($directory)
    }
    return $directory
}
