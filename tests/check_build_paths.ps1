param([Parameter(Mandatory = $true)][string]$ExistingPackage)
$ErrorActionPreference = "Stop"
$baseline = (Resolve-Path -LiteralPath $ExistingPackage).Path
$script = Join-Path $PSScriptRoot "../scripts/build.ps1"
foreach ($destination in @($baseline, (Join-Path $baseline "build-check"), (Split-Path -Parent $baseline))) {
    $message = $null
    try { & $script -ExistingPackage $baseline -Destination $destination }
    catch { $message = $_.Exception.Message }
    if ($message -ne "输出目录与已有软件包目录不能相同，也不能互相包含") {
        throw "目录包含关系检查未通过：$destination；$message"
    }
}
$message = $null
try { & $script -ExistingPackage $baseline -Destination ".work" }
catch { $message = $_.Exception.Message }
if ($message -notlike "输出目录已经存在，请指定新的 Destination：*") {
    throw "已有输出目录检查未通过：$message"
}
Write-Output "build-paths-ok: 相同目录、两个方向包含关系与已有输出目录均已拒绝"
