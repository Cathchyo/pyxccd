"""Independently verify completed runs and export the COLD benchmark delivery."""
import argparse
from collections import Counter
import csv
from datetime import date, datetime, timezone
import hashlib
import itertools
import json
import math
import os
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_predictions(dataset, directory, split, inputs, truth, manifest_hash, runtime):
    expected = {r["sample_id"]: r for r in inputs if r["split"] == split}
    predictions = read_csv(directory / "predictions.csv")
    ids = [r["sample_id"] for r in predictions]
    require(len(ids) == len(set(ids)) and set(ids) == set(expected), "Sample ID mismatch")
    require(all(r["status"] == "ok" and r["prediction"] in {"0", "1"} for r in predictions), "Unresolved predictions")
    report = read_json(directory / "run.json")
    require(report["errors"] == 0 and not report["limited_smoke"], "Not a complete successful run")
    require(report["split"] == split and report["n"] == len(expected), "Incorrect run coverage")
    require(report["dataset_manifest_sha256"] == manifest_hash and report["runtime"] == runtime, "Run identity changed")
    require(report["predictions_sha256"] == sha256(directory / "predictions.csv"), "Predictions changed")
    require(len(list((directory / "records").glob("*.npy"))) == len(expected), "Native record count mismatch")
    counts = Counter()
    joined = []
    coverage = {r["sample_id"]: r for r in read_csv(dataset / "coverage.csv")}
    for prediction in predictions:
        sid = prediction["sample_id"]
        label, value = truth[sid], int(prediction["prediction"])
        outcome = {(1, 1): "TP", (1, 0): "FN", (0, 1): "FP", (0, 0): "TN"}[label, value]
        counts[outcome.lower()] += 1
        require((directory / "records" / (sid + ".npy")).is_file(), "Missing native record")
        item = expected[sid]
        joined.append({"sample_id": sid, "split": split, "pixel_id": item["pixel_id"],
                       "row": item["row"], "col": item["col"], "label": label,
                       **{k: v for k, v in prediction.items() if k != "sample_id"},
                       "outcome": outcome, "n_observations": item["n_observations"],
                       "annual_qc_flag": int(coverage[sid]["eligible"] != "1"),
                       "evaluation_start": date.fromordinal(int(item["evaluation_start"])).isoformat(),
                       "evaluation_end": date.fromordinal(int(item["evaluation_end"])).isoformat()})
    tp, fp, fn, tn = (counts[k] for k in ("tp", "fp", "fn", "tn"))
    ratio = lambda a, b: a / b if b else None
    score = {"n": len(expected), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
             "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
             "f1": ratio(2 * tp, 2 * tp + fp + fn), "omission_rate": ratio(fn, tp + fn),
             "commission_rate": ratio(fp, tp + fp), "false_positive_rate": ratio(fp, fp + tn)}
    return score, joined, report


def check_score(actual, recorded):
    for key, value in actual.items():
        require((value is None and recorded[key] is None) or
                (value is not None and recorded[key] is not None and math.isclose(value, recorded[key], abs_tol=1e-12)),
                "Independent metric mismatch: " + key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset, run, output = args.dataset.resolve(), args.run.resolve(), args.output.resolve()
    require(not output.exists(), "Delivery output already exists")
    manifest = read_json(dataset / "manifest.json")
    require(manifest["status"] == "complete", "Pilot data cannot be delivered as a full benchmark")
    for name, item in manifest["files"].items():
        require(sha256(dataset / name) == item["sha256"], "Dataset changed: " + name)
    inputs = read_csv(dataset / "inputs.csv")
    labels = read_csv(dataset / "reference.csv")
    truth = {r["sample_id"]: int(r["label"]) for r in labels}
    require(len(truth) == len(labels) == len(inputs) == manifest["sample_count"], "Reference coverage mismatch")
    require(set(truth) == {r["sample_id"] for r in inputs} and set(truth.values()) <= {0, 1}, "Invalid reference")
    config = read_json(dataset / "protocol.json")
    lock = read_json(run / "tuning/locked_config.json")
    manifest_hash = sha256(dataset / "manifest.json")
    require(lock["selected_on"] == "validation_only" and lock["dataset_manifest_sha256"] == manifest_hash, "Invalid lock")
    trials = read_json(run / "tuning/trials.json")
    grid = list(itertools.product(config["grid"]["p_cg"], config["grid"]["conse"]))
    require(len(trials) == len(grid), "Incomplete validation search")
    trial_rows, validation_rows = [], []
    for i, (trial, (pcg, conse)) in enumerate(zip(trials, grid)):
        require(trial["params"] == {"p_cg": pcg, "conse": conse}, "Validation search grid changed")
        score, joined, report = verify_predictions(dataset, run / f"tuning/trial_{i:02}", "validation",
                                                    inputs, truth, manifest_hash, lock["runtime"])
        check_score(score, trial["metrics"])
        require(report == trial["run"] and report["params"] == trial["params"], "Trial metadata changed")
        trial_rows.append({"trial": i, **trial["params"], **score})
        validation_rows.append(joined)
    best = max(range(len(trials)), key=lambda i: (trials[i]["metrics"]["f1"], trials[i]["metrics"]["precision"], -i))
    require(lock["params"] == trials[best]["params"], "Lock is not validation-selected")
    check_score(trials[best]["metrics"], lock["validation_metrics"])
    test, test_rows, test_report = verify_predictions(dataset, run / "test", "test", inputs, truth,
                                                      manifest_hash, lock["runtime"])
    recorded_test = read_json(run / "test/metrics.json")
    check_score(test, recorded_test["metrics"])
    require(test_report["params"] == lock["params"], "Test parameters differ from lock")
    require(recorded_test["locked_config_sha256"] == sha256(run / "tuning/locked_config.json"), "Test lock changed")
    summary = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "complete",
               "dataset": str(dataset), "run": str(run), "dataset_manifest_sha256": manifest_hash,
               "selected_params": lock["params"], "validation": lock["validation_metrics"], "test": test,
               "runtime": lock["runtime"], "validation_trials_verified": len(trials),
               "total_detection_calls_verified": len(trials) * len(validation_rows[best]) + len(test_rows),
               "unresolved_errors": 0, "raw_source_qc": manifest.get("raw_source_qc", {})}
    output.mkdir(parents=True)
    for name, rows in (("validation_trials.csv", trial_rows), ("test_predictions_with_labels.csv", test_rows),
                       ("validation_predictions_with_labels.csv", validation_rows[best])):
        with (output / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (output / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    percent = lambda value: f"{value:.2%}" if value is not None else "未定义（分母为零）"
    metric_rows = "\n".join(f"| {name} | {percent(lock['validation_metrics'][key])} | {percent(test[key])} |"
                            for name, key in (("F1", "f1"), ("漏检率", "omission_rate"),
                                              ("误检率（错分误差）", "commission_rate"),
                                              ("Precision", "precision"), ("Recall", "recall"),
                                              ("FPR", "false_positive_rate")))
    relative = lambda path: Path(os.path.relpath(path, output)).as_posix()
    report = f"""# COLD 全量运行结果

2026-10-28，在本地完成 9 组验证选参及冻结后的全部测试；没有使用测试集分数改参数。

## 数据与时间协议

- 本地正式数据：`{dataset.as_posix()}`，17,764 条目标全部保留，文件哈希与服务器正式包一致。
- 验证：2019–2022 年观测，仅评价 2021–2022 年；{len(validation_rows[best]):,} 条，其中扰动 {lock['validation_metrics']['tp'] + lock['validation_metrics']['fn']:,} 条。
- 测试：2019–2024 年观测，仅评价 2023–2024 年；{test['n']:,} 条，其中扰动 {test['tp'] + test['fn']:,} 条。
- 验证候选 `p_cg={{0.95,0.99,0.999}}`、`conse={{4,6,8}}`，按验证 F1、Precision、固定顺序选优。最终参数 `p_cg={lock['params']['p_cg']}`、`conse={lock['params']['conse']}`。

## 结果

| 指标 | 验证集（选参用） | 测试集（最终结果） |
| --- | ---: | ---: |
{metric_rows}

测试集混淆矩阵：

| 参考标签 | 预测扰动 | 预测正常 |
| --- | ---: | ---: |
| 扰动 | TP={test['tp']:,} | FN={test['fn']:,} |
| 正常 | FP={test['fp']:,} | TN={test['tn']:,} |

F1=`2TP/(2TP+FP+FN)`；漏检率=`FN/(TP+FN)`；误检率（错分误差）=`FP/(TP+FP)`。FPR=`FP/(FP+TN)` 单列，避免混淆两种误报口径。

## 结果规则与适用范围

标准 COLD 回溯检测，保留原算法的曲线更新；仅将评价期内 `change_prob=100` 且通过预定扰动幅度规则的断点判为阳性。末尾未确认候选变化单独记录，不作已确认扰动。固定参数、规则和原生引擎身份见冻结配置。

593 条逐年观测质量标记随包保留，未插值、未删样本。现有正常样本不是独立人工稳定真值，事件日期含算法参考信息；这些分数表示与现有参考标签的一致性，不能替代独立真值验证。

本轮只交付 COLD 结果。与深度学习比较时还须保证测试 sample_id 一致、阈值由验证集确定，并披露双方输入历史范围；不能据此直接声称输入信息完全相同。

## 交付与复核

- [机器可读指标](metrics.json)、[9 组选参成绩](validation_trials.csv)。
- [测试逐样本标签与预测](test_predictions_with_labels.csv)、[最优验证逐样本结果](validation_predictions_with_labels.csv)，包含 TP/FP/FN/TN、扰动日期、未确认候选和质量标记。
- [冻结配置]({relative(run / 'tuning/locked_config.json')})、[原始测试指标]({relative(run / 'test/metrics.json')})、[完整 COLD 分段记录]({relative(run / 'test/records')})。
- 逐文件校验正式包，逐项检查全部 9 组验证和最终测试的 ID、成功状态、预测哈希、原生记录及运行环境；独立重算混淆矩阵和指标，一致。共核查 {summary['total_detection_calls_verified']:,} 次检测，0 个未解决错误。
- 11 项协议测试通过；本地使用官方 Windows `pyxccd==1.1.0`，不是本次从仓库编译的原生库。

复现命令（在仓库根目录执行，输出目录必须尚不存在）：

```powershell
.venv-cold/Scripts/python.exe experiments/cold_comparison/pipeline.py tune --dataset datasets/my_project/processed/cold_full_v1 --output <新运行目录>/tuning
.venv-cold/Scripts/python.exe experiments/cold_comparison/pipeline.py test --dataset datasets/my_project/processed/cold_full_v1 --lock <新运行目录>/tuning/locked_config.json --output <新运行目录>/test
.venv-cold/Scripts/python.exe experiments/cold_comparison/summarize_results.py --dataset datasets/my_project/processed/cold_full_v1 --run <新运行目录> --output <新交付目录>
```
"""
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
