$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$cache = Join-Path $project 'work/webview-download'
$target = Join-Path $project 'work/webview2'
$version = '156.0.4314.58'
$digest = '857DCFD6F7035508A29645FA2978C3A6A841F220DF77CC366D318894E19F4792'
$url = 'https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/b7919cc9-680d-4253-bf32-732988e68aa6/Microsoft.WebView2.FixedVersionRuntime.156.0.4314.58.x64.cab'
if (Test-Path (Join-Path $target 'msedgewebview2.exe')) {
    $runtime = Get-Item (Join-Path $target 'msedgewebview2.exe')
    if ($runtime.VersionInfo.ProductVersion -eq $version -and (Get-AuthenticodeSignature $runtime.FullName).Status -eq 'Valid') {
        Write-Output "WebView2 $version 已准备完成"
        exit 0
    }
    throw '现有运行环境与构建版本不符，请检查 work/webview2'
}
New-Item -ItemType Directory -Force $cache, $target | Out-Null
$archive = Join-Path $cache 'runtime.cab'
if (!(Test-Path $archive)) {
    Invoke-WebRequest $url -OutFile $archive
}
if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $digest) {
    throw 'WebView2 下载文件摘要不符'
}
& expand.exe $archive '-F:*' $cache | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'WebView2 解压失败' }
$runtime = Get-Item (Join-Path $cache "Microsoft.WebView2.FixedVersionRuntime.$version.x64/msedgewebview2.exe")
if ((Get-AuthenticodeSignature $runtime.FullName).Status -ne 'Valid') {
    throw 'WebView2 数字签名无效'
}
Copy-Item -Path (Join-Path $runtime.Directory.FullName '*') -Destination $target -Recurse -Force
Write-Output "WebView2 $version 已准备完成"
