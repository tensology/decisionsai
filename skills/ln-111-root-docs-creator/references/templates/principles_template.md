# Development Principles

**Last Updated:** {{DATE}}

<!-- SCOPE: Universal development principles for THIS project ONLY. Contains 9 core principles with rationale, Decision Framework, and Verification Checklist. NO implementation details (→ Architecture.md), NO project-specific requirements (→ Requirements.md), NO testing philosophy (→ docs/reference/guides/testing-strategy.md), NO code examples (keep language-agnostic). -->

<!-- NO_CODE_EXAMPLES: Principles must be LANGUAGE-AGNOSTIC.
     FORBIDDEN: Code snippets in any language, framework-specific examples
     ALLOWED: Conceptual examples (✅/❌ format), decision tables, checklists
     Principles apply to ANY tech stack (Python, TypeScript, C#, etc.) -->

---

## Core Principles

| # | Name | Type | Principle | Approach/Rules |
|---|------|------|-----------|----------------|
| **1** | **Standards First** | code+docs | Industry standards (ISO/RFC/OWASP/WCAG) override development principles | **Hierarchy:** Industry Standards → Security Standards → Accessibility Standards → Dev Principles (YAGNI/KISS/DRY within standard boundaries) |
| **2** | **YAGNI (You Aren't Gonna Need It)** | code+docs | Don't build features "just in case". Build what's needed NOW | **Avoid:** Generic frameworks for one use case, caching without bottleneck, extensibility points without requirements |
| **3** | **KISS (Keep It Simple)** | code+docs | Simplest solution that solves the problem. No unnecessary complexity | **Approach:** Start with naive solution → Add complexity ONLY when proven necessary → Each abstraction layer must justify existence |
| **4** | **DRY (Don't Repeat Yourself)** | code+docs | Each piece of knowledge exists in ONE place. Link, don't duplicate | **Code:** Extract repeated logic, constants defined once. **Docs:** Single Source of Truth, reference via links, update immediately |
| **5** | **Consumer-First Design** | code | Design APIs/functions/workflows from consumer's perspective | **Design:** 1. Define interface/API FIRST (what consumers need) → 2. Implement internals SECOND (how it works) → 3. Never expose internal complexity to consumers. **Note:** This is for API/interface DESIGN, not task execution order (see Foundation-First Execution in workflow) |
| **6** | **No Legacy Code** | code | Remove backward compatibility shims immediately after migration | **Rules:** Deprecated features deleted in NEXT release (not "someday"), NO commented-out code (use git history), NO `if legacy_mode:` branches |
| **7** | **Documentation-as-Code** | docs | Documentation lives WITH code, updated WITH code changes | **Rules:** Documentation in same commit as code, NO separate "docs update" tasks, Outdated docs = bug (same severity as code bug) |
| **8** | **Security by Design** | code | Security integrated from design phase, not bolted on later | **Practices:** Never commit secrets → env vars/secret managers, Validate at boundaries → Pydantic models, Least Privilege → minimum permissions, Fail Securely → don't leak info in errors, Defense in Depth → multiple security layers |
| **9** | **Auto-Generated Migrations Only** | code | Never create DB migrations manually - use ORM auto-generation | **Reason:** Manual migrations cause schema drift between code models and database. Always use your ORM's migration generation feature |

---

## Decision-Making Framework

When making technical decisions, evaluate against these principles **in order**:

1. **Security:** Is it secure by design? (OWASP, NIST standards)
2. **Standards Compliance:** Does it follow industry standards? (ISO, RFC, W3C)
3. **Correctness:** Does it solve the problem correctly?
4. **Simplicity (KISS):** Is it the simplest solution that works?
5. **Necessity (YAGNI):** Do we actually need this now?
6. **Maintainability:** Can future developers understand and modify it?
7. **Performance:** Is it fast enough? (Optimize only if proven bottleneck)

### Trade-offs

When principles conflict, use the Decision-Making Framework hierarchy:

| Conflict | Lower Priority | Higher Priority | Resolution |
|----------|---------------|-----------------|------------|
| **Simplicity vs Security** | KISS | Security by Design | Choose secure solution, even if more complex |
| **YAGNI vs Standards** | YAGNI | Standards First | Implement standard now (e.g., OAuth 2.0), not "simple custom auth" |
| **Flexibility vs Constraints** | Flexibility | YAGNI | Choose constraints (clear boundaries), not open-ended "for future" |

---

## Anti-Patterns to Avoid

### God Objects
- ❌ One class/module that does everything
- ✅ Small, focused classes with single responsibility

### Premature Optimization
- ❌ Caching before measuring actual bottlenecks
- ✅ Measure first (profiling, metrics), optimize proven bottlenecks

### Over-Engineering
- ❌ Complex abstractions "for future flexibility"
- ✅ Simple solution now, refactor if complexity justified later

### Magic Numbers/Strings
- ❌ Hardcoded values scattered throughout codebase
- ✅ Named constants or enums defined in one place

### Leaky Abstractions
- ❌ Service layer exposes internal data structures to consumers
- ✅ Service layer returns consumer-facing data contracts, hides implementation details

---

## Verification Checklist

Before submitting code, verify compliance with principles:

- [ ] **Standards First:** Follows industry standards (ISO, RFC, OWASP, WCAG 2.1 AA)
- [ ] **YAGNI:** Only building what's needed now (no speculative features)
- [ ] **KISS:** Solution is as simple as possible, not simpler
- [ ] **DRY:** No duplicated logic or documentation
- [ ] **Consumer-First Design:** API/interface designed from consumer perspective
- [ ] **No Legacy Code:** No deprecated code, no commented-out code
- [ ] **Documentation-as-Code:** Docs updated in same commit as code
- [ ] **Security by Design:** No secrets committed, input validated, least privilege
- [ ] **Auto-Generated Migrations Only:** DB migrations created via ORM auto-generation, not manually

---

## Maintenance

**Update Triggers:**
- When adding new principles
- When changing decision framework hierarchy
- When industry standards evolve (ISO, RFC, OWASP updates)
- When trade-off examples change
- Annual review (Q1 each year)

**Verification:**
- [ ] All 9 principles documented
- [ ] Decision Framework clear (7 steps)
- [ ] Trade-offs explained (3 conflicts)
- [ ] Anti-patterns listed (5 patterns)
- [ ] Verification Checklist complete (9 items)
- [ ] Links to external resources valid
- [ ] Table format demonstrates principles clearly

**Last Updated:** {{DATE}}

---

**Template Version:** 3.2.0 (Added explicit NO_CODE_EXAMPLES tag for language-agnostic enforcement)
**Template Last Updated:** 2025-01-09
