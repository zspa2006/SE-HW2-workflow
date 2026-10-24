"""Three independent agent roles communicating through typed result envelopes."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .backend import ChatBackend
from .errors import TransientError, WorkflowError
from .tools import analyze_python, read_file

SECTIONS = ("项目概览", "文件结构", "接口说明", "使用与边界", "验证建议")


@dataclass(frozen=True)
class Result:
    artifact: str
    data: dict
    outcome: str = "next"
    summary: str = ""


@dataclass(frozen=True)
class Context:
    root: Path
    state: dict
    backend: ChatBackend | None
    emit: Callable

    def tool(self, name: str, action: Callable, **metadata):
        self.emit("tool_started", tool=name, **metadata)
        value = action()
        self.emit("tool_succeeded", tool=name, **metadata)
        return value


class Agent(Protocol):
    """New nodes only need this method and a registry/route entry."""

    def run(self, context: Context) -> Result: ...


def require_text(value, label: str, limit: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise TransientError(f"模型字段 {label} 必须是非空文本，长度不超过 {limit}。")
    return value.strip()


class AnalysisAgent:
    def run(self, context: Context) -> Result:
        files = []
        for item in context.state["inputs"]:
            data = context.tool("read_file", lambda: read_file(context.root, item["path"]), path=item["path"])
            if data["sha256"] != item["sha256"]:
                raise WorkflowError("输入文件在运行中发生变化，请创建新的运行。")
            files.append(context.tool("ast.parse", lambda: analyze_python(data), path=item["path"]))
        analysis = {"files": files, "mode": context.state["config"]["mode"]}
        if context.backend:
            response = context.tool("llm.analyze", lambda: context.backend.complete("代码分析 Agent", {
                "task": "根据静态证据解释整体作用。返回 {summary: 非空中文字符串}，不推测缺失文件。",
                "evidence": analysis}))
            analysis["summary"] = require_text(response.get("summary"), "summary")
        else:
            count = sum(len(item["symbols"]) for item in files)
            analysis["summary"] = f"静态分析了 {len(files)} 个 Python 文件，提取 {count} 个公开定义（含类与方法）。"
        return Result("analysis", analysis, summary="代码结构和源码证据已发送给文档 Agent。")


def interface_entry(file: dict, symbol: dict) -> dict:
    return {"id": symbol["id"], "text": symbol["docstring"] or
            "源码未提供 docstring。下方签名与位置来自 AST，请结合源码核实具体行为。",
            "evidence": {"path": file["path"], "line": symbol["line"], "end_line": symbol["end_line"]}}


def render_document(analysis: dict, draft: dict) -> str:
    lines = ["# 代码说明文档", "", f"生成模式：{analysis['mode']}。基于静态分析，未执行输入代码。", ""]
    sections = draft["sections"]
    for heading in SECTIONS:
        if heading not in sections:
            continue
        lines += [f"## {heading}", "", sections[heading], ""]
        if heading == "文件结构":
            for file in analysis["files"]:
                lines += [f"- `{file['path']}`：{file['lines']} 行；SHA-256 `{file['sha256']}`。",
                          f"  模块说明：{file['module_doc'] or '未提供模块 docstring。'}",
                          f"  导入：{', '.join(file['imports']) or '无'}。", ""]
        if heading == "接口说明":
            index = {entry["id"]: entry for entry in draft["entries"]}
            for file in analysis["files"]:
                for symbol in file["symbols"]:
                    if symbol["id"] not in index:
                        continue
                    entry = index[symbol["id"]]
                    lines += [f"### {symbol['name']}", "", "```python", symbol["signature"], "```", "",
                              entry["text"], "",
                              f"证据：`{file['path']}:L{symbol['line']}-L{symbol['end_line']}`。", ""]
                    if symbol["raises"]:
                        lines += ["源码出现的 raise 表达式：" + ", ".join(f"`{r}`" for r in symbol["raises"]) + "。", ""]
    return "\n".join(lines)


class DocumentationAgent:
    def run(self, context: Context) -> Result:
        analysis = context.state["artifacts"].get("analysis")
        if not analysis:
            raise WorkflowError("文档 Agent 缺少 analysis 消息。")
        entries = [interface_entry(f, s) for f in analysis["files"] for s in f["symbols"]]
        sections = {
            "项目概览": analysis["summary"],
            "文件结构": "以下列出本次选择的文件。未选择的依赖文件不在本次分析范围内。",
            "接口说明": "公开定义及其类方法如下。下划线开头的内部定义除 __init__ 外不纳入接口清单。",
            "使用与边界": "调用参数以签名为准。以下说明来自静态证据；不保证运行时行为、依赖可用性或业务正确性。"
                          "docstring 由输入代码作者提供，应对照实现核实。",
            "验证建议": "对正常输入、边界值和异常路径分别编写测试。本文没有执行输入代码，也没有宣称测试已经通过。"}
        if context.backend:
            response = context.tool("llm.write", lambda: context.backend.complete("文档生成 Agent", {
                "task": "返回 {sections: {项目概览:文本, 文件结构:文本, 接口说明:文本, 使用与边界:文本, 验证建议:文本},"
                        "entries: [{id:证据中的精确id, text:该接口的中文说明}]}。覆盖所有接口。"
                        "保留全部章节，不编造实际运行结果。不添加未知接口。",
                "evidence": analysis, "feedback": context.state["artifacts"].get("review"),
                "previous_draft": context.state["artifacts"].get("draft")}))
            raw_sections = response.get("sections")
            raw_entries = response.get("entries")
            if not isinstance(raw_sections, dict) or not isinstance(raw_entries, list):
                raise TransientError("文档模型必须返回 sections 对象和 entries 数组。")
            # Missing coverage is deliberately left for the independent reviewer.
            sections = {h: require_text(v, h) for h, v in raw_sections.items() if h in SECTIONS}
            evidence = {e["id"]: e["evidence"] for e in entries}
            entries = []
            for entry in raw_entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    raise TransientError("文档接口条目缺少字符串 id。")
                entries.append({"id": entry["id"], "text": require_text(entry.get("text"), "text"),
                                "evidence": evidence.get(entry["id"], {})})
        # Explicit fault injection is only used to demonstrate the feedback loop.
        if context.state["config"]["demo_revision"] and context.state["revision"] == 0:
            sections.pop("验证建议", None)
            context.emit("demo_fault", detail="演示开关主动遗漏验证建议章节，等待审校发现。")
        draft = {"revision": context.state["revision"], "sections": sections, "entries": entries}
        draft["markdown"] = render_document(analysis, draft)
        return Result("draft", draft, summary=f"文档第 {draft['revision'] + 1} 版已发送审校。")


class ReviewAgent:
    def run(self, context: Context) -> Result:
        artifacts = context.state["artifacts"]
        if "analysis" not in artifacts or "draft" not in artifacts:
            raise WorkflowError("审校 Agent 缺少 analysis 或 draft 消息。")
        analysis, draft = artifacts["analysis"], artifacts["draft"]
        issues = []
        expected = {s["id"]: interface_entry(f, s)["evidence"]
                    for f in analysis["files"] for s in f["symbols"]}
        actual = [entry["id"] for entry in draft["entries"]]
        for heading in SECTIONS:
            if not draft["sections"].get(heading, "").strip():
                issues.append(f"缺少章节：{heading}")
        for identifier in sorted(set(expected) - set(actual)):
            issues.append(f"遗漏接口：{identifier}")
        for entry in draft["entries"]:
            if entry["id"] not in expected:
                issues.append(f"未知接口：{entry['id']}")
            elif entry["evidence"] != expected[entry["id"]]:
                issues.append(f"证据不匹配：{entry['id']}")
        if len(actual) != len(set(actual)):
            issues.append("接口条目重复。")
        if re.search(r"\]\((?:https?://|/|\.\./)", draft["markdown"]):
            issues.append("文档包含未验证的外部或越界链接，请改为普通文本。")
        context.emit("tool_succeeded", tool="validate_document", issues=len(issues))
        semantic = "未启用模型语义审校，仅完成确定性结构与证据检查。"
        if context.backend:
            result = context.tool("llm.review", lambda: context.backend.complete("文档审校 Agent", {
                "task": "独立比较文档与代码证据，返回 {issues: [具体问题字符串], summary:非空字符串}。"
                        "无问题返回空数组，不要将格式偏好当作错误。不要声称执行代码。",
                "evidence": analysis, "draft": draft, "structural_issues": issues}))
            if not isinstance(result.get("issues"), list) or len(result["issues"]) > 30:
                raise TransientError("审校模型 issues 必须是长度不超过 30 的数组。")
            issues.extend(require_text(issue, "issue", 1000) for issue in result["issues"])
            semantic = require_text(result.get("summary"), "summary")
        issues = list(dict.fromkeys(issues))
        return Result("review", {"passed": not issues, "issues": issues, "summary": semantic,
                                 "revision": context.state["revision"],
                                 "expected_interfaces": len(expected), "documented_interfaces": len(actual)},
                      "revise" if issues else "pass", "审校退回修改。" if issues else "审校通过。")


def default_registry() -> dict[str, Agent]:
    return {"analyze": AnalysisAgent(), "write": DocumentationAgent(), "review": ReviewAgent()}


def isolated_context(root, state, backend, emit) -> Context:
    # An agent cannot silently change the shared checkpoint; only Result is committed.
    return Context(root, copy.deepcopy(state), backend, emit)
