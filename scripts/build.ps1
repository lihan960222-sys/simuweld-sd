param(
    [Parameter(Mandatory = $true)][string]$ExistingPackage,
    [string]$Python = "python",
    [string]$Version = "0.9.7-thermal-preview",
    [string]$Destination = "release/SIMUWELD-SD"
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$baseline = (Resolve-Path -LiteralPath $ExistingPackage).Path.TrimEnd('\', '/')
$destinationRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination).TrimEnd('\', '/')
if ($baseline -eq $destinationRoot -or
    $destinationRoot.StartsWith($baseline + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    $baseline.StartsWith($destinationRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "输出目录与已有软件包目录不能相同，也不能互相包含"
}
foreach ($path in @($baseline, $destinationRoot)) {
    $ancestor = $path
    while ($ancestor) {
        if ((Test-Path -LiteralPath $ancestor) -and
            ((Get-Item -LiteralPath $ancestor).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "软件包源目录和输出目录不能经过链接目录：$ancestor"
        }
        $ancestor = Split-Path -Parent $ancestor
    }
}
if (Test-Path -LiteralPath $destinationRoot) { throw "输出目录已经存在，请指定新的 Destination：$destinationRoot" }
$preservedModules = @("face_load", "weld_paths", "thermal_cycle", "local_trial")
$required = @("SIMUWELD-SD-Web.exe", "_internal/LOCAL_TRIAL.md", "使用说明.md", "模块更新说明.md")
$required += $preservedModules | ForEach-Object { "modules/$_/module.json" }
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $baseline $relative) -PathType Leaf)) {
        throw "已有软件包缺少必要文件：$relative"
    }
}
New-Item -ItemType Directory -Force work/temp | Out-Null
$env:TEMP = Join-Path $PWD "work/temp"
$env:TMP = $env:TEMP
if (-not (Test-Path -LiteralPath "node_modules/.bin/vite.cmd" -PathType Leaf)) {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "网页依赖安装失败" }
}
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw "网页构建失败" }
$runtimeRoot = Join-Path $PWD ("work/web-runtime/" + [Guid]::NewGuid().ToString("N"))
& $Python -m PyInstaller --noconfirm --distpath (Join-Path $runtimeRoot "dist") --workpath (Join-Path $runtimeRoot "build") webui.spec
if ($LASTEXITCODE -ne 0) { throw "Windows 打包失败" }
New-Item -ItemType Directory -Path $destinationRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $baseline "_internal") -Destination $destinationRoot -Recurse
New-Item -ItemType Directory -Path (Join-Path $destinationRoot "modules") | Out-Null
foreach ($ident in $preservedModules) {
    Copy-Item -LiteralPath (Join-Path $baseline "modules/$ident") -Destination (Join-Path $destinationRoot "modules") -Recurse
}
Get-ChildItem -LiteralPath $baseline -File | Where-Object { $_.Extension -in @(".md", ".txt") } |
    Copy-Item -Destination $destinationRoot
Copy-Item -Path (Join-Path $runtimeRoot "dist/SIMUWELD-SD/*") -Destination $destinationRoot -Recurse -Force
& $Python scripts/build_modules.py $destinationRoot --version $Version --only shared inherent_strain
if ($LASTEXITCODE -ne 0) { throw "模块构建失败" }
& $Python -c "import sys; from runtime_modules import validate_modules; validate_modules(sys.argv[1])" $destinationRoot
if ($LASTEXITCODE -ne 0) { throw "完整软件包模块校验失败" }
Write-Output "程序位置：$(Join-Path $destinationRoot 'SIMUWELD-SD-Web.exe')"
