# Pending Features & Investigations — Needs Assessment

> Consolidated from tickets/ on 2026-05-08. Review each item: if done, delete it from here. If still open, create a proper ticket in your tracker.

---

## P1 — Critical / Hotfixes

### First Day Voucher Minimum Spend
**Assigned:** Paul (Crystal Logic) | **Status:** In Progress / Possibly Done
- Duplicate `min_order_amount` field removed, consolidated to single minimum field
- Still needs: test voucher redemption + confirm production deploy
- **Action:** Verify fix is live on production. If confirmed, remove this entry.

---

## P2 — High Priority

### Account Number Syncing (Missing ~10% of profiles)
**Assigned:** Roland (investigate) + Carmen (provide examples) | **Status:** Pending Examples
- ~10% of customers missing account numbers after purchase; some show "Web01" correctly
- Likely a D3/MP Store file sync issue
- **Action:** Carmen to provide email examples → Roland to investigate data flow

---

## P3 — Medium Priority

### Personal to Business Account Conversion
**Assigned:** Paul (Crystal Logic) | **Status:** Not Started
- Currently shows "contact sales" when personal account users try to apply for business perks
- Needs: application form → back-office approval workflow → triggers same email as new business approval
- Temp number = P number (personal perks number)

### Gift Voucher as Purchasable Product
**Assigned:** Merrypak (product setup) + Paul (purchase flow) | **Status:** Open
- Dedicated vouchers category, denomination SKUs, email delivery on purchase
- Stock: use fictitious high qty (e.g. 1000), ignore during stock updates, hide qty display
- Dev catalog area exists for testing

---

## P4 — Low / Future / Concept

### D3 → Website Communication Setup
**Assigned:** Roland (D3 push) + Paul (website endpoints) | **Status:** On Hold
- When Carmen converts inquiry to order in D3, website should reflect the update
- Reference number updates, duplicate notification prevention
- Full test system available (Carmen has been working with it)
- **Note:** Ticket #10 is a duplicate of ROLAND/d3_website_communication.md — same scope

### Work Orders and Web Quotes Processing (D3)
**Assigned:** Roland | **Status:** Awaiting Caleb's Approval
- Routines ready to convert website work orders/web quotes to D3 sales orders or invoices
- Switch requires Caleb's explicit approval before activation

### Personal-to-Business Route Sheet / Delivery Optimization
**Assigned:** TBD | **Status:** Concept Only — Not Approved
- Dispatch map view, driver assignment, QR-coded route sheets, real-time tracking
- Depends on mobile app concept; previous route optimization attempts failed due to real-world complexity
- No development until explicitly approved

### Mobile Application
**Assigned:** TBD | **Status:** Concept Only — Not Approved
- Real-time tracking, push notifications, driver/customer app
- Business customers largely prefer email; adoption uncertainty
- No development until strategic review and explicit approval

---

## Packing — Split-Dispatch (Partially Implemented)

The packing AI and checkout UI are fully built. What's NOT built yet is the dispatch-level stock model that would make split shipping actually work end-to-end.

**Pending (from packing/task-list.md):**
- [ ] Add dispatch-level entity for multi-shipment orders (dispatch number, included lines, courier cost)
- [ ] Build stock-aware dispatch payload — current implementation packs full order, not in-stock-only
- [ ] Exclude out-of-stock lines from dispatch packing list and waybill payload
- [ ] Implement instruction merge order: hard rules → variant → global → AI advisory (partially done)
- [ ] Add order timeline events for partial shipment decisions
- [ ] Full QA: full stock / partial stock / blacklisted area / outside radius scenarios
- [ ] Define SLA for delayed line-item dispatch

**Pending (checkout modal UX — from frontend-checkout-ai-task-list.md):**
- [ ] Switch courier → collect → courier resets state correctly
- [ ] Mobile viewport modal and options usability checks
- [ ] Verify popup copy exactly matches Caleb/Julie wording
- [ ] Verify progress indicator visibly changes during 30–60s latency windows
- [ ] Refresh mid-calculation restores progress and final result correctly
- [ ] Cancel button stops active task and leaves checkout editable

---

## Source Files (deleted after this consolidation)
- `tickets/KIRAN/account_number_syncing.md`
- `tickets/PHASE2/mobile_application_concept.md`
- `tickets/PHASE2/personal_to_business_conversion.md`
- `tickets/PHASE2/route_sheet_delivery_optimization.md`
- `tickets/ROLAND/d3_website_communication.md`
- `tickets/ROLAND/work_orders_web_quotes.md`
- `tickets/tasks/P3_TICKET_06_gift_voucher_as_purchasable_product.md`
- `tickets/tasks/P4_TICKET_10_d3_to_website_communication_setup.md`
- `tickets/first_day_voucher_minimum_spend.md`
- `tickets/missing_tickets.md` (401 lines — full backlog list, absorbed above)
- `tickets/scripts/create_tickets.py` (utility script — not needed)
