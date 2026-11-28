def main(P):
    """Put back the rows saved by apply_live (the 'rows' backup) after a failed post-commit check.
    One transaction; verifies the restored values."""
    import hashlib, json
    from decimal import Decimal
    from django.db import transaction
    from main.models import Invoice, InvoiceLineItem, Project

    pid, digest = int(P["proposal_id"]), P["plan_hash"]
    if P.get("confirm") != "REVERT %d %s" % (pid, digest):
        return {"ok": False, "problems": ["confirmation does not match"]}
    snap = json.load(open(P["backup"]))
    if snap["proposal_id"] != pid or snap["plan_hash"] != digest:
        return {"ok": False, "problems": ["backup belongs to a different proposal"]}
    D = lambda v: Decimal(str(v)).quantize(Decimal("0.01"))  # noqa: E731
    reason = ("revert client-issue proposal #%d" % pid)[:100]
    with transaction.atomic():
        for p in snap["projects"]:
            o = Project.objects.select_for_update().get(pk=p["id"]); o.hourly_rate = D(p["hourly_rate"]) if p["hourly_rate"] is not None else None
            o._change_reason = reason; o.save()
        for l in snap["lines"]:
            o = InvoiceLineItem.objects.select_for_update().get(pk=l["id"])
            o.unit_cost = None if l["unit_cost"] is None else D(l["unit_cost"]); o.amount = D(l["amount"])
            o._change_reason = reason; o.save()
        for i in snap["invoices"]:
            o = Invoice.objects.select_for_update().get(pk=i["id"]); o._change_reason = reason; o.save()
        bad = []
        for i in snap["invoices"]:
            o = Invoice.objects.get(pk=i["id"])
            if D(o.total_amount) != D(i["total_amount"]):
                bad.append("%s total is %s, expected %s" % (o.invoice_number, o.total_amount, i["total_amount"]))
        for l in snap["lines"]:
            o = InvoiceLineItem.objects.get(pk=l["id"])
            if D(o.amount) != D(l["amount"]):
                bad.append("line %s amount %s, expected %s" % (l["id"], o.amount, l["amount"]))
        if bad:
            transaction.set_rollback(True)
            return {"ok": False, "problems": bad}
    return {"ok": True, "restored": {"projects": len(snap["projects"]), "lines": len(snap["lines"]), "invoices": len(snap["invoices"])}}
