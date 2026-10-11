"""Tests the stored-procedure argument matching in app/db.py against a FAKE connection (no MySQL needed).
Run from the repo root:  python tools/p5_selftest_db.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402


class FakeCursor:
    def __init__(self, log, routines, params):
        self.log, self.routines, self.params = log, routines, params
        self.description = None
        self._rows = []
        self._sets = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, args=()):
        self.log.append((sql, list(args) if args else []))
        s = " ".join(sql.split())
        self.description, self._rows, self._sets = None, [], []
        if "FROM information_schema.routines" in s:
            self._rows = [{"x": 1}] if args[1] in self.routines else []
        elif "FROM information_schema.parameters" in s:
            self._rows = self.params.get(args[1], [])
        elif s.startswith("CALL"):
            self._sets = [[{"patient_id": 7, "display_code": "CLN1-000007"}]]
        elif s.startswith("SELECT @o_"):
            self._rows = [{"patient_id": 42, "display_code": "CLN1-000042"}]
        if self._rows:
            self.description = True

    def fetchall(self): return self._rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def nextset(self):
        if self._sets:
            self._rows = self._sets.pop(0); self.description = True
            return True
        return False


class FakeConn:
    def __init__(self, log, routines, params): self.log, self.routines, self.params = log, routines, params
    def cursor(self): return FakeCursor(self.log, self.routines, self.params)
    def close(self): pass


def mk(routines, params):
    s = db.Session.__new__(db.Session)
    s.actor, s.log = None, []
    s.conn = FakeConn(s.log, routines, params)
    return s


def P(name, mode="IN"): return {"name": name, "mode": mode, "dtype": "x"}


fails = 0
def check(label, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + label)
    fails += 0 if cond else 1


db._PARAM_CACHE.clear(); db._RESOLVED.clear()
# 1. arguments matched by NAME, different order, synonyms, p_ prefix, OUT param, auto request_uuid
s = mk({"register_patient"}, {"register_patient": [P("p_last_name"), P("p_first_name"), P("p_dob"), P("p_request_uuid"), P("p_patient_id", "OUT")]})
r = s.call("register_patient", given_name="Asha", family_name="Rao", date_of_birth="2001-02-03")
call_sql, call_args = [x for x in s.log if x[0].startswith("CALL")][0]
check("call uses backticked procedure name and OUT variable", call_sql.startswith("CALL `register_patient`(%s, %s, %s, %s, @o_4)"))
check("args reordered by name (last, first, dob)", call_args[:3] == ["Rao", "Asha", "2001-02-03"])
check("request_uuid auto-generated (G11)", isinstance(call_args[3], str) and len(call_args[3]) == 36)
check("OUT value returned", r.first_value("patient_id") == 42)
# 2. missing procedure -> NotReady with task name
db._RESOLVED.clear()
s = mk(set(), {})
try:
    s.call("dispense", medicine_id=1)
    check("missing procedure raises NotReady", False)
except db.NotReady as e:
    check("missing procedure raises NotReady naming the task", "P3-4" in str(e))
# 3. empty strings become NULL; unknown args are ignored, not fatal
db._PARAM_CACHE.clear(); db._RESOLVED.clear()
s = mk({"decide_approval"}, {"decide_approval": [P("p_approval_id"), P("p_decision"), P("p_note")]})
s.call("decide_approval", approval_id=5, decision="APPROVED", note="  ", bogus=1)
a = [x for x in s.log if x[0].startswith("CALL")][0][1]
check("blank string -> NULL, extra arg ignored", a == [5, "APPROVED", None])
# 4. second candidate name is found
db._PARAM_CACHE.clear(); db._RESOLVED.clear()
s = mk({"sp_book_appointment"}, {"sp_book_appointment": [P("p_patient_id"), P("p_slot_id")]})
s.call("book_appointment", patient_id=3, appointment_slot_id=9)
check("candidate procedure names are tried in order", db._RESOLVED.get("book_appointment") == "sp_book_appointment")
sys.exit(1 if fails else 0)
