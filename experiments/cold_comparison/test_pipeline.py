"""Protocol checks without requiring the native COLD extension."""
import contextlib
import csv
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

SPEC = importlib.util.spec_from_file_location("cold_comparison", Path(__file__).with_name("pipeline.py"))
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class ProtocolTests(unittest.TestCase):
    def test_conflicting_observations_are_rejected(self):
        x = np.full((1, 6), 500, dtype=np.float32)
        d, merged = p.merge_observations([(np.array([10]), x), (np.array([10]), x)])
        self.assertEqual(d.tolist(), [10])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            p.merge_observations([(np.array([10]), x), (np.array([10]), x + 2)])

    def test_disturbance_filter_excludes_history_pending_and_recovery(self):
        dt = np.dtype([("t_break", "i4"), ("change_prob", "i2"), ("category", "i2"), ("magnitude", "f4", (7,))])
        records = np.zeros(5, dtype=dt)
        records["t_break"] = [p.ordinal("2022-05-01"), p.ordinal("2023-05-01"), p.ordinal("2023-06-01"), p.ordinal("2024-02-01"), p.ordinal("2025-01-01")]
        records["change_prob"] = [100, 100, 50, 100, 100]
        records["magnitude"][:, 3] = -1000
        records["magnitude"][3, 3] = 500  # recovery, not disturbance
        result = p.records_to_prediction(records, p.ordinal("2023-01-01"), p.ordinal("2024-12-31"), 200)
        self.assertEqual(result["disturbance_breaks"], 1)
        self.assertEqual(result["confirmed_breaks"], 2)
        self.assertEqual(result["pending_breaks"], 1)
        self.assertEqual(result["disturbance_dates"], "2023-05-01")

    def test_error_metrics_and_zero_denominators(self):
        m = p.metrics([1, 1, 1, 0, 0], [1, 1, 0, 1, 0])
        self.assertEqual((m["tp"], m["fn"], m["fp"], m["tn"]), (2, 1, 1, 1))
        self.assertAlmostEqual(m["f1"], 2 / 3)
        self.assertAlmostEqual(m["omission_rate"], 1 / 3)
        self.assertAlmostEqual(m["commission_rate"], 1 / 3)
        self.assertAlmostEqual(m["false_positive_rate"], 1 / 2)
        self.assertIsNone(p.metrics([0], [0])["precision"])
        with self.assertRaises(ValueError):
            p.metrics([1], [2])

    def test_exact_pairing_and_failed_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            p.write_csv(Path(folder) / "reference.csv", [{"sample_id": "a", "label": 1}], ["sample_id", "label"])
            self.assertEqual(p.score_predictions(folder, [{"sample_id": "a", "prediction": "1"}], ["a"])["f1"], 1)
            for predictions in ([], [{"sample_id": "b", "prediction": "1"}],
                                [{"sample_id": "a", "prediction": "", "status": "error"}]):
                with self.assertRaises(ValueError):
                    p.score_predictions(folder, predictions, ["a"])

    def test_outputs_refuse_overwrite_and_source_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileExistsError):
                p.fresh_directory(folder)
            with self.assertRaises(ValueError):
                p.safe_child(folder, "../escape")

    def make_snapshot(self, root):
        config = p.read_json(p.CONFIG)
        (root / "shards/train").mkdir(parents=True)
        samples = []
        d = np.concatenate([np.arange(date(y, 1, 15).toordinal(), date(y, 12, 20).toordinal(), 20)
                            for y in range(2019, 2026)])
        x = np.full((len(d), 6), 500, dtype=np.float32)
        # One cache deliberately includes 2025 to exercise cutoff enforcement.
        np.savez(root / "shards/train/block.npz", pixel_ids=np.array([0]), offsets=np.array([0, len(d)]), days=d, spectra=x)
        for split in ["validation", "test"]:
            a, b = config["periods"][split]
            samples.append(dict(sample_id=split + "_a", split=split, pixel_id=0, row=0, col=0,
                                block_x=1, block_y=1, window_years=2, window_start=p.ordinal(a),
                                window_end=p.ordinal(b), label=1))
        p.write_csv(root / "samples.csv", samples, list(samples[0]))
        entries = {name: {"sha256": p.digest(root / name)} for name in ["samples.csv", "shards/train/block.npz"]}
        p.write_json(root / "snapshot_manifest.json", {"files": entries})
        return config

    def test_package_separates_labels_and_cuts_off_future(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "snapshot"
            config = self.make_snapshot(root)
            output = Path(folder) / "prepared"
            with contextlib.redirect_stdout(io.StringIO()):
                p.prepare(root, output, config)
            inputs = p.read_csv(output / "inputs.csv")
            self.assertTrue(all("label" not in r and "event_date" not in r for r in inputs))
            with np.load(output / "observations.npz") as z:
                for r in inputs:
                    d = z["days"][int(r["start_index"]):int(r["stop_index"])]
                    self.assertLessEqual(d.max(), int(r["evaluation_end"]))
                    self.assertGreaterEqual(d.min(), p.ordinal("2019-01-01"))
            self.assertEqual(p.load_package(output)[0]["status"], "complete")
            (output / "reference.csv").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                p.load_package(output)

    def test_incomplete_history_cannot_make_formal_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "snapshot"
            config = self.make_snapshot(root)
            path = root / "shards/train/block.npz"
            with np.load(path) as z:
                arrays = {k: z[k].copy() for k in z.files}
            keep = arrays["days"] >= p.ordinal("2021-01-01")
            arrays.update(days=arrays["days"][keep], spectra=arrays["spectra"][keep], offsets=np.array([0, keep.sum()]))
            np.savez(path, **arrays)
            manifest = p.read_json(root / "snapshot_manifest.json")
            manifest["files"]["shards/train/block.npz"]["sha256"] = p.digest(path)
            p.write_json(root / "snapshot_manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                p.prepare(root, Path(folder) / "formal", config)
            self.assertFalse((Path(folder) / "formal").exists())

    def test_detection_never_reads_labels_or_future_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "snapshot"
            config = self.make_snapshot(root)
            output = Path(folder) / "prepared"
            with contextlib.redirect_stdout(io.StringIO()):
                p.prepare(root, output, config)
            calls = []

            def fake_cold(days, *args, **kwargs):
                self.assertLessEqual(days.max(), p.ordinal("2022-12-31"))
                self.assertTrue(np.all(args[6] == 0))  # thermal placeholder
                self.assertTrue(np.all(args[7] == 0))  # clear QA
                self.assertTrue(kwargs["b_c2"])
                calls.append(days.copy())
                dt = np.dtype([("t_break", "i4"), ("change_prob", "i2"), ("category", "i2"), ("magnitude", "f4", (7,))])
                return np.zeros(1, dtype=dt)

            actual_reader = p.read_csv

            def guarded_reader(path):
                if Path(path).name == "reference.csv":
                    self.fail("Detector read label content")
                return actual_reader(path)

            with patch.dict("sys.modules", {"pyxccd": SimpleNamespace(cold_detect=fake_cold)}), \
                    patch.object(p, "runtime_info", return_value={"engine": "fixture"}), \
                    patch.object(p, "read_csv", side_effect=guarded_reader):
                predictions, report = p.detect(output, "validation", {"p_cg": .99, "conse": 6}, Path(folder) / "run")
            self.assertEqual(len(calls), 1)
            self.assertEqual(report["errors"], 0)
            self.assertEqual(predictions[0]["prediction"], 0)

    def test_tuning_uses_validation_and_test_rejects_changed_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "snapshot"
            config = self.make_snapshot(root)
            output = Path(folder) / "prepared"
            with contextlib.redirect_stdout(io.StringIO()):
                p.prepare(root, output, config)
            splits = []

            def fake_detect(dataset, split, params, directory):
                self.assertEqual(split, "validation")
                splits.append(split)
                # The unique best configuration is chosen without consulting test.
                value = int(params == {"p_cg": .99, "conse": 6})
                return [{"sample_id": "validation_a", "prediction": value}], {"errors": 0}

            with patch.object(p, "detect", side_effect=fake_detect), \
                    patch.object(p, "runtime_info", return_value={"engine": "original"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                p.tune(output, Path(folder) / "tuning")
            lock = Path(folder) / "tuning/locked_config.json"
            self.assertEqual(p.read_json(lock)["params"], {"p_cg": .99, "conse": 6})
            self.assertEqual(len(splits), 9)
            with patch.object(p, "runtime_info", return_value={"engine": "changed"}):
                with self.assertRaisesRegex(ValueError, "Runtime"):
                    p.test_locked(output, lock, Path(folder) / "test")

    def test_server_history_extraction_qa_and_same_day_means(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            block = root / "stack/block_x1_y1"
            block.mkdir(parents=True)
            config = p.read_json(p.CONFIG)
            config["block_size"] = 2
            paths = []
            for sensor, value, qa in [("L30", 500, 0), ("S30", 502, 0)]:
                x = np.zeros((2, 2, 8), dtype=np.int16)
                x[..., :6] = value
                x[..., 7] = qa
                path = block / (sensor + "T51RTQ2019015HLSv2.npy")
                np.save(path, x)
                paths.append(path)
            x[..., 7] = 4
            np.save(block / "L30T51RTQ2019020HLSv2.npy", x)
            requests = root / "requests.csv"
            p.write_csv(requests, [dict(pixel_id=0, row=0, col=0, block_x=1, block_y=1)], ["pixel_id", "row", "col", "block_x", "block_y"])
            hashes = [p.digest(path) for path in paths]
            with contextlib.redirect_stdout(io.StringIO()):
                p.extract_history(requests, root / "stack", root / "history", config)
            with np.load(root / "history/block_x1_y1.npz") as z:
                self.assertEqual(z["days"].tolist(), [p.ordinal("2019-01-15")])
                np.testing.assert_array_equal(z["spectra"], np.full((1, 6), 501))
            self.assertEqual(hashes, [p.digest(path) for path in paths])

    def test_sparse_source_history_keeps_all_targets_only_after_source_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "snapshot"
            config = self.make_snapshot(root)
            path = root / "shards/train/block.npz"
            with np.load(path) as z:
                arrays = {k: z[k].copy() for k in z.files}
            keep = arrays["days"] >= p.ordinal("2020-01-01")
            arrays.update(days=arrays["days"][keep], spectra=arrays["spectra"][keep], offsets=np.array([0, keep.sum()]))
            np.savez(path, **arrays)
            manifest = p.read_json(root / "snapshot_manifest.json")
            manifest["files"]["shards/train/block.npz"]["sha256"] = p.digest(path)
            p.write_json(root / "snapshot_manifest.json", manifest)
            history = Path(folder) / "history"
            history.mkdir()
            np.savez(history / "block_x1_y1.npz", pixel_ids=np.array([0]), offsets=np.array([0, 1]),
                     days=np.array([p.ordinal("2019-01-15")]), spectra=np.full((1, 6), 500, dtype=np.float32))
            hm = {"files": {"block_x1_y1.npz": {"sha256": p.digest(history / "block_x1_y1.npz")}},
                  "sources": [{"path": f"/stack/block_x1_y1/L30T51RTQ{year}015HLSv2.npy", "sha256": "fixture"}
                              for year in range(2019, 2025)]}
            p.write_json(history / "history_manifest.json", hm)
            with contextlib.redirect_stdout(io.StringIO()):
                p.prepare(root, Path(folder) / "full", config, history=history, accept_source_qc=True)
            meta, _, inputs = p.load_package(Path(folder) / "full")
            self.assertEqual(len(inputs), 2)
            self.assertEqual(meta["status"], "complete")
            self.assertEqual(meta["raw_source_qc"]["flagged_samples"], 2)
            hm["sources"] = hm["sources"][1:]  # no proof of 2019 source coverage
            p.write_json(history / "history_manifest.json", hm)
            with self.assertRaisesRegex(ValueError, "years"):
                p.prepare(root, Path(folder) / "bad", config, history=history, accept_source_qc=True)


if __name__ == "__main__":
    unittest.main()
