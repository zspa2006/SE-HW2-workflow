"""交互式演示入口；完整参数用法仍可通过 python -m docflow 使用。"""

from pathlib import Path

from docflow.backend import ChatBackend
from docflow.cli import console
from docflow.engine import Workflow
from docflow.errors import WorkflowError
from docflow.report import write_report

PROJECT = Path(__file__).resolve().parent


def main() -> int:
    workflow = None
    try:
        print("DocFlow 代码文档工作流")
        print("1. 运行示例\n2. 演示审校返修\n3. 演示暂停恢复\n4. 分析自己的 Python 文件\n0. 退出")
        choice = input("请选择 [默认 1]：").strip() or "1"
        if choice == "0":
            return 0
        if choice not in {"1", "2", "3", "4"}:
            raise WorkflowError("请输入 0 至 4 之间的选项。")
        root = PROJECT / "examples/shop"
        files = ["pricing.py", "orders.py"]
        mode, backend = "offline", None
        if choice == "4":
            root_text = input("输入项目根目录：").strip().strip('"')
            if not root_text:
                raise WorkflowError("项目根目录不能为空。")
            root = Path(root_text).resolve()
            files = []
            print("依次输入根目录内的 .py 文件相对路径，输入空行结束。")
            while True:
                name = input(f"文件 {len(files) + 1}：").strip().strip('"')
                if not name:
                    break
                files.append(name)
                if len(files) == 30:
                    break
            selection = input("模式：1 离线 / 2 模型 [默认 1]：").strip() or "1"
            if selection not in {"1", "2"}:
                raise WorkflowError("模式请输入 1 或 2。")
            if selection == "2":
                mode, backend = "llm", ChatBackend.from_env()
        workflow = Workflow.create(root, files, PROJECT / "runs", mode=mode, backend=backend,
                                   demo_revision=choice == "2", observer=console)
        print(f"运行目录：{workflow.directory}")
        state = workflow.execute(stop_after="analyze" if choice == "3" else None)
        if choice == "3":
            write_report(workflow.directory, state)
            print(f"暂停状态：{state['status']}，下一步：{state['current']}，已完成：{state['steps']}。")
            input("按回车继续，也可以关闭程序后使用 resume 恢复：")
            workflow = Workflow.resume(workflow.directory, observer=console)
            state = workflow.execute()
        print(f"结果：{state['status']}，完成 {state['steps']} 个节点，返修 {state['revision']} 次。")
        return 0
    except (WorkflowError, OSError, EOFError) as exc:
        print(f"运行未完成：{exc}")
        return 1
    except KeyboardInterrupt:
        print("已停止。已保存的运行可以使用 python -m docflow resume 恢复。")
        return 130
    finally:
        if workflow:
            try:
                report = write_report(workflow.directory, workflow.state)
                print(f"执行报告：{report}")
            except OSError as exc:
                print(f"报告无法保存：{exc}")


if __name__ == "__main__":
    raise SystemExit(main())
