def main(P):
    from collections import Counter
    from datetime import timedelta
    from main.models import Invoice, InvoiceLineItem

    def changes_of(history, field):
        out, prev = [], object()
        for h in history:
            v = getattr(h, field)
            v = None if v is None else str(v)
            if v != prev:
                out.append({"at": h.history_date.isoformat(), "value": v, "reason": h.history_change_reason or "", "type": h.history_type})
                prev = v
        return out

    res = []
    for num in P["invoices"]:
        inv = Invoice.objects.filter(invoice_number=num).select_related("customer").first()
        if inv is None:
            res.append({"number": num, "found": False})
            continue
        projects = []
        for p in inv.projects.all():
            projects.append({"id": p.id, "name": p.name,
                             "rate": None if p.hourly_rate is None else str(p.hourly_rate),
                             "rate_changes": changes_of(list(p.history.all().order_by("history_date")), "hourly_rate")[-6:]})
        lines = []
        for l in inv.line_items.all().order_by("id"):
            hist = list(l.history.all().order_by("history_date"))
            first = hist[0] if hist else None
            lines.append({"id": l.id, "name": l.name, "description": (l.description or "")[:90],
                          "unit": None if l.unit_cost is None else str(l.unit_cost),
                          "qty": None if l.quantity is None else str(l.quantity), "amount": str(l.amount),
                          "created_at": first.history_date.isoformat() if first else "",
                          "created_unit": None if not first or first.unit_cost is None else str(first.unit_cost),
                          "created_reason": (first.history_change_reason or "") if first else "",
                          "unit_changes": changes_of(hist, "unit_cost")[1:]})
        window = Invoice.objects.filter(customer=inv.customer, issue_date__gte=inv.issue_date - timedelta(days=45),
                                        issue_date__lte=inv.issue_date + timedelta(days=5))
        peer = Counter()
        for l in InvoiceLineItem.objects.filter(invoice__in=window).exclude(unit_cost__isnull=True):
            peer[(l.name.split(" [")[0], str(l.unit_cost))] += 1
        cust_rate = getattr(inv.customer, "hourly_rate", None)
        res.append({"number": num, "found": True, "id": inv.id, "customer": inv.customer.name,
                    "customer_id": inv.customer_id, "customer_rate": None if cust_rate is None else str(cust_rate),
                    "status": inv.status, "is_sent": inv.is_sent, "issue_date": inv.issue_date.isoformat(),
                    "subtotal": str(inv.subtotal), "tax_rate": str(inv.tax_rate or 0), "tax_amount": str(inv.tax_amount),
                    "discount": str(inv.discount), "total": str(inv.total_amount),
                    "projects": projects, "lines": lines,
                    "peer_rates": [{"name": k[0], "unit": k[1], "count": c} for k, c in sorted(peer.items())]})
    return {"invoices": res}
