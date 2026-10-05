"""Check source size and Python interfaces without inspecting private environment data."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("geo_blog", "tools", "tests", "website-reference/src")


def main() -> None:
    """Fail on oversized source modules or unannotated runtime interfaces."""
    failures: list[str] = []
    sources = [ROOT / "main.py"]
    for directory in SOURCE_DIRS:
        sources.extend(
            p
            for p in (ROOT / directory).rglob("*")
            if p.suffix in {".py", ".ts", ".tsx", ".mjs", ".css"}
        )
    largest = (0, "")
    for path in sources:
        text = path.read_text()
        length = len(text.splitlines())
        largest = max(largest, (length, str(path.relative_to(ROOT))))
        if length > 800:
            failures.append(f"{path.relative_to(ROOT)}: {length} lines; split before 800")
        if path.suffix != ".py" or "tests" in path.parts:
            continue
        tree = ast.parse(text)
        if not ast.get_docstring(tree):
            failures.append(f"{path.name}: missing module documentation")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            args.extend(a for a in (node.args.vararg, node.args.kwarg) if a)
            if node.returns is None or any(
                a.annotation is None for a in args if a.arg not in {"self", "cls"}
            ):
                failures.append(f"{path.name}:{node.lineno}: incomplete function annotations")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"PASS: {len(sources)} source files; largest is {largest[1]} ({largest[0]} lines).")


if __name__ == "__main__":
    main()
