"""Jinja environment and filters (no FastAPI import, so templates can be smoke-tested anywhere)."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import timeutil
from .constants import ROLE_LABEL

TEMPLATE_DIR = Path(__file__).parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]),
                  trim_blocks=True, lstrip_blocks=True)


# ---------------------------------------------------------------- filters
def _money(v):
    try:
        return "₹{:,.2f}".format(Decimal(str(v or 0)))
    except Exception:
        return str(v)


def _num(v):
    if v is None:
        return ""
    try:
        d = Decimal(str(v))
        return str(d.quantize(Decimal("1"))) if d == d.to_integral() else str(d.normalize())
    except Exception:
        return str(v)


def _mask_phone(p):
    p = str(p or "")
    return (p[:2] + "x" * max(len(p) - 6, 0) + p[-4:]) if len(p) > 6 else p


def _tojson(v):
    def default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        return str(o)
    return Markup(json.dumps(v, default=default).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _age(dob):
    if not isinstance(dob, (date, datetime)):
        return ""
    t = date.today()
    d = dob.date() if isinstance(dob, datetime) else dob
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def _sparkline(values, w=120, h=28):
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return Markup("")
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pts = " ".join(f"{i * (w - 4) / (len(vals) - 1) + 2:.1f},{h - 3 - (v - lo) / span * (h - 6):.1f}"
                   for i, v in enumerate(vals))
    return Markup(f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline fill="none" '
                  f'stroke="currentColor" stroke-width="1.6" points="{pts}"/></svg>')


def _opts(rows, k, v):
    return [(r[k], r[v]) for r in (rows or [])]


env.filters.update({"opts": _opts, "dt": timeutil.fmt, "d": lambda v: timeutil.fmt(v, "%d %b %Y"), "t": lambda v: timeutil.fmt(v, "%H:%M"),
                    "money": _money, "num": _num, "mask": _mask_phone, "tojson": _tojson, "age": _age,
                    "spark": _sparkline, "rolelabel": lambda r: ROLE_LABEL.get(r, r)})
env.globals.update({"enumerate": enumerate, "len": len})


