# 我的项目

本目录用于保存项目研究背景、方法说明和后续对比实验的设计与分析。

## 研究背景

请将已有的研究背景文件放到 `background/`，可保留原来的文件名和格式，例如 Markdown、Word 或 PDF。

后续将结合研究背景，确定自己的方法与 CCDC、COLD、SCCD 的对比任务、参数和评价指标。

## COLD 对比实验

- [COLD 全量结果报告](reports/cold_full_run_20261028_v1/REPORT.md)：本地完整验证选参与测试，包含 F1、漏检率、误检率及逐样本结果。
- [构建与评价计划](COLD_COMPARISON_PLAN.md)：历史输入、验证/测试时间、断点规则及指标口径。
- [执行说明](../../experiments/cold_comparison/README.md)：本地入口、服务器提取及数据文件格式。
- [本地历史覆盖审计](reports/local_coverage/summary.json)：完整目标与可运行子集的区别。
- [执行状态](COLD_EXECUTION.md)：已完成验证、数据位置及服务器进展。

## 数据集位置

数据集统一放在仓库根目录的 [`datasets/my_project/`](../../datasets/my_project/README.md)。

在开始实验前，记录数据来源、研究区域、时间范围、波段、质量控制规则，以及参考标注的含义。
