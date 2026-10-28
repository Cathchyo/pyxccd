# COLD 对比实验入口

协议见 [计划](../../docs/my_project/COLD_COMPARISON_PLAN.md)。本目录代码只写显式指定的新输出目录，不覆盖原数据。所有命令从当前仓库根目录执行。

本地全量验证选参和测试已完成：[最终结果报告](../../docs/my_project/reports/cold_full_run_20261028_v1/REPORT.md)。测试 F1 85.46%、漏检率 22.20%、误检率（错分误差）5.21%，共 8,971 条，0 个错误。正式数据已按用户后续要求下载到 `datasets/my_project/processed/cold_full_v1/`，原始影像未下载。

## 本地已可使用

```powershell
# 已建立 .venv-cold；换机器时安装 uv 后执行：
.\RUN_COLD_COMPARISON.ps1 -Stage Setup

# 已复制并校验的数据，不需要再次 Copy
.\RUN_COLD_COMPARISON.ps1 -Stage Audit

# 在已下载的正式包上抽查真实 COLD，保存分段模型及预测；该抽查不计算正式成绩
.\RUN_COLD_COMPARISON.ps1 -Stage Smoke
```

本地来源副本：`datasets/my_project/raw/hls51rtq_snapshot/`；完整正式包：`datasets/my_project/processed/cold_full_v1/`。旧 `processed/cold_pilot_v1/` 只用于流程验证，不代表完整基准。PowerShell 入口默认使用正式包。新输出目录必须不存在，便于保留每次运行记录。

`requirements-lock.txt` 固定本次 Windows 环境；运行引擎为官方 `pyxccd==1.1.0` 二进制包，不声称由当前 checkout 编译。Python `cold_detect` 与 checkout 内容一致，并通过仓库参考样本的三个数值断言；无法仅凭此保证本地与发布版的全部 native 实现相同。服务器单独记录 Linux 环境和原生库哈希，验证、测试在同一环境运行。

## 命令行完整流程

```powershell
$python = '.venv-cold/Scripts/python.exe'
$entry = 'experiments/cold_comparison/pipeline.py'
& $python $entry snapshot --source <原数据目录> --output <新副本目录>
& $python $entry audit --snapshot <副本> --output <新审计目录>
& $python $entry prepare --snapshot <副本> --history <补充历史包> --accept-source-qc --output <完整数据包>
& $python $entry smoke --dataset <完整数据包> --output <新检查目录> --limit 32
& $python $entry tune --dataset <完整数据包> --output <新验证目录>
& $python $entry test --dataset <完整数据包> --lock <验证目录>/locked_config.json --output <新测试目录>
& $python $entry compare --dataset <完整数据包> --cold <测试目录>/predictions.csv --deep <深度学习预测.csv> --output <新对比目录>
```

`prepare --pilot` 允许构建明确标记的历史完整子集；默认不允许缺历史的正式包。原始 stack 已提取后，`--accept-source-qc` 必须验证被标记像元及其各年份源文件扫描记录，才允许保留自然稀疏观测并打包全部目标；它不能用来绕过未找到历史的像元。质量告警保留在 manifest 和 coverage 文件中。`tune` 只读取验证标签；`test` 检查冻结配置、包哈希及运行库身份。检测失败保留错误并拒绝汇总评分，不能删样本。`compare` 要求所有测试 sample_id 恰好出现一次，预测必须是 0/1。

深度学习预测 CSV 为 `sample_id,prediction`，须来自在验证集确定阈值的冻结模型；不能上传概率后在测试集优化阈值。当前比较脚本能检查配对，但不能自动证明深度学习训练过程正确，需同时记录 checkpoint、验证阈值及实际输入历史范围。

## 服务器提取历史

用户指定服务器 `wangshengjie@10.75.68.230:25012`；本机 SSH 别名 `hls-nrt-server` 已配置密钥。原始 stack 保持在 `/gpfs/home/stack/51RTQ_stack`，不下载原影像。正式像元序列包已按用户后续要求下载本地。服务器工作目录为 `/gpfs/home/wangshengjie/data-pre/cold_comparison_20261028_v1/`。

```bash
cd /gpfs/home/wangshengjie/data-pre/cold_comparison_20261028_v1
./environment/bin/python code/pipeline.py extract-history \
  --requests audit/history_requests.csv \
  --stack-root /gpfs/home/stack/51RTQ_stack \
  --output history --config code/config.json
./environment/bin/python code/pipeline.py audit --snapshot snapshot --history history --output audit_after_history
./environment/bin/python code/pipeline.py prepare --snapshot snapshot --history history --accept-source-qc --output prepared
```

以上构建阶段已完成，已有目录不要重复运行或覆盖。正式包 `prepared/` 包含验证 8,793 条、测试 8,971 条；32 条真实验证样本的 COLD 检查成功，11 项协议测试通过。状态与验收记录见 [执行记录](../../docs/my_project/COLD_EXECUTION.md)。服务器原包保留，本地全量运行使用经过哈希验证的下载副本。

服务器复跑命令（服务器未执行；本地已完成同一流程。从上述服务器工作目录运行）：

```bash
./environment/bin/python code/pipeline.py tune --dataset prepared --output tuning
./environment/bin/python code/pipeline.py test --dataset prepared --lock tuning/locked_config.json --output test
./environment/bin/python code/pipeline.py compare --dataset prepared --cold test/predictions.csv --deep /path/to/deep_predictions.csv --output comparison
```

提取器按 Block 读取目标像元，筛选源 QA=0、六波段都在 (0,10000)，按同日均值合并。读取前后验证原文件 SHA-256，补充包保存来源清单。完整历史仍需再审计，不能以“脚本结束”代替覆盖验收。新增历史与现有缓存重叠日期数值冲突时停止，不选择性覆盖。服务器整套构建可执行 `./environment/bin/python code/server_pipeline.py --root <工作目录> --accept-source-qc`，状态见 `execution_status.json`；追加 `--benchmark` 才会进一步验证选参和最终评分。

## 文件契约

- `inputs.csv`：sample_id、像元、数组起止索引、历史起点及评价期；无标签。
- `observations.npz`：ordinal `days`、原尺度 `spectra (N,6)`、`qas`；不补齐缺测。进入 COLD 时按原接口转换为 int64，同日小数向零截断。
- `reference.csv`：二分类标签，检测函数不读取其内容。
- `protocol.json`、`manifest.json`：时间、QC、算法规则、来源和文件哈希。
- `coverage.csv`、`history_requests.csv`：全部目标及排除原因，防止静默挑选样本。
- 运行目录 `records/*.npy`：标准 COLD 完整分段记录；`predictions.csv`：区间级结果、候选变化及错误；`run.json`：配置、引擎及代码哈希。
- 正式测试 `metrics.json` 和对比 `comparison.json`：F1、漏检率、错分误差、FPR 和混淆矩阵；不是日期定位精度。

## 检查

```powershell
.venv-cold/Scripts/python.exe -m unittest discover -s experiments/cold_comparison -p test_pipeline.py -v
```
