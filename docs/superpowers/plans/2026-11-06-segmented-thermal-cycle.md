# 分段热循环实现计划

目标：统一采集焊线附近真实节点温度，每个焊路分段生成独立轨迹、热源和热循环表格。

用户已确认：全模型选择最近节点，等距选择一个；温度按照热源经过节点的时间平移后进行算术平均。界面与脚本并行修改。当前目录更新，不发布 GitHub，不删除历史交付。

## 数据和行为

- 保留 DAT 原始折线和方向，按累计长度确定分段端点。每条焊路支持最大长度或段数，默认最大长度 2 mm。
- 连续折线上的最近节点采用距离函数的下包络计算，使用 cKDTree 限定候选节点。采样不依赖用户设置的间距。只包含实际组件网格节点。
- 所有焊路的采样集合按组件和节点编号去重。一个节点可以服务多个分段和多次焊接，导出温度只保留一次。
- 搜索参数为 search_mm；divisions 字典以内部 path id 的字符串为键，值为 mode（length 或 count）和 value。
- 每条路径提供 label、division、segments。每段提供 path_id、index（从 1 开始）、label、start_mm、end_mm、length_mm、points_mm、normals、start_s、end_s、samples、radius_mm、depth_mm。
- samples 包含 component、node_id、station_mm、arrival_s、distance_mm 和 xyz_mm。节点列表保留 paths 和 segments 关联。
- 修改分段参数后重新计算成员和曲线；真实采样节点集合不变时继续使用已导入数据。修改节点集合后清除导出及温度有效状态。
- CSV 核对真实节点集合、组件、DAT 摘要和每个增量的完整性。所有采样节点温度参加计算，不能仅保存代表节点。
- 每段平均使用各节点的相对时间 t-arrival_s，线性插值到共同有效时间内的时间点，逐时刻等权平均，然后简化曲线并归一化。禁止对缺失记录外推。保留 Full thermal cycle=false 与原始工序时序，表格限定在段运动时间窗口，零点对应段中心；结束后解除该段温度约束。
- 热源温度表使用该分段的平均曲线；每段生成独立 WeldLine、Trajectory 和热源参数，XML subDivisionMode=noDivision。保留原 Robot 和焊接/停顿时序。
- 热源尺寸覆盖本段成员节点；空采样分段及不完整温度覆盖明确显示并禁止生成错误 TC。
- 目标 Process 关闭自适应细化。采用用户 111.7z 中 properties.xml 的八项 refinement_data 设置，使用 Process 参数导入与导出方法写入并核对。

## 实施分工

- [x] sampling.py 与真实 DAT 检查：连续最近节点、去重、分段几何、成员和作用尺寸。
- [x] thermal.py/server.py：保留全部节点温度、平移平均、状态有效性、生成配置。
- [x] templates/tc_create.py：独立轨迹和热源、无细分、保留时间、关闭自适应设置。
- [x] frontend/thermal.ts、thermal.css、viewer.ts：材料参数提前、逐条分段设置、流程状态、圆点和分段名称。
- [x] 集成检查：真实 C0002 采样覆盖、旧 CSV 不匹配拒绝、真实温度子集的平均计算、XML 和脚本配置、浏览器交互、Windows 打包。

后端 84 项通过、5 项跳过；打包程序的 4 项界面检查通过；原交付目录更新后，离线 WebView2、原生文件导入、脚本保存和关闭确认检查通过。更新 32 个交付文件并核对 SHA256，保留两种启动入口。

## 验证边界

本机没有 Simufact 可执行环境。旧真实 CSV 含 398 个节点，未覆盖新的完整采样集合，末次焊接的温度记录也不完整。使用该数据检查真实曲线运算和明确拒绝缺失数据；不伪造节点温度，不声称完成 Simufact 求解验证。
