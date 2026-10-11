"""P5-3 Doctor: queue, patient/visit summary, distraction-free prescribing (type-ahead green/blue/red + allergy dialog),
lab orders, short-stay orders, notes, sign-off, emergency override, report review, schedule requests, printable prescription."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import auth, db, timeutil
from ..constants import ROUTES
from ..data import clinical as C, common, patients as P
from ..web import csrf_protect, do, flash, formdata, fd, fi, fs, fdt, qi, redirect, render, error_page

router = APIRouter(prefix="/doctor", dependencies=[Depends(csrf_protect)])
DOC = auth.require("DOCTOR")
PRINTERS = auth.require("DOCTOR", "PHARMACY", "NURSE", "RECEPTIONIST", "OWNER")


@router.get("")
def queue(request: Request, user: db.Actor = Depends(DOC)):
    return render(request, "doctor/queue.html", user, refresh=30, rows=C.my_queue(user))


@router.post("/visits/{vid}/call")
def call_in(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, P.set_visit_status, user, vid, "With Doctor", None, None, fs(f, "request_uuid"),
              ok="Patient called in.", default=f"/doctor/visit/{vid}")


@router.get("/visit/{vid}")
def visit(request: Request, vid: int, user: db.Actor = Depends(DOC)):
    sm = C.visit_summary(user, vid)
    if not sm and not db.STUB:
        return error_page(request, user, "Visit not found.", 404, "/doctor")
    return render(request, "doctor/visit.html", user, sm=sm, vid=vid, labs=common.outside_labs(user), routes=ROUTES,
                  now_input=timeutil.now_local_input(), meds=common.medicines(user))


@router.post("/visit/{vid}/note")
def note(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.add_note, user, vid, fs(f, "note_text"), fs(f, "sharing_classification"), ok="Note saved.",
              default=f"/doctor/visit/{vid}")


@router.post("/visit/{vid}/diagnosis")
def diagnosis(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.add_diagnosis, user, vid, fs(f, "diagnosis_text"), fs(f, "diagnosis_code"), ok="Diagnosis saved.",
              default=f"/doctor/visit/{vid}")


@router.post("/visit/{vid}/lab")
def lab(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.order_lab, user, vid, fi(f, "outside_lab_id"), fs(f, "test_name"), fs(f, "request_uuid"),
              ok="Lab test ordered.", default=f"/doctor/visit/{vid}")


@router.post("/visit/{vid}/signoff")
def signoff(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.sign_off, user, vid, fs(f, "discharge_instructions"), ok="Clinical sign-off recorded.",
              default=f"/doctor/visit/{vid}")


@router.post("/visit/{vid}/override")
def override(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.emergency_override, user, vid, fs(f, "clinical_justification"),
              ok="Emergency override logged. The owner has been alerted. Medical documents are never withheld.",
              default=f"/doctor/visit/{vid}")


@router.post("/visit/{vid}/stay")
def start_stay(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, P.start_short_stay, user, vid, fs(f, "admission_reason"), fs(f, "request_uuid"),
              ok="Short-stay episode started.", default=f"/doctor/visit/{vid}")


@router.post("/visit/{vid}/stay-extension")
def stay_ext(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, P.request_extension, user, fi(f, "short_stay_episode_id"), fdt(f, "extended_until"), fs(f, "reason"),
              ok="Extension requested. Waiting for owner approval.", default=f"/doctor/visit/{vid}")


@router.post("/rx-line/{line_id}/status")
def line_status(request: Request, line_id: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.set_line_status, user, line_id, fs(f, "status"), fs(f, "reason"), ok="Item status updated.",
              nxt=fs(f, "next"), default="/doctor")


# ---------------------------------------------------------------- short-stay orders
@router.post("/visit/{vid}/order")
def order(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    sm = C.visit_summary(user, vid) if not db.STUB else None
    ep = sm["v"]["short_stay_episode_id"] if sm else None
    kind = fs(f, "item_kind", "medication")
    data = dict(medicine_id=fi(f, "medicine_id"), free_text=fs(f, "free_text"), dose_value=fd(f, "dose_value"),
                dose_unit=fs(f, "dose_unit"), route=fs(f, "route"), first_at=fdt(f, "first_scheduled_at"),
                interval=fi(f, "repeat_interval_minutes"), qty=fd(f, "quantity_ordered"),
                instructions=fs(f, "instructions"), finalize=bool(fs(f, "finalize")))
    return do(request, C.create_order, user, vid, ep, kind, data, fs(f, "request_uuid"),
              ok="Order created." + (" Nurses can now see it." if data["finalize"] else " Finalize it so nurses can act."),
              default=f"/doctor/visit/{vid}")


@router.post("/order/{oid}/status")
def order_status(request: Request, oid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.set_order_status, user, oid, fs(f, "status"), fs(f, "reason"), ok="Order updated.",
              nxt=fs(f, "next"), default="/doctor")


@router.post("/order-item/{iid}/stop")
def order_stop(request: Request, iid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.stop_order_item, user, iid, fs(f, "reason") or "Stopped by doctor", ok="Order item stopped.",
              nxt=fs(f, "next"), default="/doctor")


# ---------------------------------------------------------------- prescribing
@router.get("/visit/{vid}/prescribe")
def prescribe_page(request: Request, vid: int, user: db.Actor = Depends(DOC)):
    v = C.visit_brief(user, vid) if not db.STUB else None
    if not v and not db.STUB:
        return error_page(request, user, "Visit not found.", 404, "/doctor")
    rx = C.current_prescription(user, vid) if v else None
    allergies = [x["allergen"] for x in C.current_allergies(user, v["patient_id"])] if v else []
    existing = []
    for n, i in enumerate((rx or {}).get("active_items", [])):
        name = i["medicine_name"] or ""
        if i["strength_value"] is not None:
            name = f"{name} {i['strength_value']:g}{i['strength_unit'] or ''}" if isinstance(i["strength_value"], (int, float)) else f"{name} {i['strength_value']}{i['strength_unit'] or ''}"
        existing.append(dict(line_id=i["prescription_line_id"], medicine_id=i["medicine_id"], name=name,
                             free_text=None if i["medicine_id"] else i["free_text_medicine_name"], dose=i["chosen_dose_text"],
                             schedule=i["chosen_schedule"], food=i["food_instruction"], days=i["duration_days"],
                             qty=float(i["quantity_prescribed"]) if i["quantity_prescribed"] is not None else None,
                             state="green" if i["medicine_id"] else "red",
                             label="Previously prescribed" if i["medicine_id"] else "Not in catalog (free text)"))
    cfg = {"patientId": v["patient_id"] if v else None, "allergies": allergies, "existing": existing,
           "nextIndex": len(existing) + 10}
    return render(request, "doctor/prescribe.html", user, v=v, vid=vid, rx=rx, allergies=allergies, cfg=cfg)


def _parse_rows(f) -> list[dict]:
    rows = []
    for rid in (fs(f, "row_ids", "") or "").split(","):
        rid = rid.strip()
        if not rid:
            continue
        rows.append(dict(line_id=fi(f, f"line_id_{rid}"), medicine_id=fi(f, f"medicine_id_{rid}"),
                         free_text=fs(f, f"free_text_{rid}"), dose=fs(f, f"dose_{rid}"), schedule=fs(f, f"schedule_{rid}"),
                         food=fs(f, f"food_{rid}"), days=fi(f, f"days_{rid}"), qty=fd(f, f"qty_{rid}"),
                         remove=bool(fs(f, f"remove_{rid}")), confirm=bool(fs(f, f"confirm_{rid}")),
                         override=fs(f, f"override_{rid}")))
    return rows


@router.post("/visit/{vid}/prescribe")
def prescribe_save(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    try:
        C.save_prescription(user, vid, _parse_rows(f), fs(f, "change_reason"), fs(f, "request_uuid"))
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect(f"/doctor/visit/{vid}/prescribe")
    flash(request, "Prescription saved.")
    return redirect(f"/doctor/visit/{vid}")


@router.get("/api/suggest")
def api_suggest(request: Request, q: str = "", user: db.Actor = Depends(DOC)):
    try:
        return JSONResponse(C.suggest_medicines(user, q, qi(request, "patient_id")), headers={"Cache-Control": "no-store"})
    except db.RuleError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/visit/{vid}/prescription/print")
def print_rx(request: Request, vid: int, user: db.Actor = Depends(PRINTERS)):
    d = C.print_data(user, vid) if not db.STUB else None
    return render(request, "doctor/print.html", user, d=d)


# ---------------------------------------------------------------- lab reports to review, schedule
@router.get("/reports")
def reports(request: Request, user: db.Actor = Depends(DOC)):
    return render(request, "doctor/reports.html", user, rows=C.reports_to_review(user))


@router.post("/reports/{rid}/review")
def review(request: Request, rid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.review_report, user, rid, fs(f, "review_note"), ok="Marked as reviewed by you.",
              nxt=fs(f, "next"), default="/doctor/reports")


@router.post("/reports/{rid}/critical")
def critical(request: Request, rid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.mark_critical, user, rid, fs(f, "mark"), fs(f, "reason"), ok="Marked.", nxt=fs(f, "next"), default="/doctor/reports")


@router.post("/reports/{rid}/retract")
def retract(request: Request, rid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.retract_report, user, rid, fs(f, "retraction_kind"), fs(f, "note"),
              ok="Report retracted. The owner has been alerted.", nxt=fs(f, "next"), default="/doctor/reports")


@router.post("/visit/{vid}/stay-close")
def stay_close(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, C.close_stay, user, fi(f, "short_stay_episode_id"), fs(f, "reason") or "Stay ended", ok="Short stay ended.",
              default=f"/doctor/visit/{vid}")


@router.get("/schedule")
def schedule(request: Request, user: db.Actor = Depends(DOC)):
    return render(request, "doctor/schedule.html", user, rows=P.my_schedule(user, user.staff_id),
                  reqs=P.my_schedule_requests(user, user.staff_id))


@router.post("/schedule/request")
def schedule_request(request: Request, f=Depends(formdata), user: db.Actor = Depends(DOC)):
    return do(request, P.request_schedule_change, user, change_kind=fs(f, "change_kind"),
              target_doctor_schedule_id=fi(f, "target_doctor_schedule_id"), weekday=fi(f, "weekday"),
              start_time=fs(f, "start_time"), end_time=fs(f, "end_time"), slot_minutes=fi(f, "slot_minutes"),
              valid_from=fs(f, "valid_from"), reason=fs(f, "reason"), ok="Request sent to reception/owner.",
              default="/doctor/schedule")
