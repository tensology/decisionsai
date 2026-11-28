def main(P):
    """SCRATCH ONLY. Put the data back as it stood at P['as_of'] (from simple_history), so the
    pipeline meets the same state the client complained about. Plain UPDATEs: no new history."""
    from django.utils.dateparse import parse_datetime
    from main.models import Invoice, InvoiceLineItem, Project
    at = parse_datetime(P["as_of"])
    done = {"projects": [], "lines": 0, "invoices": []}
    for num in P["invoices"]:
        inv = Invoice.objects.get(invoice_number=num)
        for p in inv.projects.all():
            h = p.history.filter(history_date__lte=at).order_by("-history_date").first()
            if h is not None and h.hourly_rate != p.hourly_rate:
                Project.objects.filter(pk=p.pk).update(hourly_rate=h.hourly_rate)
                done["projects"].append({"id": p.id, "name": p.name, "to": str(h.hourly_rate)})
        for l in inv.line_items.all():
            h = l.history.filter(history_date__lte=at).order_by("-history_date").first()
            if h is None:
                continue
            if (h.unit_cost, h.quantity, h.amount) != (l.unit_cost, l.quantity, l.amount):
                InvoiceLineItem.objects.filter(pk=l.pk).update(unit_cost=h.unit_cost, quantity=h.quantity, amount=h.amount)
                done["lines"] += 1
        h = inv.history.filter(history_date__lte=at).order_by("-history_date").first()
        if h is not None:
            Invoice.objects.filter(pk=inv.pk).update(subtotal=h.subtotal, tax_amount=h.tax_amount,
                                                    discount=h.discount, total_amount=h.total_amount)
            done["invoices"].append({"number": num, "total": str(h.total_amount)})
    return done
