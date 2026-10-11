"""P5-5 Billing and discharge: bills, price-list lines, payments, receipts, approval requests (discount / free service /
refund / payment reversal / post-finalization correction) with 'complete after approval', discharge checklist, dues, cash receive."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import auth, db, timeutil
from ..constants import PAYMENT_METHODS
from ..data import billing as B, pharmacy as PH
from ..web import csrf_protect, do, flash, formdata, fd, fi, fs, redirect, render, error_page

router = APIRouter(prefix="/billing", dependencies=[Depends(csrf_protect)])
VIEW = auth.require("ACCOUNTANT", "RECEPTIONIST", "OWNER", "DOCTOR")
ACC = auth.require("ACCOUNTANT")
ACC_REC = auth.require("ACCOUNTANT", "RECEPTIONIST")
ASKERS = auth.require("ACCOUNTANT", "DOCTOR", "RECEPTIONIST")
DISCHARGERS = auth.require("ACCOUNTANT", "RECEPTIONIST", "OWNER")


@router.get("")
def bills(request: Request, q: str = "", due: int = 0, user: db.Actor = Depends(VIEW)):
    return render(request, "billing/list.html", user, rows=B.bills(user, q, bool(due)), q=q, due=due,
                  nobill=B.visits_without_bill(user))


@router.post("/visit/{vid}/open")
def open_bill(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(ACC_REC)):
    try:
        bid = B.open_bill_for_visit(user, vid)
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect("/billing")
    return redirect(f"/billing/bill/{bid}" if bid else "/billing")


@router.get("/bill/{bid}")
def bill_view(request: Request, bid: int, user: db.Actor = Depends(VIEW)):
    b = B.bill(user, bid)
    if not b and not db.STUB:
        return error_page(request, user, "Bill not found.", 404, "/billing")
    return render(request, "billing/bill.html", user, b=b, bid=bid, methods=PAYMENT_METHODS,
                  prices=B.price_items(user) if user.role in ("ACCOUNTANT", "RECEPTIONIST") else [])


@router.post("/bill/{bid}/line")
def add_line(request: Request, bid: int, f=Depends(formdata), user: db.Actor = Depends(ACC_REC)):
    return do(request, B.add_price_line, user, bid, fi(f, "price_list_id"), fd(f, "quantity"), ok="Line added.", default=f"/billing/bill/{bid}")


@router.post("/bill/{bid}/finalize")
def finalize(request: Request, bid: int, f=Depends(formdata), user: db.Actor = Depends(ACC_REC)):
    return do(request, B.finalize, user, bid, fs(f, "reason"), ok="Bill finalized. Only credit/debit lines can be added now.", default=f"/billing/bill/{bid}")


@router.post("/bill/{bid}/pay")
def pay(request: Request, bid: int, f=Depends(formdata), user: db.Actor = Depends(ACC)):
    return do(request, B.pay, user, bid, fd(f, "amount"), fs(f, "method", "cash"), fs(f, "request_uuid"),
              ok="Payment recorded. Receipt message queued.", default=f"/billing/bill/{bid}")


@router.post("/bill/{bid}/request")
def request_approval(request: Request, bid: int, f=Depends(formdata), user: db.Actor = Depends(ASKERS)):
    kind = fs(f, "kind")
    allowed = {"discount", "free_service", "post_finalization_bill_correction"} if user.role != "ACCOUNTANT" else \
              {"discount", "free_service", "refund", "payment_reversal", "post_finalization_bill_correction"}
    if kind not in allowed:
        flash(request, "Your role cannot request that.", "err")
        return redirect(f"/billing/bill/{bid}")
    return do(request, B.request_money_approval, user, kind, bid, fd(f, "amount"), fs(f, "reason"),
              payment=fi(f, "payment_id"), method=fs(f, "method"), line_type=fs(f, "line_type"),
              ok="Request sent to the owner. You will see it here once decided.", default=f"/billing/bill/{bid}")


@router.post("/approval/{aid}/complete")
def complete(request: Request, aid: int, f=Depends(formdata), user: db.Actor = Depends(ASKERS)):
    return do(request, B.complete_approved, user, aid, fs(f, "method"), ok="Done. The approved action has been recorded.",
              nxt=fs(f, "next"), default="/billing")


@router.get("/receipt/{pid}")
def receipt(request: Request, pid: int, user: db.Actor = Depends(auth.require("ACCOUNTANT", "RECEPTIONIST", "OWNER"))):
    return render(request, "billing/receipt.html", user, p=B.receipt(user, pid))


# ---------------------------------------------------------------- discharge
@router.get("/discharge")
def discharge_list(request: Request, user: db.Actor = Depends(DISCHARGERS)):
    return render(request, "billing/discharge_list.html", user, rows=B.open_visits_for_discharge(user))


@router.get("/discharge/{vid}")
def discharge_page(request: Request, vid: int, user: db.Actor = Depends(DISCHARGERS)):
    c = B.checklist(user, vid) if not db.STUB else {}
    return render(request, "billing/discharge.html", user, c=c, vid=vid, refresh=0)


@router.post("/discharge/{vid}/partial")
def partial(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DISCHARGERS)):
    c = B.checklist(user, vid)
    return do(request, B.request_partial, user, vid, c.get("v", {}).get("bill_id"), fd(f, "deferred_amount"), fs(f, "promised_by_date"),
              fs(f, "reason"), ok="Approval requested. The owner can approve from a phone.", default=f"/billing/discharge/{vid}")


@router.post("/discharge/{vid}/go")
def go(request: Request, vid: int, f=Depends(formdata), user: db.Actor = Depends(DISCHARGERS)):
    try:
        B.discharge(user, vid, fs(f, "promised_by_date"))
    except db.RuleError as e:
        flash(request, str(e), "err")
        return redirect(f"/billing/discharge/{vid}")
    flash(request, "Patient discharged. Visit, stay and bill are closed.")
    return redirect("/billing/discharge")


# ---------------------------------------------------------------- dues
@router.get("/dues")
def dues(request: Request, user: db.Actor = Depends(auth.require("ACCOUNTANT", "RECEPTIONIST", "OWNER"))):
    return render(request, "billing/dues.html", user, rows=B.dues(user))


@router.post("/dues/{bid}/remind")
def remind(request: Request, bid: int, f=Depends(formdata), user: db.Actor = Depends(ACC_REC)):
    return do(request, B.send_reminder, user, bid, ok="Reminder queued (simulated SMS).", default="/billing/dues")


# ---------------------------------------------------------------- cash handover (accountant counts)
@router.get("/cash")
def cash(request: Request, user: db.Actor = Depends(ACC)):
    return render(request, "billing/cash.html", user, rows=PH.open_declarations(user))


@router.post("/cash/{did}")
def cash_count(request: Request, did: int, f=Depends(formdata), user: db.Actor = Depends(ACC)):
    return do(request, PH.count_cash, user, did, fd(f, "counted_amount"), fs(f, "note"),
              ok="Counted. Any difference is flagged to the owner.", default="/billing/cash")
