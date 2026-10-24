"""在现有流程中加入核对节点，不改动工作流引擎。"""

from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from docflow.agents import Result, default_registry
from docflow.engine import Workflow, load_json
from docflow.errors import WorkflowError
from docflow.report import write_report


class AuditAgent:
    def run(self, context):
        analysis = context.state["artifacts"].get("analysis", {})
        files = analysis.get("files", [])
        expected = {item["path"] for item in context.state["inputs"]}
        actual = {item["path"] for item in files}
        if expected != actual or len(files) != len(expected):
            raise WorkflowError("分析结果中的文件清单不完整。")
        return Result("audit", {
            "file_count": len(files),
            "interface_count": sum(len(item["symbols"]) for item in files),
            "files": sorted(actual),
        }, summary="已核对分析结果，输入文件没有遗漏。")


def main():
    registry = default_registry()
    registry["audit"] = AuditAgent()
    flow = load_json(PROJECT / "examples/extended_flow.json")
    workflow = Workflow.create(
        PROJECT / "examples/shop", ["pricing.py", "orders.py"], PROJECT / "runs",
        registry=registry, flow=flow,
    )
    state = workflow.execute()
    report = write_report(workflow.directory, state)
    print("执行顺序：" + " → ".join(message["sender"] for message in state["messages"]))
    print(f"状态：{state['status']}，已完成 {state['steps']} 个节点。")
    print(f"核对结果：{state['artifacts']['audit']}")
    print(f"报告：{report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
