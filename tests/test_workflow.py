"""Behavior tests: cooperation, revision, durability, boundaries and CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from docflow.agents import DocumentationAgent, Result, ReviewAgent, default_registry, isolated_context
from docflow.engine import DEFAULT_FLOW, Workflow, load_json, run_lock, validate_flow
from docflow.errors import TransientError, WorkflowError
from docflow.report import write_report
from docflow.tools import MAX_FILE_BYTES, analyze_python, read_file, snapshot


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "项目"
        self.root.mkdir()
        self.source = self.root / "sample.py"
        self.source.write_text('"""示例模块。"""\n\ndef add(a: int, b: int = 1) -> int:\n'
                               '    """返回两个数的和。"""\n    return a + b\n', encoding="utf-8")

    def workflow(self, **options):
        return Workflow.create(self.root, ["sample.py"], self.base / "runs", sleep=lambda _: None, **options)


class WorkflowTests(Workspace):
    def test_three_agents_end_to_end(self):
        workflow = self.workflow()
        state = workflow.execute()
        self.assertEqual(state["status"], "succeeded")
        self.assertEqual([m["sender"] for m in state["messages"]], ["analyze", "write", "review"])
        self.assertEqual([m["receiver"] for m in state["messages"]], ["write", "review", "END"])
        self.assertTrue(state["artifacts"]["review"]["passed"])
        self.assertIn("sample.py:L3-L5", (workflow.directory / "README.generated.md").read_text(encoding="utf-8"))
        self.assertEqual(load_json(workflow.directory / "state.json")["current"], "END")

    def test_real_revision_feedback_and_history(self):
        workflow = self.workflow(demo_revision=True)
        state = workflow.execute()
        self.assertEqual(state["steps"], 5)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["messages"][2]["outcome"], "revise")
        self.assertEqual(state["messages"][2]["receiver"], "write")
        first_review = load_json(workflow.directory / "step-003.json")
        self.assertIn("缺少章节：验证建议", first_review["data"]["issues"])
        self.assertTrue(state["artifacts"]["review"]["passed"])
        self.assertEqual(len(list(workflow.directory.glob("step-*.json"))), 5)

    def test_pause_resume_skips_completed_analysis(self):
        workflow = self.workflow()
        workflow.execute(stop_after="analyze")
        self.assertEqual(workflow.state["status"], "paused")
        self.assertFalse((workflow.directory / "README.generated.md").exists())
        resumed = Workflow.resume(workflow.directory)
        resumed.execute()
        self.assertEqual(resumed.state["steps"], 3)
        events = [json.loads(line) for line in (workflow.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(e["event"] == "node_started" and e["node"] == "analyze" for e in events), 1)

    def test_pause_after_draft_has_no_final_output(self):
        workflow = self.workflow()
        workflow.execute(stop_after="write")
        self.assertEqual(workflow.state["current"], "review")
        self.assertFalse((workflow.directory / "README.generated.md").exists())
        self.assertIn("draft", workflow.state["artifacts"])

    def test_successful_resume_is_idempotent(self):
        workflow = self.workflow()
        workflow.execute()
        final = workflow.directory / "README.generated.md"
        final.unlink()
        Workflow.resume(workflow.directory).execute()
        self.assertTrue(final.exists())
        self.assertEqual(load_json(workflow.directory / "state.json")["steps"], 3)

    def test_changed_source_rejected_on_resume(self):
        workflow = self.workflow()
        workflow.execute(stop_after="analyze")
        self.source.write_text("x = 7\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowError, "改变"):
            Workflow.resume(workflow.directory)

    def test_portable_resume_with_new_root(self):
        workflow = self.workflow()
        workflow.execute(stop_after="analyze")
        relocated = self.base / "relocated"
        relocated.mkdir()
        (relocated / "sample.py").write_bytes(self.source.read_bytes())
        self.source.unlink()
        resumed = Workflow.resume(workflow.directory, root=relocated)
        self.assertEqual(resumed.execute()["status"], "succeeded")

    def test_source_change_during_execution_blocks_publish(self):
        registry = default_registry()
        reviewer = registry["review"]
        source = self.source

        class MutatingReviewer:
            def run(self, context):
                result = reviewer.run(context)
                source.write_text("changed = True", encoding="utf-8")
                return result

        registry["review"] = MutatingReviewer()
        workflow = self.workflow(registry=registry)
        with self.assertRaisesRegex(WorkflowError, "改变"):
            workflow.execute()
        self.assertFalse((workflow.directory / "README.generated.md").exists())
        self.assertEqual(load_json(workflow.directory / "state.json")["status"], "failed")

    def test_transient_error_retried_without_partial_messages(self):
        writer = DocumentationAgent()

        class RetryWriter:
            calls = 0

            def run(self, context):
                self.calls += 1
                if self.calls == 1:
                    raise TransientError("模拟暂时失败")
                return writer.run(context)

        registry = default_registry()
        registry["write"] = RetryWriter()
        workflow = self.workflow(registry=registry)
        workflow.execute()
        self.assertEqual(registry["write"].calls, 2)
        self.assertEqual(workflow.state["steps"], 3)
        self.assertIn('"node_retry"', (workflow.directory / "events.jsonl").read_text(encoding="utf-8"))

    def test_permanent_error_checkpoint_can_resume_after_fix(self):
        class BrokenWriter:
            calls = 0

            def run(self, context):
                self.calls += 1
                raise WorkflowError("模拟配置错误")

        registry = default_registry()
        broken = registry["write"] = BrokenWriter()
        workflow = self.workflow(registry=registry)
        with self.assertRaisesRegex(WorkflowError, "配置错误"):
            workflow.execute()
        self.assertEqual(broken.calls, 1)
        self.assertEqual(workflow.state["current"], "write")
        self.assertEqual(workflow.state["steps"], 1)
        resumed = Workflow.resume(workflow.directory)
        self.assertEqual(resumed.execute()["status"], "succeeded")

    def test_transient_retry_exhaustion(self):
        class Unavailable:
            calls = 0

            def run(self, context):
                self.calls += 1
                raise TransientError("暂时失败")

        registry = default_registry()
        registry["analyze"] = Unavailable()
        workflow = self.workflow(registry=registry, retries=1)
        with self.assertRaises(WorkflowError):
            workflow.execute()
        self.assertEqual(registry["analyze"].calls, 2)
        self.assertEqual(workflow.state["steps"], 0)

    def test_revision_exhaustion_is_terminal_across_resume(self):
        workflow = self.workflow(demo_revision=True, max_revisions=0)
        with self.assertRaisesRegex(WorkflowError, "最大返修"):
            workflow.execute()
        self.assertFalse((workflow.directory / "README.generated.md").exists())
        with self.assertRaisesRegex(WorkflowError, "最大返修"):
            Workflow.resume(workflow.directory).execute()

    def test_agent_cannot_mutate_shared_state(self):
        registry = default_registry()
        analyzer = registry["analyze"]

        class BadAgent:
            def run(self, context):
                result = analyzer.run(context)
                context.state["config"]["mode"] = "corrupted"
                return result

        registry["analyze"] = BadAgent()
        workflow = self.workflow(registry=registry)
        workflow.execute()
        self.assertEqual(workflow.state["config"]["mode"], "offline")

    def test_invalid_syntax_no_retry(self):
        self.source.write_text("def broken(:\n", encoding="utf-8")
        workflow = self.workflow()
        with self.assertRaisesRegex(WorkflowError, "语法分析失败"):
            workflow.execute()
        self.assertNotIn("node_retry", (workflow.directory / "events.jsonl").read_text(encoding="utf-8"))

    def test_lock_prevents_two_writers(self):
        workflow = self.workflow()
        with run_lock(workflow.directory):
            with self.assertRaisesRegex(WorkflowError, "锁定"):
                workflow.execute()
        self.assertFalse((workflow.directory / ".run.lock").exists())

    def test_custom_node_crash_saves_failed_checkpoint(self):
        class BrokenAgent:
            def run(self, context):
                raise RuntimeError("private diagnostic must not be persisted")

        registry = default_registry() | {"analyze": BrokenAgent()}
        workflow = self.workflow(registry=registry)
        with self.assertRaisesRegex(WorkflowError, "RuntimeError"):
            workflow.execute()
        saved = load_json(workflow.directory / "state.json")
        self.assertEqual(saved["status"], "failed")
        self.assertNotIn("private diagnostic", saved["error"])
        self.assertEqual(Workflow.resume(workflow.directory).execute()["status"], "succeeded")

    def test_runtime_step_limit_stops_cycle(self):
        flow = {"entry": "analyze", "routes": {"analyze": {"next": "analyze", "pass": "END"}}}
        workflow = self.workflow(flow=flow)
        with self.assertRaisesRegex(WorkflowError, "50 步"):
            workflow.execute()
        self.assertEqual(workflow.state["steps"], 50)
        self.assertEqual(workflow.state["status"], "failed")

    def test_corrupt_checkpoint_rejected(self):
        workflow = self.workflow()
        (workflow.directory / "state.json").write_text('{"schema_version": 1}', encoding="utf-8")
        with self.assertRaisesRegex(WorkflowError, "损坏"):
            Workflow.resume(workflow.directory)

    def test_schema_version_rejected(self):
        workflow = self.workflow()
        workflow.state["schema_version"] = 9
        workflow.save()
        with self.assertRaisesRegex(WorkflowError, "版本"):
            Workflow.resume(workflow.directory)

    def test_report_escapes_untrusted_content(self):
        self.source.write_text('"""<script>alert(1)</script> {status}"""\nx=1', encoding="utf-8")
        workflow = self.workflow()
        workflow.execute()
        page = write_report(workflow.directory, workflow.state).read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("{status}", page)  # User text is never substituted a second time.

    def test_cli_run_inspect_and_resume(self):
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        command = [sys.executable, "-m", "docflow"]
        started = subprocess.run(command + ["run", "--root", str(self.root), "--files", "sample.py",
                    "--output", str(self.base / "cli"), "--stop-after", "analyze"],
                    capture_output=True, text=True, encoding="utf-8", env=environment)
        self.assertEqual(started.returncode, 0, started.stderr)
        run = next((self.base / "cli").iterdir())
        inspected = subprocess.run(command + ["inspect", str(run)], capture_output=True, text=True,
                                   encoding="utf-8", env=environment)
        self.assertIn("status=paused", inspected.stdout)
        resumed = subprocess.run(command + ["resume", str(run)], capture_output=True, text=True,
                                 encoding="utf-8", env=environment)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(load_json(run / "state.json")["status"], "succeeded")


class ToolsTests(Workspace):
    def test_escape_absolute_and_traversal_rejected(self):
        outside = self.base / "private.py"
        outside.write_text("secret = 1", encoding="utf-8")
        for path in ("../private.py", str(outside)):
            with self.subTest(path=path), self.assertRaises(WorkflowError):
                read_file(self.root, path)

    def test_symlink_escape_rejected(self):
        outside = self.base / "private.py"
        outside.write_text("secret = 1", encoding="utf-8")
        link = self.root / "link.py"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("当前 Windows 会话没有创建符号链接的权限")
        with self.assertRaises(WorkflowError):
            read_file(self.root, "link.py")

    def test_binary_large_and_bad_encoding_rejected(self):
        for content in (b"a\x00b", b"\xff\xfe", b"x" * (MAX_FILE_BYTES + 1)):
            self.source.write_bytes(content)
            with self.subTest(content_size=len(content)), self.assertRaises(WorkflowError):
                read_file(self.root, "sample.py")

    def test_missing_directory_empty_duplicate_and_non_python(self):
        for name in ("missing.py", ".", ""):
            with self.subTest(name=name), self.assertRaises(WorkflowError):
                read_file(self.root, name)
        for names in ([], ["sample.py", "./sample.py"], ["sample.py"] * 31):
            with self.subTest(names=names), self.assertRaises(WorkflowError):
                snapshot(self.root, names)
        (self.root / "note.txt").write_text("hello", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowError, "Python"):
            snapshot(self.root, ["note.txt"])

    def test_ast_methods_async_keyword_arguments_and_no_execution(self):
        self.source.write_text('raise RuntimeError("不得执行")\nclass Store:\n'
                               '    async def fetch(self, /, key: str, *, default=None):\n'
                               '        return default\n    def _internal(self): pass\n'
                               'def outer():\n    def local(): pass\n    return local\n', encoding="utf-8")
        data = analyze_python(read_file(self.root, "sample.py"))
        self.assertEqual([s["name"] for s in data["symbols"]], ["Store", "Store.fetch", "outer"])
        self.assertIn("/, key: str, *, default=None", data["symbols"][1]["signature"])

    def test_empty_module_is_valid(self):
        self.source.write_text("", encoding="utf-8")
        workflow = self.workflow()
        self.assertEqual(workflow.execute()["artifacts"]["review"]["expected_interfaces"], 0)

    def test_input_budget_rejected(self):
        for i in range(6):
            (self.root / f"{i}.py").write_bytes(b"#" + b"x" * 29_999)
        with self.assertRaisesRegex(WorkflowError, "总大小"):
            snapshot(self.root, [f"{i}.py" for i in range(6)])

    def test_ambiguous_public_redefinitions_rejected(self):
        self.source.write_text("def same(): pass\ndef same(): pass\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkflowError, "重名"):
            analyze_python(read_file(self.root, "sample.py"))

    def test_original_source_is_available_for_model_reasoning(self):
        data = analyze_python(read_file(self.root, "sample.py"))
        self.assertIn("return a + b", data["source"])


class ReviewAndExtensionTests(Workspace):
    def test_reviewer_catches_omissions_unknown_duplicates_and_wrong_evidence(self):
        workflow = self.workflow()
        workflow.execute(stop_after="write")
        draft = workflow.state["artifacts"]["draft"]
        draft["sections"].pop("项目概览")
        entry = draft["entries"][0]
        entry["evidence"]["line"] = 999
        draft["entries"].extend([dict(entry), {"id": "invented", "text": "x", "evidence": {}}])
        result = ReviewAgent().run(isolated_context(self.root, workflow.state, None, lambda *a, **k: None))
        issues = "\n".join(result.data["issues"])
        self.assertIn("缺少章节", issues)
        self.assertIn("证据不匹配", issues)
        self.assertIn("未知接口", issues)
        self.assertIn("重复", issues)
        draft["entries"] = []
        result = ReviewAgent().run(isolated_context(self.root, workflow.state, None, lambda *a, **k: None))
        self.assertTrue(any("遗漏接口" in i for i in result.data["issues"]))

    def test_add_new_node_without_engine_changes(self):
        class AuditAgent:
            def run(self, context):
                return Result("audit", {"verified_files": len(context.state["inputs"])}, summary="扩展审计节点完成。")

        registry = default_registry() | {"audit": AuditAgent()}
        flow = {"entry": "analyze", "routes": {
            "analyze": {"next": "audit"}, "audit": {"next": "write"},
            "write": {"next": "review"}, "review": {"pass": "END", "revise": "write"}}}
        workflow = self.workflow(flow=flow, registry=registry)
        self.assertEqual(workflow.execute()["steps"], 4)
        self.assertEqual(workflow.state["artifacts"]["audit"]["verified_files"], 1)

    def test_invalid_graphs_rejected(self):
        registry = default_registry()
        invalid = [{}, {"entry": "missing", "routes": {}},
                   {"entry": "analyze", "routes": {"analyze": {"next": "missing"}}},
                   {"entry": "analyze", "routes": {"analyze": {"next": "analyze"}}},
                   {"entry": "analyze", "routes": {"analyze": {"next": "END"}, "write": {"next": "END"}}}]
        for flow in invalid:
            with self.subTest(flow=flow), self.assertRaises(WorkflowError):
                validate_flow(flow, registry)

    def test_end_without_review_does_not_publish(self):
        workflow = self.workflow(flow={"entry": "analyze", "routes": {"analyze": {"next": "END"}}})
        with self.assertRaisesRegex(WorkflowError, "没有通过审校"):
            workflow.execute()


if __name__ == "__main__":
    unittest.main()
