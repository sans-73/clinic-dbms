"""P5-4b Nurse: ward task list, dose logging, vitals with trend, medicine custody (leftover request, family handover,
acknowledgement), incoming lab-report attachment with two-identifier check, stay extension request."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import auth, db, timeutil
from ..constants import DOSE_STATUSES, IDENTIFIER_KINDS, OBS_TYPES, ROUTES
from ..data import nurse as N, patients as P
from ..web import csrf_protect, do, flash, formdata, fd, fi, fs, fdt, qi, redirect, render

router = APIRouter(prefix="/nurse", dependencies=[Depends(csrf_protect)])
NUR = auth.require("NURSE")


@router.get("")
def tasks(request: Request, user: db.Actor = Depends(NUR)):
    return render(request, "nurse/tasks.html", user, refresh=60, rows=N.ward_tasks(user), stays=P.active_stays(user),
                  acks=N.pending_acks(user))


@router.get("/dose/{item_id}")
def dose_form(request: Request, item_id: int, user: db.Actor = Depends(NUR)):
    return render(request, "nurse/dose.html", user, it=N.item_for_dose(user, item_id), statuses=DOSE_STATUSES, routes=ROUTES,
                  now_input=timeutil.now_local_input(), item_id=item_id)


@router.post("/dose/{item_id}")
def dose_save(request: Request, item_id: int, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    data = dict(status=fs(f, "status"), order_item_supply_id=fi(f, "order_item_supply_id"), quantity_consumed=fd(f, "quantity_consumed"),
                actual_dose_value=fd(f, "actual_dose_value"), actual_dose_unit=fs(f, "actual_dose_unit"), actual_route=fs(f, "actual_route"),
                deviation_reason=fs(f, "deviation_reason"), occurred_at=fdt(f, "occurred_at"), request_uuid=fs(f, "request_uuid"))
    return do(request, N.record_dose, user, item_id, data, ok="Dose recorded.", default="/nurse")


@router.get("/vitals")
def vitals(request: Request, user: db.Actor = Depends(NUR)):
    visit_id = qi(request, "visit_id")
    return render(request, "nurse/vitals.html", user, visits=N.open_visits(user), visit_id=visit_id, obs=OBS_TYPES,
                  trend=N.vitals_trend(user, visit_id) if visit_id else {})


@router.post("/vitals")
def vitals_save(request: Request, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    vid = fi(f, "visit_id")
    vals = {"bp_systolic": fd(f, "bp_systolic"), "bp_diastolic": fd(f, "bp_diastolic"), "pulse": fd(f, "pulse"),
            "temperature": fd(f, "temperature"), "spo2": fd(f, "spo2"), "resp_rate": fd(f, "resp_rate"),
            "weight": fd(f, "weight"), "height": fd(f, "height"), "blood_glucose": fd(f, "blood_glucose")}
    return do(request, N.record_vitals, user, vid, vals, fi(f, "short_stay_episode_id"), ok="Vitals saved.",
              default=f"/nurse/vitals?visit_id={vid}")


# ---------------------------------------------------------------- custody
@router.get("/custody")
def custody(request: Request, user: db.Actor = Depends(NUR)):
    visit_id = qi(request, "visit_id")
    return render(request, "nurse/custody.html", user, visits=N.open_visits(user), visit_id=visit_id,
                  rec=N.reconciliation(user, visit_id) if visit_id else [], att=N.attendants(user, visit_id) if visit_id else [],
                  acks=N.pending_acks(user))


@router.post("/custody/leftover")
def leftover_ask(request: Request, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    return do(request, N.ask_leftover, user, fi(f, "dispensing_id"), fd(f, "quantity"), fs(f, "condition_note"),
              ok="Pharmacy has been asked.", nxt=fs(f, "next"), default="/nurse/custody")


@router.post("/custody/family")
def family_handoff(request: Request, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    return do(request, N.handoff_to_family, user, fi(f, "dispensing_id"), fi(f, "attendant_id"), fd(f, "quantity"), fs(f, "request_uuid"),
              ok="Handover recorded. A different staff member must witness and acknowledge it.", nxt=fs(f, "next"), default="/nurse/custody")


@router.post("/custody/ack")
def ack(request: Request, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    return do(request, N.ack_handoff, user, fi(f, "custody_handoff_id"), fd(f, "acknowledged_quantity"), fs(f, "witness_note"),
              ok="Received and acknowledged.", nxt=fs(f, "next"), default="/nurse/custody")


# ---------------------------------------------------------------- lab reports in
@router.get("/lab")
def lab(request: Request, user: db.Actor = Depends(NUR)):
    return render(request, "nurse/lab.html", user, rows=N.labs_waiting(user), kinds=IDENTIFIER_KINDS)


@router.post("/lab/{lab_order_id}/status")
def lab_status(request: Request, lab_order_id: int, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    return do(request, N.lab_status, user, lab_order_id, fs(f, "status"), ok="Lab status updated.", default="/nurse/lab")


@router.post("/lab/{lab_order_id}/attach")
async def lab_attach(request: Request, lab_order_id: int, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    up = f.get("report_file")
    data = await up.read() if up is not None and hasattr(up, "read") else b""
    idents = [dict(kind=fs(f, f"id{n}_kind"), value=fs(f, f"id{n}_value"), matches=bool(fs(f, f"id{n}_ok"))) for n in (1, 2, 3)]
    try:
        N.attach_report(user, lab_order_id, data, getattr(up, "filename", ""), fs(f, "source", "from_lab"), idents,
                        fi(f, "supersedes_lab_report_id"), fs(f, "request_uuid"))
        flash(request, "Report attached and logged.")
    except db.RuleError as e:
        flash(request, str(e), "err")
    return redirect("/nurse/lab")


# ---------------------------------------------------------------- stay extension request
@router.post("/stay-extension")
def stay_ext(request: Request, f=Depends(formdata), user: db.Actor = Depends(NUR)):
    return do(request, P.request_extension, user, fi(f, "short_stay_episode_id"), fdt(f, "extended_until"), fs(f, "reason"),
              ok="Extension requested. Waiting for owner approval.", default="/nurse")
