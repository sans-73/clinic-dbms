"""P5-4a Pharmacy: stock (derived from ledger), receiving, dispensing (prescription / outside prescription / OTC),
FEFO, expiry list, counter sales + payments + returns, leftover decisions, catalog review, daily cash handover."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import auth, db, timeutil
from ..constants import PAYMENT_METHODS, SCHEDULE_CLASSES
from ..data import common, pharmacy as PH
from ..web import csrf_protect, do, flash, formdata, fd, fi, fs, fdt, qi, redirect, render

router = APIRouter(prefix="/pharmacy", dependencies=[Depends(csrf_protect)])
PHARM = auth.require("PHARMACY")
PHARM_OWNER = auth.require("PHARMACY", "OWNER")


@router.get("")
def stock(request: Request, user: db.Actor = Depends(PHARM_OWNER)):
    medicine_id = qi(request, "medicine_id")
    return render(request, "pharmacy/stock.html", user, meds=PH.stock_by_medicine(user), restock=PH.restock_demand(user),
                  batches=PH.batches(user, medicine_id) if medicine_id else PH.batches(user), medicine_id=medicine_id,
                  cost=PH.can_see_cost(user), is_pharm=user.role == "PHARMACY")


@router.get("/receive")
def receive_form(request: Request, user: db.Actor = Depends(PHARM)):
    return render(request, "pharmacy/receive.html", user, meds=common.medicines(user), suppliers=PH.suppliers(user))


@router.post("/receive")
def receive(request: Request, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    return do(request, PH.receive_stock, user, medicine_id=fi(f, "medicine_id"), batch_code=fs(f, "batch_code"),
              expiry_date=fs(f, "expiry_date"), quantity=fd(f, "quantity"), cost_price=fd(f, "cost_price"), mrp=fd(f, "mrp"),
              supplier_id=fi(f, "supplier_id"), request_uuid=fs(f, "request_uuid"), ok="Stock received.", default="/pharmacy")


@router.post("/adjust")
def adjust(request: Request, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    return do(request, PH.request_adjustment, user, fi(f, "stock_batch_id"), fd(f, "quantity_delta"),
              fs(f, "movement_type", "adjustment"), fs(f, "reason"),
              ok="Request sent. The owner must approve before stock changes.", default="/pharmacy")


# ---------------------------------------------------------------- dispensing against clinic prescriptions
@router.get("/dispense")
def dispense_page(request: Request, q: str = "", user: db.Actor = Depends(PHARM)):
    rows = PH.pending_prescriptions(user, q)
    for r in rows:
        r["fefo"] = PH.fefo(user, r["medicine_id"]) if r["medicine_id"] else []
        r["order_items"] = PH.order_items_for(user, r["visit_id"], r["medicine_id"]) if r["medicine_id"] else []
    return render(request, "pharmacy/dispense.html", user, rows=rows, q=q)


@router.post("/dispense/rx")
def dispense_rx(request: Request, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    return do(request, PH.dispense, user, "clinic_prescription", fi(f, "medicine_id"), fd(f, "quantity"),
              fs(f, "request_uuid"), fi(f, "clinical_order_item_id"), prescription_item_id=fi(f, "prescription_item_id"),
              stock_batch_id=fi(f, "stock_batch_id"), visit_id=fi(f, "visit_id"),
              ok="Dispensed. Stock reduced and the bill line was added.", default="/pharmacy/dispense")


# ---------------------------------------------------------------- counter sales
@router.get("/counter")
def counter_list(request: Request, user: db.Actor = Depends(PHARM)):
    return render(request, "pharmacy/counter_list.html", user, sales=PH.counter_sales(user))


@router.post("/counter/start")
def counter_start(request: Request, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    try:
        cid = PH.new_counter_customer(user, fs(f, "phone"), fs(f, "display_name"))
        if fs(f, "prescriber_name") or fs(f, "prescriber_registration_no"):
            PH.add_outside_prescription(user, cid, fs(f, "prescriber_name"), fs(f, "prescriber_registration_no"),
                                        fs(f, "prescription_date"))
        sid = PH.start_counter_sale(user, cid)
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect("/pharmacy/counter")
    return redirect(f"/pharmacy/counter/{sid}")


@router.get("/counter/{sid}")
def counter_view(request: Request, sid: int, user: db.Actor = Depends(PHARM)):
    s = PH.counter_sale(user, sid)
    meds = common.medicines(user)
    return render(request, "pharmacy/counter.html", user, s=s, meds=meds, methods=PAYMENT_METHODS,
                  otc_meds=[m for m in meds if (m["schedule_class"] or "none") == "none"])


@router.post("/counter/{sid}/line")
def counter_line(request: Request, sid: int, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    s = PH.counter_sale(user, sid)
    if not s:
        flash(request, "Sale not found.", "err")
        return redirect("/pharmacy/counter")
    src = fs(f, "source_type", "otc")
    extra = {}
    if src == "outside_prescription":
        rx = db.one(user, "SELECT outside_prescription_id FROM outside_prescription WHERE counter_customer_id=%s ORDER BY outside_prescription_id DESC LIMIT 1", (s["counter_customer_id"],))
        if not rx:
            flash(request, "Record the outside prescription (doctor name and registration number) first.", "err")
            return redirect(f"/pharmacy/counter/{sid}")
        extra["outside_prescription_id"] = rx["outside_prescription_id"]
    return do(request, PH.dispense, user, src, fi(f, "medicine_id"), fd(f, "quantity"), fs(f, "request_uuid"), None,
              counter_sale_id=sid, counter_customer_id=s["counter_customer_id"], stock_batch_id=fi(f, "stock_batch_id"), **extra,
              ok="Added to the sale.", default=f"/pharmacy/counter/{sid}")


@router.post("/counter/{sid}/rx")
def counter_rx(request: Request, sid: int, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    s = PH.counter_sale(user, sid)
    return do(request, PH.add_outside_prescription, user, s["counter_customer_id"] if s else None, fs(f, "prescriber_name"),
              fs(f, "prescriber_registration_no"), fs(f, "prescription_date"), ok="Outside prescription recorded.",
              default=f"/pharmacy/counter/{sid}")


@router.post("/counter/{sid}/pay")
def counter_pay(request: Request, sid: int, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    return do(request, PH.counter_payment, user, sid, fd(f, "amount"), fs(f, "method", "cash"), fs(f, "request_uuid"),
              ok="Payment recorded. Receipt sent if a phone number is on file.", default=f"/pharmacy/counter/{sid}")


@router.post("/counter/{sid}/return")
def counter_ret(request: Request, sid: int, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    return do(request, PH.counter_return, user, sid, fi(f, "dispensing_id"), fd(f, "quantity"), fs(f, "reason"),
              ok="Return recorded. A refund needs owner approval.", default=f"/pharmacy/counter/{sid}")


# ---------------------------------------------------------------- leftovers
@router.get("/leftovers")
def leftovers(request: Request, user: db.Actor = Depends(PHARM)):
    return render(request, "pharmacy/leftovers.html", user, rows=PH.pending_leftovers(user))


@router.post("/leftovers/{rid}/decide")
def leftover_decide(request: Request, rid: int, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    return do(request, PH.decide_leftover, user, rid, fs(f, "decision"), fs(f, "decision_reason"),
              ok="Decision recorded.", default="/pharmacy/leftovers")


# ---------------------------------------------------------------- catalog review (pharmacy or owner)
@router.get("/catalog-review")
def catalog_review(request: Request, user: db.Actor = Depends(PHARM_OWNER)):
    return render(request, "pharmacy/catalog_review.html", user, rows=PH.review_queue(user), meds=common.medicines(user),
                  classes=SCHEDULE_CLASSES)


@router.post("/catalog-review/{qid}")
def catalog_resolve(request: Request, qid: int, f=Depends(formdata), user: db.Actor = Depends(PHARM_OWNER)):
    act = fs(f, "action")
    if act == "added_to_catalog":
        return do(request, PH.add_medicine, user, qid, brand_name=fs(f, "brand_name"), generic_display_name=fs(f, "generic_display_name"),
                  strength_value=fd(f, "strength_value"), strength_unit=fs(f, "strength_unit"), dosage_form=fs(f, "dosage_form"),
                  stock_unit=fs(f, "stock_unit"), schedule_class=fs(f, "schedule_class", "none"),
                  returnable=1 if fs(f, "returnable") else 0, ok="Added to the catalog.", default="/pharmacy/catalog-review")
    return do(request, PH.review_resolve, user, qid, act, fi(f, "resolved_medicine_id"), fs(f, "resolution_note"),
              ok="Resolved.", default="/pharmacy/catalog-review")


# ---------------------------------------------------------------- daily cash handover (declare)
@router.get("/cash")
def cash(request: Request, user: db.Actor = Depends(PHARM)):
    s, e = timeutil.local_day_window()
    return render(request, "pharmacy/cash.html", user, decls=PH.my_declarations(user), start=s, end=e,
                  expected=PH.cash_expected(user, user.staff_id, s, e), start_in=timeutil.fmt(s, "%Y-%m-%dT%H:%M"),
                  end_in=timeutil.fmt(e, "%Y-%m-%dT%H:%M"))


@router.post("/cash")
def cash_declare(request: Request, f=Depends(formdata), user: db.Actor = Depends(PHARM)):
    return do(request, PH.declare_cash, user, fdt(f, "period_start"), fdt(f, "period_end"), fd(f, "declared_amount"),
              fs(f, "note"), ok="Declared. The accountant will count and confirm.", default="/pharmacy/cash")
