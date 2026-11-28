class _Abort(Exception):
    pass


def main(P):
    """APPROVED LIVE WRITE. Runs only from ``Runner.approved_script`` after Paul accepted this exact
    proposal. Everything happens in ONE transaction: lock the rows, check the current values are the
    ones the plan expected (else change nothing), back the rows up, apply, re-read and check, render
    the corrected PDFs, log the rates. Any failure rolls the transaction back."""
    import hashlib, json, logging, os, time
    from decimal import Decimal, ROUND_HALF_UP
    from django.db import transaction
    from main.models import Invoice, InvoiceLineItem, Project
    from main import views
    from main.billing_rates import record_invoice_rates, resolve_project_rate, MissingBillingRate

    plan, pid = P["plan"], int(P["proposal_id"])
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()[:16]
    if digest != P["plan_hash"] or P.get("confirm") != "APPLY %d %s" % (pid, digest):
        return {"ok": False, "stage": "confirm", "written": False, "problems": ["plan or confirmation does not match the approved proposal"]}
    reason = ("client-issue proposal #%d: %s" % (pid, P["reason"]))[:100]
    D = lambda v: Decimal(str(v)).quantize(Decimal("0.01"))  # noqa: E731
    cents = Decimal("0.02")
    problems, written = [], False
    out = {"projects": [], "lines": [], "invoices": []}
    try:
        with transaction.atomic():
            line_ids = [u["line_id"] for u in plan["line_updates"]]
            lines = {l.id: l for l in InvoiceLineItem.objects.select_for_update().select_related("invoice", "invoice__customer").filter(pk__in=line_ids)}
            projects = {p.id: p for p in Project.objects.select_for_update().filter(pk__in=[u["project_id"] for u in plan["project_updates"]])}
            invoice_ids = sorted({l.invoice_id for l in lines.values()})
            invoices = {i.id: i for i in Invoice.objects.select_for_update().select_related("customer").filter(pk__in=invoice_ids)}
            # ---- pre-checks: the world must still be exactly what the proposal described
            for u in plan["line_updates"]:
                l = lines.get(u["line_id"])
                if l is None:
                    problems.append("line %s no longer exists" % u["line_id"]); continue
                if l.invoice.invoice_number != u["invoice"]:
                    problems.append("line %s now belongs to %s, not %s" % (l.id, l.invoice.invoice_number, u["invoice"]))
                if l.unit_cost is None or D(l.unit_cost) != D(u["from"]):
                    problems.append("line %s rate is now %s, the proposal expected %s" % (l.id, l.unit_cost, u["from"]))
                if D(l.amount) != D(u["amount_from"]):
                    problems.append("line %s amount is now %s, the proposal expected %s" % (l.id, l.amount, u["amount_from"]))
                if not l.quantity or D(l.quantity) <= 0:
                    problems.append("line %s has no quantity" % l.id)
                cust = l.invoice.customer
                if D(u["to"]) <= 0:
                    problems.append("line %s target rate must be positive" % l.id)
                if D(u["to"]) == Decimal("850.00") and (getattr(cust, "hourly_rate", None) is None or D(cust.hourly_rate) != Decimal("850.00")):
                    problems.append("line %s: R850 is Tensology's own rate and is not this client's agreed rate" % l.id)
            for u in plan["project_updates"]:
                p = projects.get(u["project_id"])
                if p is None:
                    problems.append("project %s no longer exists" % u["project_id"]); continue
                if p.hourly_rate is None or D(p.hourly_rate) != D(u["from"]):
                    problems.append("project %s rate is now %s, the proposal expected %s" % (p.id, p.hourly_rate, u["from"]))
                if D(u["to"]) <= 0:
                    problems.append("project %s target rate must be positive" % p.id)
                if D(u["to"]) == Decimal("850.00"):
                    custs = {i.customer for i in invoices.values() if p in i.projects.all()}
                    if not custs or any(getattr(c, "hourly_rate", None) is None or D(c.hourly_rate) != Decimal("850.00") for c in custs):
                        problems.append("project %s: R850 is not the client's agreed rate" % p.id)
            if problems:
                raise _Abort("precheck")
            # ---- back up the affected rows (before any change)
            all_lines = list(InvoiceLineItem.objects.filter(invoice_id__in=invoice_ids).values())
            snapshot = {"proposal_id": pid, "plan_hash": digest, "taken_at": time.time(),
                        "projects": list(Project.objects.filter(pk__in=list(projects)).values("id", "name", "hourly_rate")),
                        "lines": all_lines,
                        "invoices": list(Invoice.objects.filter(pk__in=invoice_ids).values())}
            os.makedirs(P["backup_dir"], exist_ok=True)
            backup = os.path.join(P["backup_dir"], "client-issue-%d-%s-rows.json" % (pid, time.strftime("%Y%m%d-%H%M%S")))
            with open(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:
                json.dump(snapshot, fh, default=str)
            out["backup"] = backup
            before_lines = {l["id"]: l for l in all_lines}
            before_inv = {i["id"]: i for i in snapshot["invoices"]}
            # ---- apply
            written = True
            for u in plan["project_updates"]:
                p = projects[u["project_id"]]
                was = p.hourly_rate
                p.hourly_rate = D(u["to"]); p._change_reason = reason; p.save()
                out["projects"].append({"id": p.id, "name": p.name, "from": str(was), "to": str(p.hourly_rate)})
            touched = set()
            for u in plan["line_updates"]:
                l = lines[u["line_id"]]
                was = (l.unit_cost, l.amount)
                l.unit_cost = D(u["to"])
                l.amount = (l.quantity * l.unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                l._change_reason = reason; l.save()
                touched.add(l.invoice_id)
                out["lines"].append({"id": l.id, "invoice": u["invoice"], "name": l.name, "unit_from": str(was[0]), "unit_to": str(l.unit_cost),
                                     "amount_from": str(was[1]), "amount_to": str(l.amount)})
            for iid in sorted(touched):
                inv = invoices[iid]
                before = {"subtotal": str(inv.subtotal), "tax": str(inv.tax_amount), "total": str(inv.total_amount)}
                inv._change_reason = reason; inv.save(); inv.refresh_from_db()
                out["invoices"].append({"number": inv.invoice_number, "id": inv.id, "before": before,
                                        "after": {"subtotal": str(inv.subtotal), "tax": str(inv.tax_amount), "total": str(inv.total_amount)},
                                        "is_sent": inv.is_sent})
            # ---- verify inside the transaction
            planned = {u["line_id"]: u for u in plan["line_updates"]}
            for l in InvoiceLineItem.objects.filter(invoice_id__in=invoice_ids):
                b = before_lines[l.id]
                if l.id in planned:
                    want = (D(planned[l.id]["to"]) * D(l.quantity)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if D(l.unit_cost) != D(planned[l.id]["to"]) or abs(D(l.amount) - want) > Decimal("0.01"):
                        problems.append("line %s is not at the planned rate after the write" % l.id)
                elif D(l.unit_cost or 0) != D(b["unit_cost"] or 0) or D(l.amount) != D(b["amount"]):
                    problems.append("line %s was not part of the plan but changed" % l.id)
            for u in plan["project_updates"]:
                if D(Project.objects.get(pk=u["project_id"]).hourly_rate) != D(u["to"]):
                    problems.append("project %s is not at the planned rate after the write" % u["project_id"])
            for iid in touched:
                inv = Invoice.objects.get(pk=iid)
                sub = sum((D(l.amount) for l in inv.line_items.all()), Decimal("0"))
                tax = (sub * D(inv.tax_rate or 0) / 100).quantize(Decimal("0.01"))
                if abs(sub + tax - D(inv.discount or 0) - D(inv.total_amount)) > cents:
                    problems.append("%s total does not equal its lines plus tax" % inv.invoice_number)
                b = before_inv[iid]
                if inv.is_sent != b["is_sent"] or inv.status != b["status"] or str(inv.issue_date) != str(b["issue_date"]):
                    problems.append("%s status, sent flag or date changed" % inv.invoice_number)
            if problems:
                raise _Abort("verify")
            # ---- corrected documents
            try:
                os.makedirs(P["pdf_dir"], exist_ok=True)
            except OSError as exc:
                problems.append("cannot write the corrected documents: %s" % str(exc)[:100])
                raise _Abort("pdf")
            for rec in out["invoices"]:
                inv = Invoice.objects.get(pk=rec["id"])
                path = os.path.join(P["pdf_dir"], inv.invoice_number + "-corrected.pdf")
                try:
                    data = views.render_invoice_pdf_bytes(inv)
                    with open(path, "wb") as fh:
                        fh.write(data)
                    rec["pdf"] = {"path": path, "bytes": len(data)}
                except Exception as exc:  # noqa: BLE001
                    problems.append("%s PDF did not render: %s" % (inv.invoice_number, str(exc)[:120])); break
            if problems:
                raise _Abort("pdf")
            # ---- log: billing.rates log + the client's history
            log = logging.getLogger("billing.rates")
            for rec in out["invoices"]:
                inv = Invoice.objects.select_related("customer").get(pk=rec["id"])
                sources = {}
                for p in inv.projects.all():
                    try:
                        sources[p.name] = resolve_project_rate(p, inv.customer)
                    except MissingBillingRate:
                        continue
                if sources:
                    record_invoice_rates(inv, sources)
                log.info("client-issue proposal #%d changed %s: %s", pid, inv.invoice_number,
                         "; ".join("%s %s -> %s" % (l["name"], l["unit_from"], l["unit_to"]) for l in out["lines"] if l["invoice"] == inv.invoice_number))
    except _Abort as stage:
        return {"ok": False, "stage": str(stage), "written": False, "rolled_back": written, "problems": problems, "backup": out.get("backup")}
    out.update({"ok": True, "written": True, "plan_hash": digest})
    return out
