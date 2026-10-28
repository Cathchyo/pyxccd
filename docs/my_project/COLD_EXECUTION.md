# COLD 执行记录

## 最新交付：本地全量运行完成

用户后续明确要求将正式数据包下载本地并交付完整 COLD 结果。本次已下载到 `datasets/my_project/processed/cold_full_v1/`，35,509,064 字节，清单 SHA-256 与服务器一致，所有包内文件逐项校验通过；没有下载全量原始影像。

本地使用同一隔离环境完成 9 组验证选参，冻结 `p_cg=0.999、conse=4` 后运行全部 8,971 条测试样本。结果目录 `datasets/my_project/results/cold_full_run_20261028_v1/`：测试 F1 **85.46%**、漏检率 **22.20%**、误检率（错分误差）**5.21%**；TP=946、FP=52、FN=270、TN=7,703，0 个运行错误。

交付报告及逐样本明细见 [全量结果报告](reports/cold_full_run_20261028_v1/REPORT.md)。`summarize_results.py` 独立核查全部 88,108 次检测的覆盖与状态，并重算 9 组验证及最终测试指标，和原始结果一致。11 项协议测试再次通过。下文保留此前构建阶段记录；其中未下载、未评价等状态以本节更新为准。

## 本地已完成

2026-10-28，原目录 `C:/personal/Jason/reviewed-hls-51rtq/data/temporal_disturbance_51RTQ_v1` 只读；复制到当前项目 `datasets/my_project/raw/hls51rtq_snapshot/`。共 1,509 个必要文件，59,962,325 字节，复制前、复制后源文件和副本 SHA-256 一致。原始数据没有微调；格式适配写入 `processed/`。

本地缓存包含六年的观测，但并非所有像元各年都被抽样保留。审计的验证目标 8,793 条，历史满足规则 1,350 条（38 正类）；测试目标 8,971 条，历史满足规则 652 条（4 正类）。剩余情况逐条记录在 `reports/local_coverage/history_requests.csv`，不能把子集当作完整测试集。

已生成 `datasets/my_project/processed/cold_pilot_v1/`，共 2,002 条样本。日期、光谱与标签分开保存。使用 `.venv-cold` 中官方 Windows `pyxccd==1.1.0`；Python 接口与当前仓库 `cold_detect` 一致，原生二进制由发布 wheel 提供，并非本次编译源码。

验证结果：

- 11 项协议测试在本地和服务器均通过，涵盖重叠冲突、时间截断、检测不读取标签、断点过滤、配对、指标、覆盖不足、服务器提取同日合并、只在验证集选参、运行引擎冻结和源观测稀疏时保留全部目标。
- 仓库参考样本的断点 `735040`、第一段观测数 `142`、第二段结束日期 `737368` 三个数值断言通过。
- `RUN_COLD_COMPARISON.ps1 -Stage Smoke` 在真实验证样本 32 条上成功，0 个错误。运行目录 `datasets/my_project/results/cold_smoke_v2/`；产生完整分段、2 个区间阳性预测和末尾候选变化记录。该小样本结果不作为 F1 成绩。

本轮没有训练深度学习模型，也没有用测试分数修改参数。

## 服务器执行

用户指定 `wangshengjie@10.75.68.230:25012`，通过既有 SSH 别名 `hls-nrt-server` 连接。原始数据仍在 `/gpfs/home/stack/51RTQ_stack`。仅在新目录 `/gpfs/home/wangshengjie/data-pre/cold_comparison_20261028_v1/` 写入脚本、隔离环境和派生数据，不修改旧数据集及原环境，不将原始影像或正式数据包下载到本地。

服务器 `snapshot/` 同样复制并验证 1,509 个必要文件，60,241,419 字节；服务器来源说明与本地副本的元数据大小有差异，两个来源各自保留清单，不能混称为同一文件哈希。两边目标数量及覆盖审计一致。

服务器隔离环境 `environment/` 使用 Python 3.11.7、NumPy 2.0.0 和 pyxccd 1.1.0，不改动已有 `reproducible_core/.venv`。`code/server_pipeline.py` 已完成审计、提取 483 个 Block 的目标像元历史、再次审计、构建正式包及真实 COLD 检查。共检查 147,058 个源 NPY 文件，读取前后 SHA-256 一致。小型验收记录见 [server_execution_summary.json](reports/server_execution_summary.json)。

正式数据包已完成，路径为 `/gpfs/home/wangshengjie/data-pre/cold_comparison_20261028_v1/prepared/`：

- `manifest.json` 状态为 `complete`，共 17,764 条样本：验证 8,793 条、测试 8,971 条，全部原定目标均保留。
- 数据包共 35,509,064 字节；清单 SHA-256 为 `27a36d66b981496711139520d85b72018eed8d8e014ad789b1637b81813cd957`。
- 服务器使用正式包运行 32 条真实验证样本，0 个错误，输出保存在 `smoke/`；这只是可运行性检查，不是正式 F1 评价。
- 本地与服务器的 `cold_detect` 函数文本经统一换行后哈希一致；原生库分别记录身份，不宣称跨平台二进制一致。

查看状态（只输出小型状态文本）：

```powershell
ssh hls-nrt-server cat /gpfs/home/wangshengjie/data-pre/cold_comparison_20261028_v1/execution_status.json
ssh hls-nrt-server tail -n 8 /gpfs/home/wangshengjie/data-pre/cold_comparison_20261028_v1/history.log
```

补齐后仍有验证 221 条、测试 372 条逐年质量标记，主要为有效观测少或缺口；不是尚未找到原始文件。已通过 `--accept-source-qc` 验证这些像元及各年源扫描记录，保留全部目标、真实稀疏观测及质量标记，没有插值或删样本。首次严格拒绝记录保存在 `prepare.log` 中，后续独立阶段日志保留以便追溯。

## 服务器复跑入口（本轮全量运行已在本地完成）

服务器上尚未另行运行全量选参和测试评分；如需在服务器复跑，可登录后执行以下命令。必须使用隔离环境解释器，系统默认 Python 版本不适用。输出目录必须尚不存在。Linux 结果需独立选参冻结，不能直接复用 Windows 运行环境锁。

```bash
cd /gpfs/home/wangshengjie/data-pre/cold_comparison_20261028_v1
./environment/bin/python code/pipeline.py tune --dataset prepared --output tuning
./environment/bin/python code/pipeline.py test --dataset prepared --lock tuning/locked_config.json --output test
```

对比时还需要深度学习模型在同一批两年测试窗口上导出的 `sample_id,prediction` 文件，以及其验证阈值、checkpoint 和历史输入说明。历史信息不同需在结果中明示，不能只以最终测试样本相同就宣称严格控制了输入变量。拿到预测文件后，从同一服务器工作目录执行：

```bash
./environment/bin/python code/pipeline.py compare --dataset prepared --cold test/predictions.csv --deep /path/to/deep_predictions.csv --output comparison
```
