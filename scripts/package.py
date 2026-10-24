"""Create a portable submission ZIP from an explicit allowlist."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
FILES = ("README.md", "Design.md", "Desgin.md", "requirements.txt", ".gitignore", ".gitattributes", ".env.example")
DIRECTORIES = ("docflow", "tests", "examples", "docs", "scripts")
ALLOWED_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".txt", ".html", ".png", ".mp4"}
PREFIX = "2404080334潘彦博/docflow-workflow"


def main():
    selected = [PROJECT / name for name in FILES]
    for directory in DIRECTORIES:
        selected.extend(path for path in (PROJECT / directory).rglob("*")
                        if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
                        and not any(part in {"__pycache__", ".git", ".venv"} for part in path.parts)
                        and path.name not in {".env", ".run.lock"})
    if not (PROJECT / "docs/evidence/summary.json").exists():
        raise SystemExit("请先运行 python scripts/demo.py 生成演示证据。")
    bundle = PROJECT / "repository.bundle"
    if bundle.exists():
        selected.append(bundle)
    target_dir = PROJECT / "提交"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / "2404080334潘彦博.zip"
    hashes = {}
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(set(selected)):
            relative = source.relative_to(PROJECT).as_posix()
            content = source.read_bytes()
            archive.writestr(f"{PREFIX}/{relative}", content)
            hashes[relative] = hashlib.sha256(content).hexdigest()
        archive.writestr(f"{PREFIX}/MANIFEST.json", json.dumps({"files": hashes}, ensure_ascii=False, indent=2))
    if target.stat().st_size >= 200_000_000:
        raise SystemExit("提交包超出 200 MB，请检查内容。")
    with zipfile.ZipFile(target) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise SystemExit(f"压缩包校验失败：{corrupt}")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    (target_dir / "SHA256.txt").write_text(f"{checksum}  {target.name}\n", encoding="utf-8")
    print(f"提交包：{target}")
    print(f"文件：{len(hashes) + 1}；大小：{target.stat().st_size / 1024 / 1024:.2f} MiB")
    print(f"SHA-256：{checksum}")


if __name__ == "__main__":
    main()
