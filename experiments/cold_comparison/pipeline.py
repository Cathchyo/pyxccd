"""Prepare HLS observations, run standard COLD, and compare aligned predictions.

All mutations are confined to explicitly selected output directories. Preparation
and evaluation need NumPy only; detection imports the installed native pyxccd.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import importlib.metadata
import itertools
import json
from pathlib import Path
import re
import shutil
import sys

import numpy as np

REPO = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name("config.json")
DEFAULT_SOURCE = Path(r"C:\personal\Jason\reviewed-hls-51rtq\data\temporal_disturbance_51RTQ_v1")
DEFAULT_DATA = REPO / "datasets/my_project"
DEFAULT_SNAPSHOT = DEFAULT_DATA / "raw/hls51rtq_snapshot"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, fields):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fresh_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def ordinal(value):
    return date.fromisoformat(value).toordinal()


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_child(root, relative):
    root = Path(root).resolve()
    child = (root / relative).resolve()
    if not child.is_relative_to(root):
        raise ValueError(f"Path escapes dataset root: {relative}")
    return child


def snapshot(source, output):
    """Copy manifest-listed inputs; never edit or import source-repository code."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError("Snapshot must be outside the original dataset")
    original_manifest = read_json(source / "manifest.json")
    selected = {
        name for name in original_manifest["files"]
        if name.startswith("shards/") and name.endswith(".npz")
    }
    selected.update(["manifest.json", "config.json", "normalization.json", "README.md",
                     "quality_report.json", "pipeline_provenance.json", "samples.csv",
                     "samples_train.csv", "samples_validation.csv", "samples_test.csv", "events.csv"])
    fresh_directory(output)
    copied = {}
    for name in sorted(selected):
        src, dst = safe_child(source, name), safe_child(output, name)
        before = digest(src)
        expected = original_manifest["files"].get(name, {}).get("sha256")
        if expected and before != expected:
            raise ValueError(f"Original file differs from manifest: {name}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if digest(dst) != before or digest(src) != before:
            raise ValueError(f"Copy/source integrity failure: {name}")
        copied[name] = {"sha256": before, "bytes": dst.stat().st_size}
    report = {"status": "copied_and_verified", "created_utc": now(),
              "source_root": str(source), "file_count": len(copied),
              "total_bytes": sum(x["bytes"] for x in copied.values()), "files": copied}
    write_json(output / "snapshot_manifest.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False))


def verify_snapshot(root):
    manifest = read_json(root / "snapshot_manifest.json")
    for name, entry in manifest["files"].items():
        if digest(safe_child(root, name)) != entry["sha256"]:
            raise ValueError(f"Snapshot changed: {name}")


def target_rows(root):
    rows = read_csv(root / "samples.csv")
    targets = [r for r in rows if r["split"] in {"validation", "test"} and int(r["window_years"]) == 2]
    ids = [r["sample_id"] for r in targets]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate target sample_id")
    return targets


def merge_observations(parts):
    """Deduplicate observations without averaging inconsistent cache versions."""
    days = np.concatenate([x[0] for x in parts]).astype(np.int64)
    spectra = np.concatenate([x[1] for x in parts]).astype(np.float32)
    if spectra.shape != (len(days), 6) or not np.isfinite(spectra).all():
        raise ValueError("Expected finite T x 6 original-scale spectra")
    if (spectra <= 0).any() or (spectra >= 10000).any():
        raise ValueError("Spectra violate original clear-observation DN contract")
    order = np.argsort(days, kind="stable")
    days, spectra = days[order], spectra[order]
    repeats = np.flatnonzero(np.diff(days) == 0)
    if repeats.size and not np.allclose(spectra[repeats], spectra[repeats + 1], atol=1e-4, rtol=0):
        raise ValueError("Conflicting spectra for the same pixel/date")
    keep = np.r_[True, np.diff(days) != 0]
    return days[keep], spectra[keep]


def load_observations(root, targets, history=None):
    needed = {int(r["pixel_id"]) for r in targets}
    parts = defaultdict(list)
    paths = sorted((root / "shards").glob("*/*.npz"))
    if history:
        hroot = Path(history)
        hm = read_json(hroot / "history_manifest.json")
        for name, entry in hm["files"].items():
            p = safe_child(hroot, name)
            if digest(p) != entry["sha256"]:
                raise ValueError(f"History changed: {name}")
            paths.append(p)
    for path in paths:
        with np.load(path, allow_pickle=False) as z:
            for index, pixel in enumerate(z["pixel_ids"]):
                if int(pixel) not in needed:
                    continue
                a, b = map(int, z["offsets"][index:index + 2])
                if b > a:
                    parts[int(pixel)].append((z["days"][a:b].copy(), z["spectra"][a:b].copy()))
    return {pixel: merge_observations(p) for pixel, p in parts.items()}


def quality_issues(days, first, last, rules):
    issues = []
    for year in range(first, last + 1):
        start, end = date(year, 1, 1).toordinal(), date(year, 12, 31).toordinal()
        d = days[(days >= start) & (days <= end)]
        reasons = []
        if len(d) < rules["min_dates_per_year"]:
            reasons.append("too_few_observations")
        if len({(date.fromordinal(int(x)).month - 1) // 3 for x in d}) < rules["min_quarters_per_year"]:
            reasons.append("insufficient_quarters")
        if not len(d) or d[0] - start > rules["max_edge_gap_days"] or end - d[-1] > rules["max_edge_gap_days"]:
            reasons.append("edge_gap")
        if len(d) > 1 and np.diff(d).max() > rules["max_gap_days"]:
            reasons.append("internal_gap")
        if reasons:
            issues.append((year, ";".join(reasons), len(d)))
    if len(days) > 1 and np.diff(days).max() > rules["max_gap_days"]:
        issues.append((0, "cross_year_or_internal_gap", len(days)))
    return issues


def inspect_coverage(root, config, history=None):
    verify_snapshot(root)
    targets = target_rows(root)
    observations = load_observations(root, targets, history)
    eligible, missing, coverage = [], [], []
    for row in targets:
        split, pixel = row["split"], int(row["pixel_id"])
        start, end = map(ordinal, config["periods"][split])
        if (int(row["window_start"]), int(row["window_end"])) != (start, end):
            raise ValueError("Target window differs from protocol: " + row["sample_id"])
        if pixel != int(row["row"]) * config["tile_width"] + int(row["col"]):
            raise ValueError("Pixel/row/column mapping mismatch")
        days, _ = observations.get(pixel, (np.array([], dtype=np.int64), None))
        d = days[(days >= ordinal(config["history_start"])) & (days <= end)]
        issues = quality_issues(d, date.fromordinal(ordinal(config["history_start"])).year,
                                date.fromordinal(end).year, config["quality"])
        coverage.append({"sample_id": row["sample_id"], "split": split, "pixel_id": pixel,
                         "eligible": int(not issues), "n_observations": len(d),
                         "first_day": int(d[0]) if len(d) else "", "last_day": int(d[-1]) if len(d) else ""})
        if not issues:
            eligible.append(row)
        for year, reason, count in issues:
            missing.append({"sample_id": row["sample_id"], "split": split, "pixel_id": pixel,
                            "row": row["row"], "col": row["col"], "block_x": row["block_x"],
                            "block_y": row["block_y"], "year": year, "observations": count, "reason": reason})
    summary = {"created_utc": now(), "protocol": config["protocol"],
               "status": "complete" if not missing else ("history_quality_flags" if history else "history_missing"),
               "source_manifest_sha256": digest(root / "snapshot_manifest.json"),
               "history_manifest_sha256": digest(Path(history) / "history_manifest.json") if history else None,
               "splits": {s: {"targets": sum(r["split"] == s for r in targets),
                              "eligible": sum(r["split"] == s for r in eligible),
                              "target_labels": dict(Counter(r["label"] for r in targets if r["split"] == s)),
                              "eligible_labels": dict(Counter(r["label"] for r in eligible if r["split"] == s))}
                          for s in config["periods"]},
               "note": "Annual QC is diagnostic, not proof of COLD initialization. Distinguish unavailable cache history from intrinsically sparse observations after raw-source extraction. Pilot selection is not a final benchmark."}
    return targets, eligible, missing, coverage, observations, summary


def audit(root, output, config, history=None):
    _, _, missing, coverage, _, summary = inspect_coverage(root, config, history)
    output = fresh_directory(output)
    write_json(output / "summary.json", summary)
    write_csv(output / "coverage.csv", coverage, ["sample_id", "split", "pixel_id", "eligible", "n_observations", "first_day", "last_day"])
    write_csv(output / "history_requests.csv", missing, ["sample_id", "split", "pixel_id", "row", "col", "block_x", "block_y", "year", "observations", "reason"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def verify_raw_source_coverage(history, missing, config):
    """Require every flagged pixel's block/year to have been scanned at source.

This permits retaining sparse real observations, never silently replacing absent
history with a shorter cached window. File integrity was checked by loading.
"""
    if not history:
        raise ValueError("QC flags require an audited raw-source history package")
    history = Path(history)
    manifest = read_json(history / "history_manifest.json")
    scene_years = defaultdict(set)
    pattern = re.compile(r"^(L30|S30)T" + re.escape(config["tile"]) + r"(\d{4})(\d{3})HLS.*\.npy$")
    for source in manifest["sources"]:
        path = Path(source["path"].replace("\\", "/"))
        match = pattern.match(path.name)
        if match:
            scene_years[path.parent.name].add(int(match[2]))
    pixel_blocks = {}
    for name in manifest["files"]:
        with np.load(safe_child(history, name), allow_pickle=False) as z:
            for pixel in z["pixel_ids"]:
                pixel_blocks[int(pixel)] = Path(name).stem
    for row in missing:
        block = f"block_x{row['block_x']}_y{row['block_y']}"
        if pixel_blocks.get(int(row["pixel_id"])) != block:
            raise ValueError("Flagged pixel has no verified raw-source history: " + row["sample_id"])
        first = date.fromisoformat(config["history_start"]).year
        last = date.fromisoformat(config["periods"][row["split"]][1]).year
        if not set(range(first, last + 1)) <= scene_years[block]:
            raise ValueError("Raw source years are not fully represented for " + row["sample_id"])
    return {"flagged_samples": len({r["sample_id"] for r in missing}),
            "verified_source_scene_count": len(manifest["sources"]),
            "policy": "Preserve all targets after raw-source scan; no interpolation or sample deletion"}


def prepare(root, output, config, pilot=False, history=None, accept_source_qc=False):
    targets, eligible, missing, coverage, observations, summary = inspect_coverage(root, config, history)
    source_qc = None
    if pilot and accept_source_qc:
        raise ValueError("Pilot subset and full-source QC policy are mutually exclusive")
    if missing and not pilot and accept_source_qc:
        source_qc = verify_raw_source_coverage(history, missing, config)
    if missing and not pilot and not source_qc:
        raise ValueError("Full history is incomplete. Run audit and extract-history; use --pilot only for a clearly identified subset.")
    selected = eligible if pilot else targets
    if not selected:
        raise ValueError("No samples meet history requirements")
    output = fresh_directory(output)
    all_days, all_spectra, index, labels = [], [], [], []
    offset = 0
    for row in selected:
        start, end = map(ordinal, config["periods"][row["split"]])
        d, x = observations[int(row["pixel_id"])]
        mask = (d >= ordinal(config["history_start"])) & (d <= end)
        d, x = d[mask], x[mask]
        index.append({"sample_id": row["sample_id"], "split": row["split"], "pixel_id": row["pixel_id"],
                      "pos": int(row["pixel_id"]) + 1, "row": row["row"], "col": row["col"],
                      "start_index": offset, "stop_index": offset + len(d), "n_observations": len(d),
                      "history_start": ordinal(config["history_start"]), "evaluation_start": start, "evaluation_end": end})
        labels.append({"sample_id": row["sample_id"], "label": row["label"]})
        all_days.append(d)
        all_spectra.append(x)
        offset += len(d)
    np.savez_compressed(output / "observations.npz", days=np.concatenate(all_days),
                        spectra=np.concatenate(all_spectra), qas=np.zeros(offset, dtype=np.uint8))
    write_csv(output / "inputs.csv", index, list(index[0]))
    write_csv(output / "reference.csv", labels, ["sample_id", "label"])
    write_csv(output / "coverage.csv", coverage, list(coverage[0]))
    write_csv(output / "history_requests.csv", missing, ["sample_id", "split", "pixel_id", "row", "col", "block_x", "block_y", "year", "observations", "reason"])
    write_json(output / "protocol.json", config)
    meta = {"status": "pilot_subset" if pilot else "complete", "created_utc": now(), "sample_count": len(selected),
            "splits": {s: sum(r["split"] == s for r in selected) for s in config["periods"]},
            "coverage": summary, "integer_conversion": "astype(int64): truncate fractional same-day means toward zero, matching wrapper",
            "raw_source_qc": source_qc,
            "label_policy": "reference.csv is read only by tuning/evaluation, never by the detector"}
    files = ["observations.npz", "inputs.csv", "reference.csv", "coverage.csv", "history_requests.csv", "protocol.json"]
    meta["files"] = {name: {"sha256": digest(output / name)} for name in files}
    write_json(output / "manifest.json", meta)
    print(json.dumps({k: v for k, v in meta.items() if k not in {"files", "coverage"}}, ensure_ascii=False, indent=2))


def extract_history(requests, stack_root, output, config):
    """Run where the original HLS stack is accessible; copy only pixel series."""
    request_rows = read_csv(requests)
    if not request_rows:
        raise ValueError("History request list is empty")
    blocks = defaultdict(dict)
    for r in request_rows:
        blocks[(int(r["block_x"]), int(r["block_y"]))][int(r["pixel_id"])] = r
    output = fresh_directory(output)
    file_entries, sources = {}, []
    first = ordinal(config["history_start"])
    last = max(ordinal(p[1]) for p in config["periods"].values())
    pattern = re.compile(r"^(L30|S30)T" + re.escape(config["tile"]) + r"(\d{4})(\d{3})HLS.*\.npy$")
    for (bx, by), pixels in sorted(blocks.items()):
        block = Path(stack_root) / f"block_x{bx}_y{by}"
        if not block.is_dir():
            raise ValueError(f"Missing source block: {block}")
        ids = sorted(pixels)
        rr = np.array([int(pixels[p]["row"]) % config["block_size"] for p in ids])
        cc = np.array([int(pixels[p]["col"]) % config["block_size"] for p in ids])
        if any(int(pixels[p]["row"]) // config["block_size"] + 1 != by or
               int(pixels[p]["col"]) // config["block_size"] + 1 != bx for p in ids):
            raise ValueError("Block/coordinate mapping mismatch")
        gathered = defaultdict(list)
        for path in sorted(block.glob("*.npy")):
            match = pattern.match(path.name)
            if not match:
                continue
            year, doy = int(match[2]), int(match[3])
            day = date(year, 1, 1).toordinal() + doy - 1
            if date.fromordinal(day).year != year or not first <= day <= last:
                continue
            before = digest(path)
            z = np.load(path, mmap_mode="r", allow_pickle=False)
            if z.shape != (config["block_size"], config["block_size"], 8) or z.dtype != np.int16:
                raise ValueError(f"Unexpected HLS source layout: {path}")
            values = np.array(z[rr, cc])
            del z
            if not set(np.unique(values[:, 7]).tolist()) <= {0, 1, 2, 3, 4, 255} or np.any(values[:, 6] != 0):
                raise ValueError(f"Unexpected QA or thermal encoding: {path}")
            keep = (values[:, 7] == 0) & (values[:, :6] > 0).all(axis=1) & (values[:, :6] < 10000).all(axis=1)
            for i in np.flatnonzero(keep):
                gathered[ids[i]].append((day, values[i, :6].copy()))
            if digest(path) != before:
                raise ValueError(f"Original changed while reading: {path}")
            sources.append({"path": str(path), "sha256": before})
        days_list, spectra_list, offsets = [], [], [0]
        for pixel in ids:
            obs = gathered[pixel]
            d = np.array([r[0] for r in obs], dtype=np.int64)
            x = np.array([r[1] for r in obs], dtype=np.float64).reshape(-1, 6)
            unique, inv, counts = np.unique(d, return_inverse=True, return_counts=True)
            means = np.zeros((len(unique), 6), dtype=np.float64)
            np.add.at(means, inv, x)
            means /= counts[:, None]
            days_list.append(unique)
            spectra_list.append(means.astype(np.float32))
            offsets.append(offsets[-1] + len(unique))
        name = f"block_x{bx}_y{by}.npz"
        np.savez_compressed(output / name, pixel_ids=np.array(ids), offsets=np.array(offsets),
                            days=np.concatenate(days_list), spectra=np.concatenate(spectra_list))
        file_entries[name] = {"sha256": digest(output / name)}
        print(f"history: {len(file_entries)}/{len(blocks)} blocks, {len(sources)} source scenes checked", flush=True)
    write_json(output / "history_manifest.json", {"created_utc": now(), "request_sha256": digest(requests),
               "protocol_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
               "files": file_entries, "sources": sources, "note": "Re-run audit; extraction alone does not guarantee complete coverage."})
    print(f"Extracted {len(file_entries)} blocks; re-run audit with --history {output}")


def metrics(labels, predictions):
    labels, predictions = np.asarray(labels), np.asarray(predictions)
    if labels.shape != predictions.shape or not np.isin(labels, [0, 1]).all() or not np.isin(predictions, [0, 1]).all():
        raise ValueError("Metrics require aligned binary arrays")
    tp = int(((labels == 1) & (predictions == 1)).sum())
    fn = int(((labels == 1) & (predictions == 0)).sum())
    fp = int(((labels == 0) & (predictions == 1)).sum())
    tn = int(((labels == 0) & (predictions == 0)).sum())
    ratio = lambda a, b: a / b if b else None
    return {"n": len(labels), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn), "omission_rate": ratio(fn, tp + fn),
            "commission_rate": ratio(fp, tp + fp), "false_positive_rate": ratio(fp, fp + tn)}


def records_to_prediction(records, start, end, threshold):
    dates = records["t_break"]
    inside = (dates >= start) & (dates <= end)
    confirmed = inside & (records["change_prob"] == 100)
    mag = records["magnitude"]
    # Exactly the land-disturbance complement in utils.getcategory_cold.
    disturbance = ~((mag[:, 3] > -threshold) & (mag[:, 2] < threshold) & (mag[:, 4] < threshold))
    chosen = dates[confirmed & disturbance]
    pending = inside & (records["change_prob"] > 0) & (records["change_prob"] < 100)
    return {"prediction": int(len(chosen) > 0), "confirmed_breaks": int(confirmed.sum()),
            "disturbance_breaks": len(chosen), "pending_breaks": int(pending.sum()),
            "disturbance_dates": ";".join(date.fromordinal(int(d)).isoformat() for d in chosen),
            "has_fallback_segment": int(np.any(np.isin(records["category"], [24, 44, 54])))}


def load_package(root):
    root = Path(root)
    meta = read_json(root / "manifest.json")
    for name, entry in meta["files"].items():
        if digest(safe_child(root, name)) != entry["sha256"]:
            raise ValueError("Prepared dataset changed: " + name)
    return meta, read_json(root / "protocol.json"), read_csv(root / "inputs.csv")


def runtime_info():
    import pyxccd
    module_root = Path(pyxccd.__file__).resolve().parent
    return {"pyxccd_version": importlib.metadata.version("pyxccd"),
            "pyxccd_module": str(Path(pyxccd.__file__).resolve()), "numpy_version": np.__version__,
            "engine_hashes": {p.name: digest(p) for p in sorted(module_root.iterdir())
                              if p.name == "ccd.py" or p.suffix in {".pyd", ".so"}},
            "python_version": sys.version, "pipeline_sha256": digest(__file__)}


def detect(root, split, params, output, limit=None):
    """This function never opens reference.csv: only load_package hashes it."""
    import pyxccd
    meta, config, rows = load_package(root)
    rows = [r for r in rows if r["split"] == split]
    if limit is not None:
        rows = [rows[i] for i in np.linspace(0, len(rows) - 1, min(limit, len(rows)), dtype=int)] if rows else []
    if not rows:
        raise ValueError("No samples for requested split")
    output = fresh_directory(output)
    (output / "records").mkdir()
    predictions = []
    with np.load(Path(root) / "observations.npz", allow_pickle=False) as z:
        packed_days, packed_spectra, packed_qas = z["days"], z["spectra"], z["qas"]
        for n, row in enumerate(rows, 1):
            result = {"sample_id": row["sample_id"], "status": "ok", "error": ""}
            a, b = int(row["start_index"]), int(row["stop_index"])
            days, spectra, qas = packed_days[a:b], packed_spectra[a:b], packed_qas[a:b]
            end = int(row["evaluation_end"])
            keep = (days >= int(row["history_start"])) & (days <= end)
            days, spectra, qas = days[keep].astype(np.int64), spectra[keep].astype(np.int64), qas[keep].astype(np.int64)
            try:
                records = pyxccd.cold_detect(days, *[np.ascontiguousarray(spectra[:, i]) for i in range(6)],
                                            np.zeros(len(days), dtype=np.int64), qas,
                                            pos=int(row["pos"]), **config["fixed"], **params)
                np.save(output / "records" / (row["sample_id"] + ".npy"), records, allow_pickle=False)
                result.update(records_to_prediction(records, int(row["evaluation_start"]), end,
                                                    config["disturbance_magnitude_threshold"]))
            except Exception as exc:
                result.update(status="error", error=f"{type(exc).__name__}: {exc}", prediction="",
                              confirmed_breaks="", disturbance_breaks="", pending_breaks="",
                              disturbance_dates="", has_fallback_segment="")
            predictions.append(result)
            if n % 100 == 0:
                print(f"{split}: {n}/{len(rows)}", flush=True)
    write_csv(output / "predictions.csv", predictions, list(predictions[0]))
    report = {"created_utc": now(), "dataset_status": meta["status"], "split": split, "params": params,
              "n": len(rows), "limited_smoke": limit is not None, "errors": sum(r["status"] != "ok" for r in predictions),
              "dataset_manifest_sha256": digest(Path(root) / "manifest.json"),
              "predictions_sha256": digest(output / "predictions.csv"), "runtime": runtime_info()}
    write_json(output / "run.json", report)
    return predictions, report


def score_predictions(root, predictions, expected_ids):
    by_id = {r["sample_id"]: r for r in predictions}
    if len(by_id) != len(predictions) or set(by_id) != set(expected_ids):
        raise ValueError("Predictions must match every target sample_id exactly once")
    if any(r.get("status", "ok") != "ok" or str(r["prediction"]) not in {"0", "1"} for r in predictions):
        raise ValueError("Unresolved run errors: no aggregate score is released")
    reference = {r["sample_id"]: int(r["label"]) for r in read_csv(Path(root) / "reference.csv")}
    ids = sorted(expected_ids)
    return metrics([reference[i] for i in ids], [int(by_id[i]["prediction"]) for i in ids])


def tune(root, output):
    meta, config, rows = load_package(root)
    output = fresh_directory(output)
    expected = [r["sample_id"] for r in rows if r["split"] == "validation"]
    trials = []
    for number, (pcg, conse) in enumerate(itertools.product(config["grid"]["p_cg"], config["grid"]["conse"])):
        params = {"p_cg": pcg, "conse": conse}
        predictions, run = detect(root, "validation", params, output / f"trial_{number:02}")
        score = score_predictions(root, predictions, expected)
        trials.append({"params": params, "metrics": score, "run": run})
        write_json(output / "trials.json", trials)
    best = max(range(len(trials)), key=lambda i: (trials[i]["metrics"]["f1"] if trials[i]["metrics"]["f1"] is not None else -1,
                                                trials[i]["metrics"]["precision"] if trials[i]["metrics"]["precision"] is not None else -1, -i))
    lock = {"created_utc": now(), "selected_on": "validation_only", "dataset_status": meta["status"],
            "dataset_manifest_sha256": digest(Path(root) / "manifest.json"), "runtime": runtime_info(),
            "params": trials[best]["params"], "validation_metrics": trials[best]["metrics"],
            "rule": "confirmed break within evaluation period plus fixed native disturbance magnitude rule"}
    write_json(output / "locked_config.json", lock)
    print(json.dumps(lock, ensure_ascii=False, indent=2))


def test_locked(root, lock_path, output):
    _, _, rows = load_package(root)
    lock = read_json(lock_path)
    if lock["selected_on"] != "validation_only" or lock["dataset_manifest_sha256"] != digest(Path(root) / "manifest.json"):
        raise ValueError("Lock is not bound to this dataset/validation protocol")
    if lock["runtime"] != runtime_info():
        raise ValueError("Runtime or pipeline differs from frozen validation run")
    predictions, report = detect(root, "test", lock["params"], output)
    expected = [r["sample_id"] for r in rows if r["split"] == "test"]
    score = score_predictions(root, predictions, expected)
    write_json(Path(output) / "metrics.json", {"dataset_status": report["dataset_status"], "metrics": score,
                                               "locked_config_sha256": digest(lock_path)})
    print(json.dumps(score, ensure_ascii=False, indent=2))


def compare(root, cold_predictions, deep_predictions, output):
    meta, _, rows = load_package(root)
    expected = [r["sample_id"] for r in rows if r["split"] == "test"]
    cold, deep = read_csv(cold_predictions), read_csv(deep_predictions)
    cold_score = score_predictions(root, cold, expected)
    deep_score = score_predictions(root, deep, expected)
    output = fresh_directory(output)
    by_c = {r["sample_id"]: r for r in cold}
    by_d = {r["sample_id"]: r for r in deep}
    truth = {r["sample_id"]: r["label"] for r in read_csv(Path(root) / "reference.csv")}
    joined = [{"sample_id": sid, "label": truth[sid], "cold_prediction": by_c[sid]["prediction"],
               "deep_prediction": by_d[sid]["prediction"]} for sid in expected]
    write_csv(output / "paired_predictions.csv", joined, list(joined[0]))
    write_json(output / "comparison.json", {"dataset_status": meta["status"], "cold": cold_score, "deep_learning": deep_score,
               "note": "Verify the deep-learning checkpoint, input-history allowance and validation-selected threshold separately.",
               "cold_predictions_sha256": digest(cold_predictions), "deep_predictions_sha256": digest(deep_predictions)})
    print(json.dumps({"cold": cold_score, "deep_learning": deep_score}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot")
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    for name in ("audit", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
        p.add_argument("--history", type=Path)
        p.add_argument("--config", type=Path, default=CONFIG)
        p.add_argument("--output", type=Path, required=True)
        if name == "prepare":
            p.add_argument("--pilot", action="store_true")
            p.add_argument("--accept-source-qc", action="store_true", help="Preserve flagged targets only after verifying exhaustive raw-source history")
    p = sub.add_parser("extract-history")
    p.add_argument("--requests", type=Path, required=True)
    p.add_argument("--stack-root", type=Path, required=True, help="e.g. /gpfs/home/stack/51RTQ_stack")
    p.add_argument("--config", type=Path, default=CONFIG)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("smoke")
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--split", choices=["validation", "test"], default="validation")
    p.add_argument("--limit", type=int, default=8)
    for name in ("tune", "test"):
        p = sub.add_parser(name)
        p.add_argument("--dataset", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
        if name == "test":
            p.add_argument("--lock", type=Path, required=True)
    p = sub.add_parser("compare")
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--cold", type=Path, required=True)
    p.add_argument("--deep", type=Path, required=True, help="CSV with sample_id,prediction; threshold fixed on validation")
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        snapshot(args.source, args.output)
    elif args.command in {"audit", "prepare"}:
        config = read_json(args.config)
        if args.command == "audit":
            audit(args.snapshot, args.output, config, args.history)
        else:
            prepare(args.snapshot, args.output, config, args.pilot, args.history, args.accept_source_qc)
    elif args.command == "extract-history":
        extract_history(args.requests, args.stack_root, args.output, read_json(args.config))
    elif args.command == "smoke":
        if args.limit <= 0:
            raise ValueError("Smoke limit must be positive")
        _, report = detect(args.dataset, args.split, {"p_cg": .99, "conse": 6}, args.output, args.limit)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["errors"]:
            raise RuntimeError("Smoke run contains errors; inspect predictions.csv")
    elif args.command == "tune":
        tune(args.dataset, args.output)
    elif args.command == "test":
        test_locked(args.dataset, args.lock, args.output)
    elif args.command == "compare":
        compare(args.dataset, args.cold, args.deep, args.output)


if __name__ == "__main__":
    main()
