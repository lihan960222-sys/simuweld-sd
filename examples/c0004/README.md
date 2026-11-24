# C0004 双板固有应变试算

本目录保存室温双板算例的求解代码与重建后的壳几何、压区和四条焊路。SD 顶栏的「双板算例」可查看这次保存的结果。常规 DAT 计算入口仍使用其原有求解器。

两块 C19000 板从 25 °C 起焊。上板中间约 0.05 mm、周边约 0.08 mm，底板等厚 0.30 mm，保留外围高台。底托全覆盖，允许法向脱开和面内滑动。热源参数与功率曲线见装配后的工艺 JSON。

1. 热计算采用面内变尺寸有限体积，每板厚度分五层。焊路附近网格 0.2 mm，远处 1.6 mm。每步推进面内焓扩散和厚向传热，面内扩散率取初温值。
2. 每块板独立求膜内相容应变并更新平面应力塑性状态，再由五个厚度点算出弯曲修正。修正前后保持厚度加权平均塑性应变相同。压头只参与位移求解。
3. 把分层固有应变和末帧热应变一次映射到 MITC4 壳。焊缝约束连接两板，先求压紧平衡，再逐步卸除上压头，底托保留。结构网格 1.0 mm。

## 复算

在仓库的 Python 环境中安装项目依赖与 Pillow，进入本目录，用 PowerShell 执行。把 C19000-TM00-sw.xmt 放在本目录或通过 C0004_MATERIAL 指定其路径。温历文件约 1.1 GB。

```powershell
$env:C0004_RESULT_DIR='整板分层_2_初温25C'
$env:OPENBLAS_NUM_THREADS='1'
python case_heat.py 2 25
python case_strain.py 2 0 2
python case_strain.py 2 1 2
python case_balance.py 2 10 --soft-release
python case_report.py 2 10
python case_temperature_movie.py $env:C0004_RESULT_DIR
```

本机实测温度 201.5 s，两板收缩并行约 262.6 s，结构 639.1 s。最高温超过 2000 °C 材料表上限，计算沿用末端物性，未模拟蒸发。1.6 mm 与 1.0 mm 网格的上板端部翘高分别为 1.96、1.99 mm，局部横向鼓高分别为 0.052、0.097 mm。此双板简化工况尚无同条件 Simufact 结果验证。
