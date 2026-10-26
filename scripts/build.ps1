param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force work/temp | Out-Null
$env:TEMP = Join-Path $PWD "work/temp"
$env:TMP = $env:TEMP
npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw "网页依赖安装失败" }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw "网页构建失败" }
& $Python -m PyInstaller --noconfirm --distpath release --workpath work/build workbench.spec
if ($LASTEXITCODE -ne 0) { throw "Windows 打包失败" }
Write-Output "程序位置：release/sfMarc-Workbench/sfMarc-Workbench.exe"
