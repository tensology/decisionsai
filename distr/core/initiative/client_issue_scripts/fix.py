def main(P):
    """SCRATCH ONLY. Apply the plan P['plan'] with change reasons, let the app recalculate the
    invoices, and render each corrected invoice to a PDF."""
    import os
    from decimal import Decimal, ROUND_HALF_UP
    from main.models import Invoice, InvoiceLineItem, Project
    from main import views
    reason = P["reason"][:100]
    plan, out = P["plan"], {"projects": [], "lines": [], "invoices": []}
    for u in plan["project_updates"]:
        p = Project.objects.get(pk=u["project_id"])
        before = p.hourly_rate
        p.hourly_rate = Decimal(u["to"])
        p._change_reason = reason
        p.save()
        out["projects"].append({"id": p.id, "name": p.name, "from": str(before), "to": str(p.hourly_rate)})
    touched = set()
    for u in plan["line_updates"]:
        l = InvoiceLineItem.objects.get(pk=u["line_id"])
        before = (l.unit_cost, l.amount)
        l.unit_cost = Decimal(u["to"])
        l.amount = (l.quantity * l.unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        l._change_reason = reason
        l.save()
        touched.add(l.invoice_id)
        out["lines"].append({"id": l.id, "invoice": u["invoice"], "name": l.name, "unit_from": str(before[0]),
                             "unit_to": str(l.unit_cost), "amount_from": str(before[1]), "amount_to": str(l.amount)})
    os.makedirs(P["pdf_dir"], exist_ok=True)
    for inv in Invoice.objects.filter(pk__in=touched):
        before = {"subtotal": str(inv.subtotal), "tax": str(inv.tax_amount), "total": str(inv.total_amount)}
        inv._change_reason = reason
        inv.save()
        inv.refresh_from_db()
        path = os.path.join(P["pdf_dir"], inv.invoice_number + "-corrected.pdf")
        try:
            data = views.render_invoice_pdf_bytes(inv)
            with open(path, "wb") as fh:
                fh.write(data)
            pdf = {"path": path, "bytes": len(data)}
        except Exception as exc:  # noqa: BLE001
            pdf = {"error": type(exc).__name__ + ": " + str(exc)[:160]}
        out["invoices"].append({"number": inv.invoice_number, "before": before,
                                "after": {"subtotal": str(inv.subtotal), "tax": str(inv.tax_amount), "total": str(inv.total_amount)},
                                "is_sent": inv.is_sent, "pdf": pdf})
    return out
