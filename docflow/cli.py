"""CLI entry points with stable exit codes and a concise execution trace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backend import ChatBackend
from .engine import Workflow, load_json
from .errors import WorkflowError
from .report import write_report


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="DocFlow：分析、生成、审校三个 Agent 协作生成 Python 代码文档。")
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="创建新的工作流")
    run.add_argument("--root", type=Path, default=Path("."), help="输入项目根目录")
    run.add_argument("--files", nargs="+", required=True, help="根目录内的 .py 文件，相对路径")
    run.add_argument("--output", type=Path, default=Path("runs"), help="运行产物父目录")
    run.add_argument("--mode", choices=("offline", "llm"), default="offline")
    run.add_argument("--demo-revision", action="store_true", help="演示开关：初稿主动漏一章以触发返修")
    run.add_argument("--max-revisions", type=int, default=2)
    run.add_argument("--retries", type=int, default=2, help="每个节点临时错误重试次数")
    run.add_argument("--flow", type=Path, help="自定义流程 JSON")
    run.add_argument("--stop-after", choices=("analyze", "write", "review"), help="节点提交后暂停，便于验证恢复")
    resume = commands.add_parser("resume", help="从已保存的检查点继续")
    resume.add_argument("directory", type=Path)
    resume.add_argument("--root", type=Path, help="项目搬家后指定新根目录，源码哈希必须相同")
    inspect = commands.add_parser("inspect", help="只读查看状态摘要")
    inspect.add_argument("directory", type=Path)
    return root


def console(record: dict):
    if record["event"] in {"node_started", "node_succeeded", "node_retry", "workflow_paused", "workflow_failed"}:
        print(f"[{record['event']}] {record['node']}: {record.get('detail', '')}")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    workflow = None
    try:
        if args.command == "inspect":
            state = load_json(args.directory / "state.json")
            print(f"run_id={state['run_id']} status={state['status']} current={state['current']} "
                  f"steps={state['steps']} revision={state['revision']}")
            return 0
        if args.command == "run":
            backend = ChatBackend.from_env() if args.mode == "llm" else None
            workflow = Workflow.create(args.root, args.files, args.output, mode=args.mode, backend=backend,
                demo_revision=args.demo_revision, max_revisions=args.max_revisions, retries=args.retries,
                flow=load_json(args.flow) if args.flow else None, observer=console)
        else:
            saved = load_json(args.directory / "state.json")
            backend = ChatBackend.from_env() if saved.get("config", {}).get("mode") == "llm" else None
            workflow = Workflow.resume(args.directory, root=args.root, backend=backend, observer=console)
        print(f"运行目录：{workflow.directory}")
        state = workflow.execute(stop_after=getattr(args, "stop_after", None))
        print(f"结果：{state['status']}；已提交 {state['steps']} 个节点；返修 {state['revision']} 次。")
        return 0
    except (WorkflowError, OSError, KeyError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断，可使用 resume 从检查点继续。", file=sys.stderr)
        return 130
    finally:
        if workflow is not None:
            try:
                report = write_report(workflow.directory, workflow.state)
                print(f"可视化报告：{report}")
            except OSError as exc:
                print(f"报告无法写入：{exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
