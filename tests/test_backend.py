"""Use a local HTTP fixture, never a paid or external model service."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from docflow.backend import ChatBackend
from docflow.engine import Workflow
from docflow.errors import TransientError, WorkflowError


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, dict(self.headers), body))
                status, payload = owner.responses.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.backend = ChatBackend(endpoint=f"http://127.0.0.1:{self.server.server_port}/v1",
                                   model="fixture-model", key="test-only-secret")

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def reply(self, obj):
        self.responses.append((200, {"choices": [{"message": {"content": json.dumps(obj)}}]}))

    def test_request_contract(self):
        self.reply({"summary": "完成"})
        self.assertEqual(self.backend.complete("分析", {"task": "test"})["summary"], "完成")
        path, headers, body = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-only-secret")
        self.assertEqual(body["model"], "fixture-model")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertIn("JSON", body["messages"][0]["content"])

    def test_all_three_agents_use_backend_end_to_end(self):
        self.reply({"summary": "示例模块提供加法接口。"})
        self.reply({"sections": {h: "来自测试模型的文档内容。" for h in
                                ("项目概览", "文件结构", "接口说明", "使用与边界", "验证建议")},
                    "entries": [{"id": "add.py::add", "text": "返回 a 与 b 的和。"}]})
        self.reply({"issues": [], "summary": "根据提供的证据未发现问题。"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "add.py").write_text('def add(a, b):\n    """求和。"""\n    return a + b\n', encoding="utf-8")
            workflow = Workflow.create(root, ["add.py"], root / "runs", mode="llm", backend=self.backend)
            workflow.execute()
            self.assertEqual(workflow.state["status"], "succeeded")
            self.assertEqual(len(self.requests), 3)
            persisted = "\n".join(p.read_text(encoding="utf-8") for p in workflow.directory.iterdir())
            self.assertNotIn("test-only-secret", persisted)
            self.assertNotIn("Authorization", persisted)

    def test_retryable_http_statuses(self):
        for status in (429, 500, 503):
            self.responses.append((status, {"error": "ignored"}))
            with self.subTest(status=status), self.assertRaises(TransientError):
                self.backend.complete("分析", {})

    def test_auth_failure_not_retried_or_leaked(self):
        self.responses.append((401, {"error": "test-only-secret"}))
        with self.assertRaises(WorkflowError) as caught:
            self.backend.complete("分析", {})
        self.assertNotIsInstance(caught.exception, TransientError)
        self.assertNotIn("test-only-secret", str(caught.exception))

    def test_malformed_json_response_is_retryable(self):
        self.responses.append((200, {"choices": [{"message": {"content": "not JSON"}}]}))
        with self.assertRaises(TransientError):
            self.backend.complete("分析", {})

    def test_response_schema_missing_fields(self):
        self.reply({"summary": ""})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.py").write_text("x=1", encoding="utf-8")
            workflow = Workflow.create(root, ["a.py"], root / "runs", mode="llm", backend=self.backend, retries=0)
            with self.assertRaisesRegex(WorkflowError, "summary"):
                workflow.execute()
            self.assertEqual(workflow.state["steps"], 0)

    def test_backend_identity_change_rejected_on_resume(self):
        self.reply({"summary": "示例"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.py").write_text("x=1", encoding="utf-8")
            workflow = Workflow.create(root, ["a.py"], root / "runs", mode="llm", backend=self.backend)
            workflow.execute(stop_after="analyze")
            with self.assertRaisesRegex(WorkflowError, "相同模式"):
                Workflow.resume(workflow.directory)
            self.backend.key = "rotated-key"
            self.assertIsNotNone(Workflow.resume(workflow.directory, backend=self.backend))

    def test_url_and_credentials_validation(self):
        for endpoint in ("http://example.com/v1", "https://u:p@example.com/v1", "https://example.com?key=abc",
                         "ftp://example.com", ""):
            with self.subTest(endpoint=endpoint), self.assertRaises(WorkflowError):
                ChatBackend(endpoint=endpoint, model="x", key="x")
        with self.assertRaises(WorkflowError):
            ChatBackend(endpoint="https://example.com/v1", model="", key="")


if __name__ == "__main__":
    unittest.main()
