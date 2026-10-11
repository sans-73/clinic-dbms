"""Serves stored lab report files to authorized staff only (never public; family gets its own gated route)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from .. import auth, db
from ..data import nurse as N
from ..web import error_page

router = APIRouter(prefix="/files")


@router.get("/lab/{report_id}")
def lab_file(request: Request, report_id: int, user: db.Actor = Depends(auth.require("DOCTOR", "NURSE", "OWNER", "RECEPTIONIST"))):
    r = db.one(user, "SELECT file_path FROM lab_report WHERE lab_report_id=%s", (report_id,))
    p = N.resolve_upload(r["file_path"]) if r else None
    if not p:
        return error_page(request, user, "Report file not found.", 404)
    return FileResponse(str(p), headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
