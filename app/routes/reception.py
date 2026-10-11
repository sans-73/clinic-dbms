"""P5-2 Reception: patients (duplicate check before saving), emergency patient, merge, appointments + slot grid,
check-in, queue board, demographic corrections, schedule-change application."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request

from .. import auth, db, timeutil
from ..constants import APPOINTMENT_STATUSES, VISIT_STATUSES
from ..data import common, patients as P
from ..web import csrf_protect, do, flash, formdata, fd, fi, fs, qi, redirect, render

router = APIRouter(prefix="/reception", dependencies=[Depends(csrf_protect)])
REC = auth.require("RECEPTIONIST")
GENDERS = ["Male", "Female", "Other"]


# ---------------------------------------------------------------- queue board
@router.get("")
def queue_board(request: Request, user: db.Actor = Depends(REC)):
    rows = P.queue(user)
    stays = P.active_stays(user)
    return render(request, "reception/queue.html", user, refresh=30, rows=rows, stays=stays, statuses=VISIT_STATUSES,
                  doctors=common.doctors(user), today=timeutil.fmt(timeutil.utcnow(), "%A, %d %B %Y"))


@router.post("/visits/{visit_id}/status")
def visit_status(request: Request, visit_id: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.set_visit_status, user, visit_id, fs(f, "status"), fs(f, "location_label"), fs(f, "reason"),
              fs(f, "request_uuid"), ok="Status updated.", default="/reception")


# ---------------------------------------------------------------- patients
@router.get("/patients")
def patients(request: Request, q: str = "", user: db.Actor = Depends(REC)):
    return render(request, "reception/patients.html", user, rows=P.search(user, q), q=q)


@router.get("/patients/new")
def new_patient(request: Request, user: db.Actor = Depends(REC)):
    return render(request, "reception/new.html", user, genders=GENDERS, vals={}, candidates=None)


def _reg_fields(f) -> dict:
    return dict(given_name=fs(f, "given_name"), middle_name=fs(f, "middle_name"), family_name=fs(f, "family_name"),
                date_of_birth=fs(f, "date_of_birth"), gender=fs(f, "gender"), phone=fs(f, "phone"),
                aadhaar_last4=fs(f, "aadhaar_last4"), address_line=fs(f, "address_line"), locality=fs(f, "locality"),
                city=fs(f, "city"), state=fs(f, "state"), postal_code=fs(f, "postal_code"),
                attendant_name=fs(f, "attendant_name"), attendant_relationship=fs(f, "attendant_relationship"),
                attendant_phone=fs(f, "attendant_phone"))


@router.post("/patients/check")
def check_duplicates(request: Request, f=Depends(formdata), user: db.Actor = Depends(REC)):
    """Step 1: show ranked duplicate candidates. The receptionist decides (G12: never auto-merge)."""
    vals = _reg_fields(f)
    if not vals["given_name"] or not vals["family_name"]:
        flash(request, "First name and last name are required.", "err")
        return redirect("/reception/patients/new")
    try:
        cands = P.find_candidates(user, given_name=vals["given_name"], family_name=vals["family_name"],
                                  date_of_birth=vals["date_of_birth"], phone=vals["phone"],
                                  aadhaar_last4=vals["aadhaar_last4"], address_line=vals["address_line"])
    except db.RuleError as e:
        flash(request, str(e), "err")
        cands = []
    return render(request, "reception/new.html", user, genders=GENDERS, vals=vals, candidates=cands)


@router.post("/patients/create")
def create_patient(request: Request, f=Depends(formdata), user: db.Actor = Depends(REC)):
    vals = _reg_fields(f)
    if fs(f, "confirm_new") != "1":
        flash(request, "Tick the box to confirm this is a NEW person (not one of the candidates).", "err")
        return redirect("/reception/patients/new")
    try:
        pid, code = P.register(user, **vals, request_uuid=fs(f, "request_uuid"))
    except db.RuleError as e:
        flash(request, str(e), "err")
        return render(request, "reception/new.html", user, genders=GENDERS, vals=vals, candidates=None)
    flash(request, f"Patient registered{(' as ' + str(code)) if code else ''}.")
    return redirect(f"/reception/patients/{pid}" if pid else "/reception/patients")


@router.get("/patients/{pid}")
def patient_profile(request: Request, pid: int, user: db.Actor = Depends(REC)):
    prof = P.profile(user, pid)
    if db.STUB:
        prof = None
    if not prof:
        return render(request, "reception/profile.html", user, prof=None)
    return render(request, "reception/profile.html", user, prof=prof, doctors=common.doctors(user), genders=GENDERS,
                  edit=P.EDITABLE_DEMOGRAPHICS)


@router.post("/patients/{pid}/edit")
def edit_patient(request: Request, pid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.update_demographics, user, pid, given_name=fs(f, "given_name"), middle_name=fs(f, "middle_name"),
              family_name=fs(f, "family_name"), gender=fs(f, "gender"), date_of_birth=fs(f, "date_of_birth"),
              approximate_age=fi(f, "approximate_age"), aadhaar_last4=fs(f, "aadhaar_last4"),
              ok="Details corrected (old values are kept in the audit log).", default=f"/reception/patients/{pid}")


@router.post("/patients/{pid}/phone")
def add_phone(request: Request, pid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.add_phone, user, pid, fs(f, "phone"), 1 if fs(f, "is_primary") else 0, fs(f, "request_uuid"),
              ok="Phone added.", default=f"/reception/patients/{pid}")


@router.post("/patients/{pid}/attendant")
def add_attendant(request: Request, pid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.add_attendant, user, pid, fs(f, "name"), fs(f, "relationship"), fs(f, "phone"),
              1 if fs(f, "is_emergency_contact") else 0, ok="Attendant added.", default=f"/reception/patients/{pid}")


@router.post("/patients/{pid}/allergy")
def add_allergy(request: Request, pid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.add_allergy, user, pid, fs(f, "allergen_text"), fs(f, "reaction"), fs(f, "severity"),
              ok="Allergy recorded.", default=f"/reception/patients/{pid}")


@router.post("/patients/{pid}/walkin")
def walk_in(request: Request, pid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.start_visit, user, pid, fs(f, "entry_type", "walk_in"), fi(f, "doctor_staff_id"),
              fs(f, "request_uuid"), ok="Visit started. Patient is in the queue.", default="/reception")


# ---------------------------------------------------------------- emergency unknown patient
@router.get("/emergency")
def emergency_form(request: Request, user: db.Actor = Depends(REC)):
    return render(request, "reception/emergency.html", user, genders=GENDERS, doctors=common.doctors(user))


@router.post("/emergency")
def emergency_submit(request: Request, f=Depends(formdata), user: db.Actor = Depends(REC)):
    try:
        pid, code = P.register_unknown(user, fs(f, "gender"), fi(f, "approximate_age"), fs(f, "request_uuid"))
        vid = P.start_visit(user, pid, "emergency", fi(f, "doctor_staff_id"))
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect("/reception/emergency")
    flash(request, f"Unknown patient registered as {code or pid}. Emergency visit started.")
    return redirect("/reception")


# ---------------------------------------------------------------- merge (human confirmed)
@router.get("/merge")
def merge_form(request: Request, user: db.Actor = Depends(REC)):
    source, target = qi(request, "source"), qi(request, "target")
    s = P.profile(user, source) if source and not db.STUB else None
    t = P.profile(user, target) if target and not db.STUB else None
    return render(request, "reception/merge.html", user, s=s, t=t, source=source, target=target)


@router.post("/merge")
def merge_submit(request: Request, f=Depends(formdata), user: db.Actor = Depends(REC)):
    if fs(f, "confirm") != "1":
        flash(request, "Tick the confirmation: you have checked that both records are the same person.", "err")
        return redirect(f"/reception/merge?source={fi(f, 'source')}&target={fi(f, 'target')}")
    try:
        P.merge(user, fi(f, "source"), fi(f, "target"), fs(f, "reason"), fs(f, "tier"), fs(f, "request_uuid"))
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect(f"/reception/merge?source={fi(f, 'source')}&target={fi(f, 'target')}")
    flash(request, "Records merged. A merge log entry was written.")
    return redirect(f"/reception/patients/{fi(f, 'target')}")


# ---------------------------------------------------------------- appointments
@router.get("/appointments")
def appointments(request: Request, q: str = "", user: db.Actor = Depends(REC)):
    doctor, patient_id = qi(request, "doctor"), qi(request, "patient_id")
    day = timeutil.parse_date(request.query_params.get("date")) or date.today()
    docs = common.doctors(user)
    doctor = doctor or (docs[0]["staff_id"] if docs else None)
    slot_rows = P.slots(user, doctor, day) if doctor else []
    appts = P.appointments(user, day, doctor)
    patient = common.patient_brief(user, patient_id) if patient_id and not db.STUB else None
    found = P.search(user, q, 8) if q else []
    return render(request, "reception/appointments.html", user, day=day.isoformat(), doctors=docs, doctor=doctor,
                  slots=slot_rows, appts=appts, patient=patient, found=found, q=q, statuses=APPOINTMENT_STATUSES,
                  patient_id=patient_id)


@router.post("/appointments/book")
def book(request: Request, f=Depends(formdata), user: db.Actor = Depends(REC)):
    if not fi(f, "patient_id"):
        flash(request, "Choose the patient first (search above the slot grid).", "err")
        return redirect(f"/reception/appointments?date={fs(f, 'day')}&doctor={fi(f, 'doctor_staff_id')}")
    return do(request, P.book, user, fi(f, "patient_id"), fi(f, "doctor_staff_id"), fi(f, "appointment_slot_id"),
              "reception", fs(f, "request_uuid"), ok="Appointment booked.", nxt=fs(f, "next"), default="/reception/appointments")


@router.post("/appointments/{aid}/status")
def appt_status(request: Request, aid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.set_appointment_status, user, aid, fs(f, "status"), fs(f, "reason"), fs(f, "request_uuid"),
              ok="Appointment updated.", nxt=fs(f, "next"), default="/reception/appointments")


@router.post("/appointments/{aid}/checkin")
def appt_checkin(request: Request, aid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.check_in, user, aid, fs(f, "request_uuid"), ok="Checked in. Patient added to the queue.",
              default="/reception")


@router.post("/appointments/{aid}/reschedule")
def appt_resched(request: Request, aid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.reschedule, user, aid, fi(f, "appointment_slot_id"), fs(f, "reason"), fs(f, "request_uuid"),
              ok="Appointment moved (history is kept).", nxt=fs(f, "next"), default="/reception/appointments")


@router.post("/slots/generate")
def gen_slots(request: Request, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.generate_slots, user, fi(f, "doctor_staff_id"), fs(f, "from_date"), fs(f, "to_date"),
              ok="Slots generated.", nxt=fs(f, "next"), default="/reception/appointments")


# ---------------------------------------------------------------- schedule changes (doctor requests, authorized person applies)
@router.get("/schedule-changes")
def schedule_changes(request: Request, user: db.Actor = Depends(REC)):
    return render(request, "reception/schedule_changes.html", user, rows=P.pending_schedule_changes(user))


@router.post("/schedule-changes/{rid}/apply")
def apply_change(request: Request, rid: int, f=Depends(formdata), user: db.Actor = Depends(REC)):
    return do(request, P.apply_schedule_change, user, rid, fs(f, "outcome"), fs(f, "reason"), ok="Done.",
              default="/reception/schedule-changes")
