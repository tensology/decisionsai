# Presentation Creator - Detailed Phase Workflows

Reference file for ln-115-presentation-creator Phase 3 and Phase 6 detailed processes.

---

## Phase 3: Validate Source Content Quality

**Objective**: Verify source docs contain sufficient content for presentation generation. Warn about incomplete content but don't block execution.

### 3.1 Check for Mermaid diagrams

**Required diagrams:**
- `architecture.md` MUST have at least 1 Mermaid diagram (preferably C4 Context)
- `database_schema.md` (if exists) MUST have ER diagram

For each file:
1. Read file content
2. Search for Mermaid code blocks: ` ```mermaid`
3. Count diagrams

**If diagrams missing:**
```
⚠ WARN: Missing diagrams in architecture.md
  Expected: At least 1 Mermaid diagram (C4 Context, Container, or Component)
  Found: 0 diagrams
```

### 3.2 Check for placeholders

**Placeholder patterns to detect:**
- `{{PLACEHOLDER}}` (template placeholder)
- `[Add your ...]` (instruction placeholder)
- `TODO:` (incomplete content marker)

**If placeholders found:**
```
⚠ WARN: Source docs contain placeholders:
  - requirements.md:42 - {{PROJECT_DESCRIPTION}}
  - tech_stack.md:15 - [Add your technology rationale]
```

### 3.3 Check content length

**Minimum expected lengths:**
- `requirements.md` > 500 words
- `architecture.md` > 1000 words
- `tech_stack.md` > 200 words

### 3.4 Auto-fix note

**None** - ln-115 is **read-only** for source docs:
- ❌ Does NOT edit markdown documentation
- ✅ Only READS and TRANSFORMS to HTML

**If issues found:** Run ln-111-project-docs-creator to complete documentation.

### 3.5 Report content quality summary

Log summary with diagrams count, placeholders found, content lengths. Warnings only, does not block execution.

---

## Phase 6: Content Injection & Example Cleanup

**Objective**: Parse MD documentation, inject into HTML templates, and remove example blocks.

### 6.1 Read and Parse MD Documents

Read and parse documentation files (from Phase 1):
1. **requirements.md**: Project name, tagline, business goal, FRs, constraints, success criteria
2. **architecture.md**: C4 diagrams, solution strategy, quality attributes
3. **tech_stack.md**: Technology table with versions, Docker config
4. **api_spec.md** (optional): API endpoints, authentication, error codes
5. **database_schema.md** (optional): ER diagrams, data dictionary
6. **design_guidelines.md** (optional): Typography, colors, components
7. **runbook.md** (optional): Setup, deployment, troubleshooting
8. **adrs/*.md**: ADR files (title, status, category)
9. **kanban_board.md** (optional): Epic Story Counters for Roadmap
10. **guides/*.md** (optional): How-to guides

### 6.2 Inject Content - Placeholder Reference

**Overview Tab** (`tab_overview.html`):
- `{{PROJECT_SUMMARY}}` — Problem/Solution/Business Value (3 sections)
- `{{TECH_STACK}}` — Technology badges (brief list)
- `{{STAKEHOLDERS}}` — Stakeholder cards with names and roles
- `{{QUICK_FACTS}}` — Project Status, Total Epics, Deployment Model, Platforms
- `{{NAVIGATION_GUIDE}}` — Documentation guide with tab descriptions

**Requirements Tab** (`tab_requirements.html`):
- `{{FUNCTIONAL_REQUIREMENTS}}` — FRs table (ID, Priority, Requirement, AC)
- `{{ADR_STRATEGIC}}` — Strategic ADRs grouped with accordion
- `{{ADR_TECHNICAL}}` — Technical ADRs grouped with accordion
- `{{SUCCESS_CRITERIA}}` — Project success metrics
- **NFRs are banned**: Drop any NFR content found

**Architecture Tab** (`tab_architecture.html`):
- `{{C4_CONTEXT}}` — System Context diagram (C4 Level 1)
- `{{C4_CONTAINER}}` — Container diagram (C4 Level 2)
- `{{C4_COMPONENT}}` — Component diagram (C4 Level 3)
- `{{DEPLOYMENT_DIAGRAM}}` — Infrastructure deployment diagram
- `{{ARCHITECTURE_NOTES}}` — Key architecture highlights table

**Technical Spec Tab** (`tab_technical_spec.html`):
- `{{TECH_STACK_TABLE}}` — Full technology stack with versions and purposes
- `{{API_ENDPOINTS}}` — API endpoints tables
- `{{DATA_MODELS}}` — ER diagram + Data dictionary table
- `{{TESTING_STRATEGY}}` — Risk-Based Testing approach

**Roadmap Tab** (`tab_roadmap.html`):
- `{{EPIC_CARDS}}` — Epic cards with Scope, Dependencies, Progress
- `{{OUT_OF_SCOPE_ITEMS}}` — Out-of-scope items with reasons
- `{{ROADMAP_LEGEND}}` — Status badges explanation

**Guides Tab** (`tab_guides.html`):
- `{{GETTING_STARTED}}` — Prerequisites, Installation, Verification
- `{{HOW_TO_GUIDES}}` — Step-by-step guides (from guides/*.md)
- `{{BEST_PRACTICES}}` — Code style, Testing, Design patterns
- `{{TROUBLESHOOTING}}` — Common problems and solutions
- `{{CONTRIBUTING}}` — Contributing guidelines
- `{{EXTERNAL_RESOURCES}}` — External documentation links

**Replacement Logic:**
- Use Edit tool to replace `{{PLACEHOLDER}}` → actual content
- For lists: generate HTML dynamically (loop through ADRs, create `<details>`)
- For Kanban: parse kanban_board.md → calculate progress % → generate Epic cards
- Preserve SCOPE tags in tab files
- Escape special characters (XSS prevention)
- Generate valid Mermaid syntax

### 6.3 Delete Example Blocks

**CRITICAL**: Remove all example content blocks for project-specific presentation.

**Process:**
1. Search for markers: `<!-- EXAMPLE START -->` and `<!-- EXAMPLE END -->`
2. Delete everything between markers (inclusive) using Edit tool
3. Do this for ALL 6 tab files

**Validation:**
- ✅ NO `<!-- EXAMPLE START -->` markers remain
- ✅ NO `<!-- EXAMPLE END -->` markers remain
- ✅ NO hardcoded e-commerce examples (John Smith, React 18 badges, Stripe, etc.)
- ✅ Only actual project data visible

**Example transformation:**

Before (dual-content template):
```html
<!-- PLACEHOLDER: {{PROJECT_SUMMARY}} -->
<!-- EXAMPLE START: Remove this block after replacing placeholder -->
<div class="project-summary">
    <p>Traditional e-commerce platforms struggle...</p>
</div>
<!-- EXAMPLE END -->
```

After Step 6.2 (placeholder replaced):
```html
<div class="project-summary">
    <p>Our healthcare system needs unified patient records...</p>
</div>
<!-- EXAMPLE START -->
<div class="project-summary">
    <p>Traditional e-commerce platforms struggle...</p>
</div>
<!-- EXAMPLE END -->
```

After Step 6.3 (examples deleted):
```html
<div class="project-summary">
    <p>Our healthcare system needs unified patient records...</p>
</div>
```

**Output**: Clean, project-specific tab files ready for build

---

**Version:** 1.0.0
**Last Updated:** 2025-12-12
