# COLD 全量运行结果

2026-10-28，在本地完成 9 组验证选参及冻结后的全部测试；没有使用测试集分数改参数。

## 数据与时间协议

- 本地正式数据：`C:/personal/Jason/pyxccd/datasets/my_project/processed/cold_full_v1`，17,764 条目标全部保留，文件哈希与服务器正式包一致。
- 验证：2019–2022 年观测，仅评价 2021–2022 年；8,793 条，其中扰动 1,134 条。
- 测试：2019–2024 年观测，仅评价 2023–2024 年；8,971 条，其中扰动 1,216 条。
- 验证候选 `p_cg={0.95,0.99,0.999}`、`conse={4,6,8}`，按验证 F1、Precision、固定顺序选优。最终参数 `p_cg=0.999`、`conse=4`。

## 结果

| 指标 | 验证集（选参用） | 测试集（最终结果） |
| --- | ---: | ---: |
| F1 | 92.00% | 85.46% |
| 漏检率 | 9.79% | 22.20% |
| 误检率（错分误差） | 6.15% | 5.21% |
| Precision | 93.85% | 94.79% |
| Recall | 90.21% | 77.80% |
| FPR | 0.87% | 0.67% |

测试集混淆矩阵：

| 参考标签 | 预测扰动 | 预测正常 |
| --- | ---: | ---: |
| 扰动 | TP=946 | FN=270 |
| 正常 | FP=52 | TN=7,703 |

F1=`2TP/(2TP+FP+FN)`；漏检率=`FN/(TP+FN)`；误检率（错分误差）=`FP/(TP+FP)`。FPR=`FP/(FP+TN)` 单列，避免混淆两种误报口径。

## 结果规则与适用范围

标准 COLD 回溯检测，保留原算法的曲线更新；仅将评价期内 `change_prob=100` 且通过预定扰动幅度规则的断点判为阳性。末尾未确认候选变化单独记录，不作已确认扰动。固定参数、规则和原生引擎身份见冻结配置。

593 条逐年观测质量标记随包保留，未插值、未删样本。现有正常样本不是独立人工稳定真值，事件日期含算法参考信息；这些分数表示与现有参考标签的一致性，不能替代独立真值验证。

本轮只交付 COLD 结果。与深度学习比较时还须保证测试 sample_id 一致、阈值由验证集确定，并披露双方输入历史范围；不能据此直接声称输入信息完全相同。

## 交付与复核

- [机器可读指标](metrics.json)、[9 组选参成绩](validation_trials.csv)。
- [测试逐样本标签与预测](test_predictions_with_labels.csv)、[最优验证逐样本结果](validation_predictions_with_labels.csv)，包含 TP/FP/FN/TN、扰动日期、未确认候选和质量标记。
- [冻结配置](../../../../datasets/my_project/results/cold_full_run_20261028_v1/tuning/locked_config.json)、[原始测试指标](../../../../datasets/my_project/results/cold_full_run_20261028_v1/test/metrics.json)、[完整 COLD 分段记录](../../../../datasets/my_project/results/cold_full_run_20261028_v1/test/records)。
- 逐文件校验正式包，逐项检查全部 9 组验证和最终测试的 ID、成功状态、预测哈希、原生记录及运行环境；独立重算混淆矩阵和指标，一致。共核查 88,108 次检测，0 个未解决错误。
- 11 项协议测试通过；本地使用官方 Windows `pyxccd==1.1.0`，不是本次从仓库编译的原生库。

复现命令（在仓库根目录执行，输出目录必须尚不存在）：

```powershell
.venv-cold/Scripts/python.exe experiments/cold_comparison/pipeline.py tune --dataset datasets/my_project/processed/cold_full_v1 --output <新运行目录>/tuning
.venv-cold/Scripts/python.exe experiments/cold_comparison/pipeline.py test --dataset datasets/my_project/processed/cold_full_v1 --lock <新运行目录>/tuning/locked_config.json --output <新运行目录>/test
.venv-cold/Scripts/python.exe experiments/cold_comparison/summarize_results.py --dataset datasets/my_project/processed/cold_full_v1 --run <新运行目录> --output <新交付目录>
```
