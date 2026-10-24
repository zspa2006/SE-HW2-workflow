"""Single-process workflow engine with explicit routing and atomic checkpoints."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .agents import Agent, default_registry, isolated_context
from .errors import TransientError, WorkflowError
from .tools import read_file, snapshot

DEFAULT_FLOW = {"entry": "analyze", "routes": {
    "analyze": {"next": "write"}, "write": {"next": "review"},
    "review": {"pass": "END", "revise": "write"}}}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic_text(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def dump_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 10_000_000:
            raise WorkflowError("JSON 文件超过 10 MB。")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"无法读取有效的 JSON 对象：{path.name}") from exc


def validate_flow(flow: dict, registry: dict[str, Agent]) -> None:
    routes = flow.get("routes")
    if not isinstance(routes, dict) or flow.get("entry") not in routes or not routes:
        raise WorkflowError("流程必须包含 entry 和非空 routes。")
    for name, edges in routes.items():
        if name not in registry or not isinstance(edges, dict) or not edges:
            raise WorkflowError(f"流程节点未注册或没有路由：{name}")
        if any(not isinstance(outcome, str) or not isinstance(target, str)
               or target not in {*routes, "END"} for outcome, target in edges.items()):
            raise WorkflowError(f"节点路由包含无效目标：{name}")
    reachable, pending = set(), [flow["entry"]]
    while pending:
        name = pending.pop()
        if name in reachable or name == "END":
            continue
        reachable.add(name)
        pending.extend(routes[name].values())
    if reachable != set(routes):
        raise WorkflowError("流程包含从入口无法到达的节点。")
    # Every reachable node must have some path to END, even if there are cycles.
    can_end = {"END"}
    for _ in routes:
        can_end.update(name for name, edges in routes.items() if set(edges.values()) & can_end)
    if not set(routes).issubset(can_end):
        raise WorkflowError("流程含无法到达 END 的节点。")


@contextmanager
def run_lock(directory: Path):
    lock = directory / ".run.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise WorkflowError("该运行已被锁定。确认没有运行进程后，才能删除 .run.lock 恢复。") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


class Workflow:
    def __init__(self, directory: Path, state: dict, *, backend=None, registry=None,
                 sleep=time.sleep, observer=None):
        self.directory, self.state = directory, state
        self.backend = backend
        self.registry = registry if registry is not None else default_registry()
        self.sleep, self.observer = sleep, observer
        validate_flow(state["flow"], self.registry)

    @classmethod
    def create(cls, root: Path, files: list[str], output: Path, *, mode="offline", backend=None,
               demo_revision=False, max_revisions=2, retries=2, flow=None, **kwargs):
        root = root.resolve()
        if mode not in {"offline", "llm"} or (mode == "llm") != (backend is not None):
            raise WorkflowError("运行模式与模型后端不一致。")
        if not 0 <= max_revisions <= 5 or not 0 <= retries <= 5:
            raise WorkflowError("返修次数和重试次数必须在 0 至 5 之间。")
        inputs = snapshot(root, files)
        config = {"mode": mode, "demo_revision": demo_revision, "max_revisions": max_revisions,
                  "retries": retries, "backend": backend.identity() if backend else None}
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        selected_flow = flow if flow is not None else DEFAULT_FLOW
        validate_flow(selected_flow, kwargs.get("registry") or default_registry())
        state = {"schema_version": 1, "run_id": run_id, "created_at": now(), "updated_at": now(),
                 "root": str(root), "inputs": inputs, "config": config, "flow": selected_flow,
                 "status": "pending", "current": selected_flow["entry"], "revision": 0,
                 "steps": 0, "artifacts": {}, "messages": [], "error": None}
        workflow = cls(output.resolve() / run_id, state, backend=backend, **kwargs)
        workflow.directory.mkdir(parents=True, exist_ok=False)
        workflow.save()
        return workflow

    @classmethod
    def resume(cls, directory: Path, *, root: Path | None = None, backend=None, **kwargs):
        directory = directory.resolve()
        state = load_json(directory / "state.json")
        try:
            if state["schema_version"] != 1:
                raise WorkflowError("不支持此状态文件版本。")
            if state["status"] not in {"pending", "running", "paused", "failed", "succeeded"}:
                raise WorkflowError("状态文件包含未知状态。")
            if (backend.identity() if backend else None) != state["config"]["backend"]:
                raise WorkflowError("恢复时必须保持相同模式、接口地址和模型名。密钥可更换。")
            if not isinstance(state["messages"], list) or not isinstance(state["artifacts"], dict):
                raise WorkflowError("状态文件消息或产物结构损坏。")
            if state["current"] not in {*state["flow"]["routes"], "END"}:
                raise WorkflowError("状态文件包含未知节点。")
            source_root = (root or Path(state["root"])).resolve()
            actual = snapshot(source_root, [item["path"] for item in state["inputs"]])
            if actual != state["inputs"]:
                raise WorkflowError("输入内容已改变，拒绝把新代码和旧检查点混合。请重新 run。")
            state["root"] = str(source_root)
            return cls(directory, state, backend=backend, **kwargs)
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowError("状态文件字段缺失或损坏。") from exc

    def save(self):
        self.state["updated_at"] = now()
        dump_json(self.directory / "state.json", self.state)

    def emit(self, event: str, **fields):
        record = {"time": now(), "run_id": self.state["run_id"], "event": event,
                  "node": self.state["current"], "revision": self.state["revision"], **fields}
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
        if self.observer:
            self.observer(record)

    def _verify_inputs(self):
        for item in self.state["inputs"]:
            if read_file(Path(self.state["root"]), item["path"])["sha256"] != item["sha256"]:
                raise WorkflowError("输入文件已改变，请创建新的运行。")

    def _publish(self):
        artifacts = self.state["artifacts"]
        if not artifacts.get("review", {}).get("passed"):
            raise WorkflowError("流程到达 END 但没有通过审校，拒绝发布。")
        atomic_text(self.directory / "README.generated.md", artifacts["draft"]["markdown"])
        dump_json(self.directory / "review.json", artifacts["review"])

    def execute(self, *, stop_after: str | None = None) -> dict:
        with run_lock(self.directory):
            if self.state.get("terminal_reason"):
                raise WorkflowError(self.state["terminal_reason"] + " 请创建新的运行并调整配置或输入。")
            if self.state["status"] == "succeeded":
                self._verify_inputs()
                self._publish()  # Idempotent: restore deleted output without rerunning agents.
                return self.state
            if stop_after and stop_after not in self.state["flow"]["routes"]:
                raise WorkflowError("--stop-after 必须是流程中存在的节点。")
            self.state.update(status="running", error=None)
            self.save()
            self.emit("workflow_started")
            try:
                while self.state["current"] != "END":
                    if self.state["steps"] >= 50:
                        raise WorkflowError("流程超过 50 步，已停止以避免无限循环。")
                    self._verify_inputs()
                    node = self.state["current"]
                    self.emit("node_started", detail=f"执行 {node}")
                    for attempt in range(self.state["config"]["retries"] + 1):
                        try:
                            result = self.registry[node].run(isolated_context(
                                Path(self.state["root"]), self.state, self.backend, self.emit))
                            break
                        except TransientError as exc:
                            if attempt == self.state["config"]["retries"]:
                                raise
                            delay = min(2 ** attempt, 4)
                            self.emit("node_retry", attempt=attempt + 1, delay_seconds=delay, detail=str(exc))
                            self.sleep(delay)
                    target = self.state["flow"]["routes"][node].get(result.outcome)
                    if target is None:
                        raise WorkflowError(f"节点 {node} 没有为结果 {result.outcome} 配置路由。")
                    dump_json(self.directory / f"step-{self.state['steps'] + 1:03d}.json", {
                        "node": node, "artifact": result.artifact, "outcome": result.outcome, "data": result.data})
                    self.state["artifacts"][result.artifact] = result.data
                    self.state["steps"] += 1
                    self.state["messages"].append({"id": f"msg-{self.state['steps']:03d}", "time": now(),
                        "sender": node, "receiver": target, "artifact": result.artifact,
                        "outcome": result.outcome, "revision": self.state["revision"], "summary": result.summary})
                    self.emit("node_succeeded", outcome=result.outcome, target=target, detail=result.summary)
                    if result.outcome == "revise":
                        if self.state["revision"] >= self.state["config"]["max_revisions"]:
                            self.state["current"] = target
                            self.state["terminal_reason"] = "已达到最大返修次数。"
                            raise WorkflowError("已达到最大返修次数，文档仍未通过。保留草稿与审校意见。")
                        self.state["revision"] += 1
                    self.state["current"] = target
                    self.save()  # Committed transition; completed nodes are skipped on resume.
                    if stop_after == node and target != "END":
                        self.state["status"] = "paused"
                        self.save()
                        self.emit("workflow_paused", detail=f"已保存 {node} 节点检查点。")
                        return self.state
                self._verify_inputs()
                self._publish()
                self.state["status"] = "succeeded"
                self.save()
                self.emit("workflow_succeeded", detail="最终文档与审校报告已保存。")
            except (WorkflowError, OSError) as exc:
                self.state.update(status="failed", error=str(exc))
                self.save()
                self.emit("workflow_failed", detail=str(exc))
                raise WorkflowError(str(exc)) from exc
            except KeyboardInterrupt:
                self.state.update(status="paused", error="用户中断；可从最近的完整节点恢复。")
                self.save()
                self.emit("workflow_paused", detail=self.state["error"])
                raise
            except Exception as exc:
                detail = f"节点内部异常（{type(exc).__name__}），检查该节点实现后可恢复。"
                self.state.update(status="failed", error=detail)
                self.save()
                self.emit("workflow_failed", detail=detail)
                raise WorkflowError(detail) from exc
            return self.state
