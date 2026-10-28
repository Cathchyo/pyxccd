"""Run preparation on the server without transferring HLS imagery locally.

Use a fresh workspace containing code/pipeline.py, code/config.json and a verified
snapshot/. Stages write completion markers and individual logs. Reinvocation
skips completed stages, but refuses to overwrite a partially produced directory.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stack-root", type=Path, default=Path("/gpfs/home/stack/51RTQ_stack"))
    parser.add_argument("--benchmark", action="store_true", help="Also tune on validation and score test after full preparation")
    parser.add_argument("--accept-source-qc", action="store_true", help="Preserve sparse targets after verified raw-source scan")
    args = parser.parse_args()
    root = args.root.resolve()
    entry = root / "code/pipeline.py"
    stages = [
        ("audit", root / "audit/summary.json", ["audit", "--snapshot", str(root / "snapshot"), "--output", str(root / "audit")]),
        ("history", root / "history/history_manifest.json", ["extract-history", "--requests", str(root / "audit/history_requests.csv"),
          "--stack-root", str(args.stack_root), "--output", str(root / "history")]),
        ("audit_after_history", root / "audit_after_history/summary.json", ["audit", "--snapshot", str(root / "snapshot"),
          "--history", str(root / "history"), "--output", str(root / "audit_after_history")]),
        ("prepare", root / "prepared/manifest.json", ["prepare", "--snapshot", str(root / "snapshot"),
          "--history", str(root / "history"), "--output", str(root / "prepared")]),
        ("smoke", root / "smoke/run.json", ["smoke", "--dataset", str(root / "prepared"),
          "--output", str(root / "smoke"), "--limit", "32"]),
    ]
    if args.accept_source_qc:
        stages[3][2].append("--accept-source-qc")
    if args.benchmark:
        stages.extend([
            ("tune", root / "tuning/locked_config.json", ["tune", "--dataset", str(root / "prepared"), "--output", str(root / "tuning")]),
            ("test", root / "test/metrics.json", ["test", "--dataset", str(root / "prepared"),
             "--lock", str(root / "tuning/locked_config.json"), "--output", str(root / "test")]),
        ])
    status = {"started_utc": datetime.now(timezone.utc).isoformat(), "status": "running", "completed": []}
    status_path = root / "execution_status.json"

    def save():
        status["updated_utc"] = datetime.now(timezone.utc).isoformat()
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        temporary.replace(status_path)

    for name, marker, command in stages:
        status["stage"] = name
        save()
        if marker.exists():
            status["completed"].append(name)
            continue
        log = root / (name + ".log")
        with log.open("a", encoding="utf-8") as stream:
            stream.write("\nCOMMAND " + json.dumps([sys.executable, str(entry)] + command) + "\n")
            stream.flush()
            result = subprocess.run([sys.executable, str(entry)] + command, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            status.update(status="failed", returncode=result.returncode, log=str(log))
            save()
            raise SystemExit(result.returncode)
        status["completed"].append(name)
    status["status"] = "complete"
    save()


if __name__ == "__main__":
    main()
