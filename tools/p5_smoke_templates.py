"""Renders EVERY template twice without a database or FastAPI:
  pass 1 'full'  : any undefined value behaves like a rich object (truthy, iterable, numeric) -> exercises every branch
  pass 2 'empty' : undefined values are falsy / empty                                          -> exercises empty states
Catches Jinja syntax errors, wrong macro arguments, bad filters.  Run from repo root:  python tools/p5_smoke_templates.py"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, Undefined, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import jinja_env  # noqa: E402


class Rich(Undefined):
    def __getattr__(self, n):
        return Rich() if not n.startswith("__") else super().__getattr__(n)
    __getitem__ = lambda self, k: Rich()
    __call__ = lambda self, *a, **k: Rich()
    __bool__ = lambda self: True
    __iter__ = lambda self: iter([Rich(), Rich()])
    __len__ = lambda self: 2
    __str__ = lambda self: "x"
    __float__ = lambda self: 1.0
    __int__ = lambda self: 1
    __gt__ = __ge__ = lambda self, o: True
    __lt__ = __le__ = lambda self, o: False
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = lambda self, o: Rich()
    __contains__ = lambda self, o: True


def make_env(undefined):
    e = Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")), autoescape=select_autoescape(["html"]),
                    trim_blocks=True, lstrip_blocks=True, undefined=undefined)
    e.filters.update(jinja_env.env.filters)
    e.globals.update(jinja_env.env.globals)
    return e


def main() -> int:
    user = SimpleNamespace(role="OWNER", roles=["OWNER", "DOCTOR"], name="Test User", staff_id=1)
    base = dict(user=user, menu=[("A", "/a")], flashes=[("ok", "hello"), ("err", "bad")], csrf="tok", clinic_name="Test Clinic",
                banner={"interrupts": 2, "approvals": 1}, path="/owner", stub=True, request=None, status_code=403,
                message="msg", back="/", token="abc", reports={}, vals={}, trend={},
                c={"balance": 0, "basis": None, "items": [("Step", True, "hint"), ("Other step", False, "hint")]}, cfg={"patientId": 1, "allergies": [], "existing": [], "nextIndex": 10})
    names = sorted(p.relative_to(ROOT / "app" / "templates").as_posix() for p in (ROOT / "app" / "templates").rglob("*.html")
                   if p.name not in ("base.html", "macros.html"))
    bad = 0
    for mode, und in (("full", Rich), ("empty", ChainableUndefined)):
        env = make_env(und)
        for n in names:
            try:
                out = env.get_template(n).render(**base)
                assert "</html>" in out
            except Exception as e:  # noqa: BLE001
                bad += 1
                print(f"[{mode}] {n}: {type(e).__name__}: {e}")
    print(f"rendered {len(names)} templates x 2 modes, {bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
