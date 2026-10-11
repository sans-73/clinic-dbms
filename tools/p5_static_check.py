"""Poor man's pyflakes for the web app: compiles every .py under app/ and reports names that are used but never defined.
No third-party packages needed.   Run from the repo root:   python tools/p5_static_check.py"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILTINS = set(dir(builtins)) | {"__file__", "__name__"}


def targets(node, out: set):
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)


def scope_names(fn) -> set:
    names = set()
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = fn.args
        for x in a.posonlyargs + a.args + a.kwonlyargs:
            names.add(x.arg)
        if a.vararg: names.add(a.vararg.arg)
        if a.kwarg: names.add(a.kwarg.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store): names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn: names.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name: names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names: names.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.arg): names.add(n.arg)
        elif isinstance(n, (ast.Global, ast.Nonlocal)): names.update(n.names)
    return names


def check(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, str(path))
    defined = scope_names(tree) | BUILTINS
    problems = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined:
            problems.append(f"{path.relative_to(ROOT)}:{n.lineno}: undefined name '{n.id}'")
    return problems


def main() -> int:
    bad = []
    files = sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "tools").glob("p5_*.py"))
    for f in files:
        try:
            bad += check(f)
        except SyntaxError as e:
            bad.append(f"{f.relative_to(ROOT)}:{e.lineno}: SYNTAX ERROR {e.msg}")
    for b in bad:
        print(b)
    print(f"checked {len(files)} files, {len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
