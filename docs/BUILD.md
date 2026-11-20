# Windows 完整软件包构建

在项目目录执行以下命令，`ExistingPackage` 指向已有的完整 Web UI 软件包，`Python` 指向包含项目依赖和 PyInstaller 的 Python 环境：

```powershell
./scripts/build.ps1 -ExistingPackage './existing/SIMUWELD-SD' -Python './.venv/Scripts/python.exe'
```

默认输出为 `release/SIMUWELD-SD`，新增模块版本为 `0.9.7-thermal-preview`。可以通过 `-Destination` 和 `-Version` 指定其他输出目录与版本。输出目录必须尚未存在，与已有软件包目录不能相同，也不能互相包含；源目录和输出目录的上级目录均不能使用目录链接。

构建复用现有网页依赖；缺少 Vite 时执行 `npm ci`。PyInstaller 产物保存在 `work/web-runtime` 下的独立目录。完整输出包含新 EXE、新运行依赖、已有软件包的内部资源、四个原模块、根目录 Markdown 和 TXT 说明，以及重新构建的 `shared` 和 `inherent_strain`。已有软件包保持只读，用户运行数据目录 `work` 不会复制到输出。

以下检查使用实际软件包文件：

```powershell
./tests/check_build_paths.ps1 -ExistingPackage './existing/SIMUWELD-SD'
python tests/check_build_package.py './existing/SIMUWELD-SD' release/SIMUWELD-SD
python scripts/package_webui.py release/SIMUWELD-SD release/SIMUWELD-SD.zip
```

`check_build_package.py` 应当在第一次启动构建产物之前执行，以检查输出没有包含用户运行数据。
