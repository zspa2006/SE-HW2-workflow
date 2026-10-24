"""Generate reproducible evidence for normal, revision and recovery runs."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from docflow.engine import Workflow, dump_json  # noqa: E402
from docflow.report import write_report  # noqa: E402


def publish_evidence(workflow: Workflow, name: str):
    write_report(workflow.directory, workflow.state)
    target = PROJECT / "docs" / "evidence" / name
    target.mkdir(parents=True, exist_ok=True)
    # A previous generation may contain more steps; remove only known generated files.
    for path in target.glob("step-*.json"):
        path.unlink()
    for source in workflow.directory.iterdir():
        if source.is_file() and source.suffix in {".json", ".jsonl", ".md", ".html"}:
            shutil.copy2(source, target / source.name)
    return {"scenario": name, "run_id": workflow.state["run_id"], "status": workflow.state["status"],
            "steps": workflow.state["steps"], "revisions": workflow.state["revision"],
            "report": f"{name}/report.html"}


def main():
    records = []
    for name, revision in (("normal", False), ("revision", True)):
        workflow = Workflow.create(PROJECT / "examples" / "shop", ["pricing.py", "orders.py"],
                                   PROJECT / "runs", demo_revision=revision)
        workflow.execute()
        assert workflow.state["steps"] == (5 if revision else 3)
        assert workflow.state["revision"] == int(revision)
        records.append(publish_evidence(workflow, name))
    workflow = Workflow.create(PROJECT / "examples" / "shop", ["pricing.py", "orders.py"], PROJECT / "runs")
    workflow.execute(stop_after="analyze")
    paused = {"status": workflow.state["status"], "current": workflow.state["current"],
              "steps": workflow.state["steps"]}
    workflow = Workflow.resume(workflow.directory)
    workflow.execute()
    assert workflow.state["steps"] == 3
    record = publish_evidence(workflow, "recovery")
    record["before_resume"] = paused
    records.append(record)
    dump_json(PROJECT / "docs" / "evidence" / "summary.json", {"scenarios": records})
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
