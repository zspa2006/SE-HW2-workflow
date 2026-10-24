"""Read-only tools. No imports or execution of the analyzed project.

The path boundary, UTF-8 and size checks adapt Homework 1's read_file tool.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from .errors import WorkflowError

MAX_FILE_BYTES = 32_000
MAX_FILES = 30
MAX_TOTAL_BYTES = 160_000


def read_file(root: Path, name: str) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("文件路径不能为空。")
    try:
        root = root.resolve(strict=True)
        path = (root / name).resolve(strict=True)
        relative = path.relative_to(root)
        if not path.is_file():
            raise WorkflowError(f"不是文件：{name}")
        # Read a bounded amount even if a file grows after stat().
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise WorkflowError(f"文件超过 {MAX_FILE_BYTES} 字节：{name}")
        source = raw.decode("utf-8-sig")
        if "\x00" in source:
            raise WorkflowError(f"拒绝二进制文件：{name}")
    except (OSError, ValueError, UnicodeError) as exc:
        raise WorkflowError(f"无法读取目录内的 UTF-8 文件：{name}") from exc
    return {"path": relative.as_posix(), "source": source, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "lines": len(source.splitlines())}


def snapshot(root: Path, names: list[str]) -> list[dict]:
    if not 1 <= len(names) <= MAX_FILES:
        raise WorkflowError(f"每次必须选择 1 至 {MAX_FILES} 个 Python 文件。")
    files = [read_file(root, name) for name in names]
    if any(Path(item["path"]).suffix.lower() != ".py" for item in files):
        raise WorkflowError("本 MVP 仅支持 Python .py 文件。")
    if len({item["path"] for item in files}) != len(files):
        raise WorkflowError("输入文件重复（含不同路径指向同一文件的情况）。")
    if sum(item["bytes"] for item in files) > MAX_TOTAL_BYTES:
        raise WorkflowError(f"输入总大小超过 {MAX_TOTAL_BYTES} 字节。")
    return [{key: item[key] for key in ("path", "sha256", "bytes", "lines")} for item in files]


def analyze_python(file: dict) -> dict:
    try:
        tree = ast.parse(file["source"], filename=file["path"])
    except (SyntaxError, ValueError, RecursionError) as exc:
        line = getattr(exc, "lineno", "?")
        raise WorkflowError(f"Python 语法分析失败：{file['path']}，第 {line} 行。") from exc
    symbols, imports = [], []

    def visit_body(body: list, scope: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_") and node.name != "__init__":
                    continue
                name = f"{scope}.{node.name}" if scope else node.name
                is_class = isinstance(node, ast.ClassDef)
                signature = (f"class {node.name}" if is_class else
                             f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}"
                             f"{node.name}({ast.unparse(node.args)})"
                             f"{(' -> ' + ast.unparse(node.returns)) if node.returns else ''}")
                exceptions = sorted({ast.unparse(n.exc.func if isinstance(n.exc, ast.Call) else n.exc)
                                     for n in ast.walk(node) if isinstance(n, ast.Raise) and n.exc})
                symbols.append({"id": f"{file['path']}::{name}", "name": name,
                                "kind": "class" if is_class else "function", "signature": signature,
                                "line": node.lineno, "end_line": node.end_lineno,
                                "docstring": ast.get_docstring(node) or "",
                                "raises": exceptions if not is_class else []})
                if is_class:
                    visit_body(node.body, name)
            # Include module/class-level definitions inside conditionals, but not local functions.
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ("body", "orelse", "finalbody"):
                    visit_body(getattr(node, field, []), scope)
                for handler in getattr(node, "handlers", []):
                    visit_body(handler.body, scope)

    visit_body(tree.body)
    identifiers = [symbol["id"] for symbol in symbols]
    if len(identifiers) != len(set(identifiers)):
        raise WorkflowError(f"存在同作用域重名的公开定义，当前 MVP 不做运行时覆盖推断：{file['path']}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append("." * node.level + (node.module or ""))
    return {key: file[key] for key in ("path", "sha256", "lines", "source")} | {
        "module_doc": ast.get_docstring(tree) or "", "imports": sorted(set(imports)),
        "symbols": symbols}
