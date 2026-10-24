"""Generate an offline, escaped HTML run report, without a web server."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .engine import atomic_text, load_json
from .errors import WorkflowError


def step_history(directory: Path, state: dict) -> str:
    """Show committed step files, including review feedback from earlier drafts."""
    escape = lambda value: html.escape(str(value), quote=True)
    parts = []
    for number, message in enumerate(state["messages"], 1):
        title = f"第 {number} 步：{message['sender']} → {message['receiver']}"
        content = f"<p>{escape(message['summary'])}</p>"
        try:
            record = load_json(directory / f"step-{number:03d}.json")
            data = record["data"]
            if not isinstance(data, dict):
                raise ValueError
            if record.get("artifact") == "review":
                verdict = "通过" if data.get("passed") else "不通过"
                content += f"<p>本轮审校：{verdict}</p>"
                content += "<ul>" + "".join(f"<li>{escape(issue)}</li>" for issue in data.get("issues", [])) + "</ul>"
            elif record.get("artifact") == "draft":
                content += f"<p>文档版本：第 {escape(data.get('revision', 0) + 1)} 版</p>"
            serialized = json.dumps(data, ensure_ascii=False, indent=2)
            content += f"<pre>{escape(serialized)}</pre>"
        except (WorkflowError, KeyError, TypeError, ValueError):
            content += '<p class="muted">这一步的结果文件缺失或无法读取，可根据消息和日志继续核对。</p>'
        parts.append(f"<details class=\"step\"><summary>{escape(title)}"
                     f"（{escape(message['outcome'])}）</summary>{content}</details>")
    return "".join(parts) or "<p>尚无完成的节点。</p>"


def write_report(directory: Path, state: dict) -> Path:
    escape = lambda value: html.escape(str(value), quote=True)
    status = state["status"]
    labels = {"succeeded": "执行成功", "failed": "执行失败", "paused": "已暂停",
              "running": "执行中", "pending": "等待执行"}
    messages = "".join(f"<tr><td>{escape(m['id'])}</td><td>{escape(m['sender'])}</td>"
                       f"<td>{escape(m['receiver'])}</td><td>{escape(m['artifact'])}</td>"
                       f"<td>{escape(m['outcome'])}</td><td>{escape(m['summary'])}</td></tr>"
                       for m in state["messages"])
    review = state["artifacts"].get("review", {})
    issues = "".join(f"<li>{escape(issue)}</li>" for issue in review.get("issues", []))
    draft = state["artifacts"].get("draft", {}).get("markdown", "尚无文档草稿。")
    events_path = directory / "events.jsonl"
    events = events_path.read_text(encoding="utf-8") if events_path.exists() else "尚无事件。"
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>DocFlow 工作流执行报告</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#182b3a;font:16px/1.7 'Microsoft YaHei',sans-serif}
main{max-width:1120px;margin:48px auto;padding:0 28px}header{border-top:6px solid #087f8c;padding:22px 0}
h1{font-size:38px;margin:0}h2{font-size:24px;margin-top:34px}p{margin:8px 0}.tag{color:#087f8c;font-weight:bold}
table{width:100%;border-collapse:collapse;background:white;font-size:14px}td,th{text-align:left;padding:12px;border-bottom:1px solid #d9e1e7}
.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:24px;border:1px solid #d9e1e7;font:14px/1.8 Consolas,'Microsoft YaHei',monospace}
.muted{color:#576b7c}.error{color:#a12736}footer{margin:36px 0;color:#576b7c}
details.step{border-bottom:1px solid #d9e1e7;padding:12px 0}summary{cursor:pointer;font-weight:bold}
@media(max-width:650px){main{margin:20px auto;padding:0 16px}h1{font-size:30px}pre{padding:16px}}
</style><main><header><p class="tag">SOFTWARE PROJECT MANAGEMENT / HOMEWORK 2</p>
<h1>DocFlow 代码文档工作流</h1><p>{flow_sequence}</p>
<p class="tag">{status}</p><p class="muted">模式 {mode} · 已提交 {steps} 个节点 · 返修 {revision} 次</p>
<p class="muted">运行编号 {run_id}</p></header>
<p class="error">{error}</p><h2>Agent 协作消息</h2><div class="scroll"><table><thead><tr>
<th>消息</th><th>发送者</th><th>接收者</th><th>产物</th><th>结果</th><th>说明</th></tr></thead><tbody>{messages}</tbody></table></div>
<h2>每一步的结果</h2><p class="muted">展开查看传给下一节点的数据。返修前后的草稿和审校意见分别保留。</p>{history}
<h2>审校结果</h2><p>{review_summary}</p><ul>{issues}</ul>
<h2>文档内容{draft_label}</h2><pre>{draft}</pre>
<details><summary>原始执行事件（JSONL）</summary><pre>{events}</pre></details>
<footer>潘彦博 · 2404080334<br>本报告在本地打开，无需联网。源码只做静态分析，未执行输入程序。</footer></main></html>"""
    replacements = {"status": escape(labels.get(status, status)), "mode": escape(state["config"]["mode"]),
                    "steps": str(state["steps"]), "revision": str(state["revision"]),
                    "run_id": escape(state["run_id"]), "error": escape(state.get("error") or ""),
                    "messages": messages, "review_summary": escape(review.get("summary", "尚未审校。")),
                    "history": step_history(directory, state),
                    "flow_sequence": escape(" → ".join(m["sender"] for m in state["messages"]) or state["flow"]["entry"]),
                    "issues": issues, "draft": escape(draft), "events": escape(events),
                    "draft_label": "" if status == "succeeded" else "（草稿，尚未验收）"}
    # Substitute only known placeholders; CSS braces and source braces remain literal.
    import re
    page = re.sub(r"\{(" + "|".join(replacements) + r")\}",
                  lambda match: replacements[match.group(1)], page)
    path = directory / "report.html"
    atomic_text(path, page)
    return path
