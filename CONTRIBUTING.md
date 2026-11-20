# 参与开发

变更需要说明对应的用户操作、涉及的 Marc 选项和实际验证文件。保持文件处理、几何与界面职责独立。

使用真实模型和真实请求验证功能。涉及 DAT 写入时，必须检查原始编号、载荷阶段、未编辑内容的字节一致性，并重新读取输出。求解器执行结果与程序测试结果分别报告。

请勿提交客户模型、材料、求解结果、会话凭据或个人路径。使用公共测试数据时记录来源、固定版本与许可。

Python 检查运行 `python -m pytest`；网页检查运行 `npm run build`。浏览器检查通过 `SFMARC_TEST_DAT` 和 `SFMARC_TEST_TOKEN` 指定本机测试环境，运行 `npm run test:browser`。

固有应变与局部结果检查通过环境变量指定真实测试数据，数据文件保留在本机：

| 环境变量 | 测试数据 |
| --- | --- |
| `SFMARC_TEST_MATERIAL` | C19000 XMT 材料文件 |
| `SFMARC_TEST_C0002_DAT` | C0002 DAT 模型 |
| `SFMARC_TEST_LOCAL_RESULTS` | 包含 `model-match.json`、`shell-equivalent.json`、`increments.csv` 等文件的局部结果目录 |
| `SFMARC_TEST_LOCAL_ARCHIVE` | Simufact 导出的真实局部结果 ZIP |
| `SFMARC_TEST_INHERENT_DAT` | 固有应变浏览器检查使用的 4 × 2 mm 小型 DAT 模型，包含上下板层、夹具与 0.01 s 时间安排 |

未配置数据时，相关检查显示跳过；已经配置但文件不存在时，检查直接报错。小规模核心检查命令：

```powershell
python -m pytest -q tests/test_dat_workflow.py tests/test_inherent_strain.py tests/test_plate_units.py tests/test_temperature_job_physical.py
```

这些检查使用短时间温度历史和真实材料，不执行完整生产模型计算。固有应变浏览器检查运行 `npx playwright test --config playwright.inherent.config.ts`，局部结果浏览器检查运行 `npx playwright test --config playwright.shell.config.ts`。
