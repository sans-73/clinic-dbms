"""Database access layer. EVERY database call in the web app goes through this file.

Rules it enforces (G4/G5):
  * every connection sets @app_user_id and @app_role before anything else
  * values are always passed as parameters (never string-built SQL)
  * MySQL rule errors (SQLSTATE 45000 / errno 1644) become RuleError with the readable message
  * stored procedures are called by LOGICAL key through contract.py; parameters are matched BY NAME using
    information_schema, so argument order differences do not matter (see call()).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from . import config
from . import contract

try:
    import pymysql
    import pymysql.cursors
except ImportError:                                           # stub mode works without the driver
    pymysql = None                                            # type: ignore

log = logging.getLogger("clinic.db")


# ---------------------------------------------------------------- errors / types
class RuleError(Exception):
    """A business rule or permission refused the action. Message is safe to show to staff."""


class NotReady(RuleError):
    """The database object this screen needs does not exist (yet) or differs from the contract."""


@dataclass
class Actor:
    staff_id: int
    role: str                      # active hat, e.g. 'DOCTOR'
    name: str = ""
    display_code: str = ""
    role_id: int | None = None
    roles: list[str] = field(default_factory=list)   # every role this person may use

    def has(self, *roles: str) -> bool:
        return self.role in roles


@dataclass
class CallResult:
    sets: list[list[dict]] = field(default_factory=list)
    out: dict[str, Any] = field(default_factory=dict)
    proc: str = ""

    @property
    def rows(self) -> list[dict]:
        return self.sets[0] if self.sets else []

    @property
    def row(self) -> dict | None:
        return self.rows[0] if self.rows else None

    def first_value(self, *names):
        """Find a value by name in OUT params, then first result row."""
        for n in names:
            if n in self.out and self.out[n] is not None:
                return self.out[n]
        r = self.row or {}
        for n in names:
            for k, v in r.items():
                if k.lower().lstrip("p_") == n.lower().lstrip("p_") and v is not None:
                    return v
        return None


STUB = config.DB_MODE == "stub"


# ---------------------------------------------------------------- connection
def _connect(role: str | None):
    if STUB:
        raise NotReady("Stub mode: no database connected.")
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed. Run: pip install -r app/requirements.txt")
    user, pw = config.db_credentials(role)
    return pymysql.connect(host=config.DB_HOST, port=config.DB_PORT, user=user, password=pw,
                           database=config.DB_NAME, charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True, connect_timeout=5)


def translate(e: Exception) -> Exception:
    """pymysql error -> RuleError / NotReady with a message a receptionist can read."""
    if pymysql is None or not isinstance(e, pymysql.MySQLError):
        return e
    code = e.args[0] if e.args else 0
    msg = str(e.args[1]) if len(e.args) > 1 else str(e)
    tech = f" [{code}] {msg}" if config.DEBUG_ERRORS else ""
    if code == 1644:
        return RuleError(msg)
    if code in (1305, 1370, 1146, 1054, 1318, 1136):
        return NotReady(f"This screen needs a database object that is missing or different from what the web app "
                        f"expects (see docs/phase5/PHASE5_HANDOFF_GUIDE.md).{tech}")
    if code == 1062:
        return RuleError("This already exists (or the form was submitted twice).")
    if code in (1451, 1452):
        return RuleError("This record is linked to other records, so the action is not allowed." + tech)
    if code == 3819:
        return RuleError("One of the values is not allowed by a database rule." + tech)
    if code in (1048, 1364):
        return RuleError("A required value is missing." + tech)
    if code in (1142, 1143, 1044, 1045, 1370):
        return RuleError("Your role is not allowed to do this." + tech)
    if code in (1205, 1213):
        return RuleError("The system was busy with another request. Please try again.")
    if code in (2003, 2006, 2013):
        return RuleError("Cannot reach the database. Is Docker/MySQL running?" + tech)
    return RuleError("Database error." + tech)


class Session:
    """One MySQL connection with the actor's session variables set."""

    def __init__(self, actor: Actor | None, autocommit: bool = True):
        self.actor = actor
        self.conn = _connect(actor.role if actor else None)
        try:
            with self.conn.cursor() as c:
                c.execute("SET time_zone = '+00:00'")
                if actor:
                    c.execute("SET @app_user_id = %s, @app_role = %s", (actor.staff_id, actor.role))
            if not autocommit:
                self.conn.autocommit(False)
        except Exception as e:
            self.close()
            raise translate(e)
        self._params_cache = _PARAM_CACHE

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et and not self.conn.get_autocommit():
            try:
                self.conn.rollback()
            except Exception:
                pass
        elif not self.conn.get_autocommit():
            self.conn.commit()
        self.close()
        return False

    # -- plain SQL
    def q(self, sql: str, params: tuple | list | dict = ()) -> list[dict]:
        try:
            with self.conn.cursor() as c:
                c.execute(sql, params)
                return list(c.fetchall() or [])
        except Exception as e:
            raise translate(e) from e

    def ex(self, sql: str, params: tuple | list | dict = ()) -> int:
        try:
            with self.conn.cursor() as c:
                c.execute(sql, params)
                return c.lastrowid or 0
        except Exception as e:
            raise translate(e) from e

    # -- routine metadata
    def routine_exists(self, name: str) -> bool:
        r = self.q("SELECT 1 AS x FROM information_schema.routines WHERE routine_schema=%s AND routine_name=%s "
                   "AND routine_type='PROCEDURE'", (config.DB_NAME, name))
        return bool(r)

    def routine_params(self, name: str) -> list[dict]:
        if name in _PARAM_CACHE:
            return _PARAM_CACHE[name]
        rows = self.q("SELECT parameter_name AS name, parameter_mode AS mode, data_type AS dtype "
                      "FROM information_schema.parameters WHERE specific_schema=%s AND specific_name=%s "
                      "AND routine_type='PROCEDURE' ORDER BY ordinal_position", (config.DB_NAME, name))
        _PARAM_CACHE[name] = rows
        return rows

    def resolve(self, key: str) -> str:
        if key in _RESOLVED:
            return _RESOLVED[key]
        spec = contract.CONTRACT.get(key)
        cands = spec["procs"] if spec else [key]
        for cand in cands:
            if self.routine_exists(cand):
                _RESOLVED[key] = cand
                return cand
        raise NotReady(f"Database procedure for '{key}' is not installed yet (looked for: {', '.join(cands)}). "
                       f"It belongs to task {spec['task'] if spec else '?'}.")

    # -- stored procedure call, parameters matched by name
    def call(self, key: str, **kw) -> CallResult:
        proc = self.resolve(key)
        params = self.routine_params(proc)
        given = {_norm(k): _clean(v) for k, v in kw.items()}
        used: set[str] = set()
        sql_args: list[str] = []
        values: list[Any] = []
        outs: list[tuple[str, str]] = []
        pre: list[tuple[str, Any]] = []
        for i, p in enumerate(params):
            nm = _norm(p["name"])
            mode = (p["mode"] or "IN").upper()
            if mode == "OUT":
                var = f"@o_{i}"
                sql_args.append(var)
                outs.append((var, nm))
                continue
            val, hit = _pick(given, nm)
            if hit:
                used.add(hit)
            if nm == "request_uuid" and val is None:
                val = str(uuid.uuid4())
            if mode == "INOUT":
                var = f"@o_{i}"
                pre.append((var, val))
                sql_args.append(var)
                outs.append((var, nm))
            else:
                sql_args.append("%s")
                values.append(val)
        unused = [k for k in given if k not in used and given[k] is not None]
        if unused:
            log.warning("call %s(%s): ignored arguments %s (not parameters of the procedure)", proc,
                        ",".join(_norm(p["name"]) for p in params), unused)
        res = CallResult(proc=proc)
        try:
            with self.conn.cursor() as c:
                for var, val in pre:
                    c.execute(f"SET {var} = %s", (val,))
                c.execute(f"CALL `{proc}`({', '.join(sql_args)})", values)
                while True:
                    if c.description:
                        res.sets.append(list(c.fetchall() or []))
                    if not c.nextset():
                        break
                if outs:
                    c.execute("SELECT " + ", ".join(f"{v} AS `{n}`" for v, n in outs))
                    res.out = dict((c.fetchone() or {}))
        except Exception as e:
            raise translate(e) from e
        return res


_PARAM_CACHE: dict[str, list[dict]] = {}
_RESOLVED: dict[str, str] = {}

# groups of names that mean the same thing; used when matching our argument names to the procedure's
_SYN = [
    {"given_name", "first_name", "first"}, {"family_name", "last_name", "surname"},
    {"date_of_birth", "dob", "birth_date"}, {"phone", "phone_number", "mobile"},
    {"doctor_staff_id", "doctor_id", "doctor"}, {"patient_id", "patient"}, {"visit_id", "visit"},
    {"request_uuid", "uuid", "client_uuid"}, {"occurred_at", "when", "at"}, {"quantity", "qty"},
    {"status", "new_status"}, {"reason", "note", "notes", "comment"}, {"amount", "value"},
    {"type", "request_type", "approval_type"}, {"approval_request_id", "approval_id"},
    {"appointment_slot_id", "slot_id"}, {"medicine_id", "medicine"}, {"stock_batch_id", "batch_id"},
    {"ref_table", "target_table"}, {"ref_id", "target_id"},
]


def _norm(name: str) -> str:
    n = name.lower()
    return n[2:] if n.startswith("p_") else n


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v if v != "" else None
    return v


def _pick(given: dict, nm: str):
    if nm in given:
        return given[nm], nm
    for grp in _SYN:
        if nm in grp:
            for alt in grp:
                if alt in given:
                    return given[alt], alt
    return None, None


# ---------------------------------------------------------------- module-level helpers (one short connection each)
def q(actor: Actor | None, sql: str, params=()) -> list[dict]:
    if STUB:
        return []
    with Session(actor) as s:
        return s.q(sql, params)


def one(actor: Actor | None, sql: str, params=()) -> dict | None:
    r = q(actor, sql, params)
    return r[0] if r else None


def scalar(actor: Actor | None, sql: str, params=(), default=None):
    r = one(actor, sql, params)
    if not r:
        return default
    v = next(iter(r.values()))
    return default if v is None else v


def ex(actor: Actor | None, sql: str, params=()) -> int:
    if STUB:
        raise NotReady("Stub mode: nothing is saved.")
    with Session(actor) as s:
        return s.ex(sql, params)


def call(actor: Actor | None, key: str, **kw) -> CallResult:
    if STUB:
        raise NotReady("Stub mode: nothing is saved (no database connected).")
    with Session(actor) as s:
        return s.call(key, **kw)


def calls(actor: Actor | None, steps: list[tuple[str, dict]]) -> list[CallResult]:
    """Several procedure calls on ONE connection (so session variables / OUT values stay together).
    Not atomic as a whole: each procedure commits itself. Use only for naturally sequential steps."""
    if STUB:
        raise NotReady("Stub mode: nothing is saved (no database connected).")
    with Session(actor) as s:
        return [s.call(k, **kw) for k, kw in steps]


_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def insert_event(actor: Actor, table: str, **cols) -> int:
    """Direct INSERT into a simple append-only event table (triggers fill recorded_by etc.).
    Used ONLY where one row needs no cross-table logic. Adds request_uuid + occurred_at when absent.
    Every table used here is listed in docs/phase5/PHASE5_HANDOFF_GUIDE.md section 'Direct writes'."""
    assert _IDENT.match(table)
    cols = {k: _clean(v) for k, v in cols.items()}
    cols.setdefault("request_uuid", str(uuid.uuid4()))
    cols.setdefault("occurred_at", None)
    if cols["occurred_at"] is None:
        from .timeutil import utcnow
        cols["occurred_at"] = utcnow()
    names = list(cols)
    for n in names:
        assert _IDENT.match(n)
    sql = f"INSERT INTO `{table}` ({', '.join(f'`{n}`' for n in names)}) VALUES ({', '.join(['%s'] * len(names))})"
    return ex(actor, sql, [cols[n] for n in names])


def insert_master(actor: Actor, table: str, **cols) -> int:
    assert _IDENT.match(table)
    cols = {k: _clean(v) for k, v in cols.items()}
    for n in cols:
        assert _IDENT.match(n)
    sql = f"INSERT INTO `{table}` ({', '.join(f'`{n}`' for n in cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    return ex(actor, sql, list(cols.values()))


def update_master(actor: Actor, table: str, pk_col: str, pk, **cols) -> None:
    """UPDATE of a MASTER row (audit trigger writes old/new). Never use on event tables."""
    for n in (table, pk_col, *cols):
        assert _IDENT.match(n)
    cols = {k: _clean(v) for k, v in cols.items()}
    sets = ", ".join(f"`{n}`=%s" for n in cols)
    ex(actor, f"UPDATE `{table}` SET {sets} WHERE `{pk_col}`=%s", [*cols.values(), pk])


def setting(actor: Actor | None, key: str, default=None):
    v = scalar(actor, "SELECT setting_value FROM clinic_settings WHERE setting_key=%s", (key,), None)
    return default if v is None else v


def setting_num(actor: Actor | None, key: str, default: float) -> float:
    try:
        return float(setting(actor, key, default))
    except (TypeError, ValueError):
        return default


def money(v) -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def system_actor(kind: str) -> Actor | None:
    """The seeded system principals SYS-SCHEDULER / SYS-ALERT / SYS-FAMILY / SYS-AUTH (G5). ASSUMPTION A-03."""
    if STUB:
        return None
    try:
        with Session(None) as s:
            r = s.q("SELECT staff_id, display_code FROM staff WHERE principal_kind='system' "
                    "AND (display_code LIKE %s OR display_code LIKE %s) ORDER BY staff_id LIMIT 1",
                    (f"%{kind}%", f"%{kind.lower()}%"))
        if r:
            return Actor(staff_id=r[0]["staff_id"], role="SYSTEM", name=r[0]["display_code"],
                         display_code=r[0]["display_code"], roles=["SYSTEM"])
    except Exception as e:
        log.warning("system actor %s not found: %s", kind, e)
    return None
