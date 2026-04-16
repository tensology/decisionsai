# Root Documentation Questions (Q1-Q22)

<!-- SCOPE: Interactive questions for 4 root docs (PROJECT.md, docs/README.md, documentation_standards.md, principles.md) ONLY. -->
<!-- DO NOT add here: question logic → ln-111-root-docs-creator SKILL.md, other doc questions → questions_core.md, questions_backend.md -->

**Purpose:** Validation questions for 4 root documentation files.

**Format:** Document -> Rules -> Questions -> Validation Heuristics -> Auto-Discovery

---

## Table of Contents

| Document | Questions | Auto-Discovery | Priority | Line |
|----------|-----------|----------------|----------|------|
| [PROJECT.md](#claudemd) | 6 | Medium | Critical | L25 |
| [docs/README.md](#docsreadmemd) | 7 | Low | High | L95 |
| [docs/documentation_standards.md](#docsdocumentation_standardsmd) | 3 | None | Medium | L175 |
| [docs/principles.md](#docsprinciplesmd) | 6 | None | High | L215 |

---

<!-- DOCUMENT_START: PROJECT.md -->
## PROJECT.md

**File:** PROJECT.md (project root)
**Target Sections:** Critical Rules, Documentation Navigation, Documentation, Development Commands, Maintenance

**Rules:**
- Recommended length: <=100 lines
- Must have SCOPE tag in first 10 lines
- Must link to docs/README.md
- Entry point for AI agents (DAG root)

---

<!-- QUESTION_START: 1 -->
### Question 1: Where is project documentation located?

**Expected Answer:** Links to docs/README.md, documentation_standards.md, principles.md
**Target Section:** ## Documentation

**Validation Heuristics:**
- Has section "## Documentation" with links to docs/README.md, documentation_standards.md, principles.md

**Auto-Discovery:** None (standard structure)
<!-- QUESTION_END: 1 -->

---

<!-- QUESTION_START: 2 -->
### Question 2: What are critical rules for AI agents?

**Expected Answer:** Table of critical rules by category (Standards, Documentation, Testing, Research, Task Management)
**Target Section:** ## Critical Rules for AI Agents

**Validation Heuristics:**
- Has section "## Critical Rules for AI Agents" with table, 7+ rows, "Key Principles" subsection

**Auto-Discovery:** None (universal rules)
<!-- QUESTION_END: 2 -->

---

<!-- QUESTION_START: 3 -->
### Question 3: How to navigate documentation (DAG structure)?

**Expected Answer:** SCOPE tags explanation + reading order + graph structure
**Target Section:** ## Documentation Navigation Rules

**Validation Heuristics:**
- Has section with SCOPE tag explanation, reading order, >40 words

**Auto-Discovery:** None (universal best practice)
<!-- QUESTION_END: 3 -->

---

<!-- QUESTION_START: 4 -->
### Question 4: What are documentation maintenance rules?

**Expected Answer:** DRY principles, Single Source of Truth, English-only policy
**Target Section:** ## Documentation Maintenance Rules

**Validation Heuristics:**
- Has "Single Source of Truth"/"DRY", "English Only" rule, >60 words

**Auto-Discovery:** None (universal standards)
<!-- QUESTION_END: 4 -->

---

<!-- QUESTION_START: 5 -->
### Question 5: When should PROJECT.md be updated?

**Expected Answer:** Update triggers + verification checklist
**Target Section:** ## Maintenance

**Validation Heuristics:**
- Has "## Maintenance" with "Update Triggers" and "Verification" subsections, "Last Updated" field

**Auto-Discovery:** None (standard section)
<!-- QUESTION_END: 5 -->

---

<!-- QUESTION_START: 6 -->
### Question 6: What are the project development commands?

**Expected Answer:** Table with commands (Install, Test, Dev, Build, Lint) for Windows/Bash
**Target Section:** ## Development Commands

**Validation Heuristics:**
- Has "## Development Commands" with table (Task, Windows, Bash), 5+ rows

**Auto-Discovery:**
- Scan package.json -> "scripts"
- Scan pyproject.toml -> [project.scripts]
- Scan Makefile -> targets
<!-- QUESTION_END: 6 -->

---

**Overall File Validation:**
- Has SCOPE tag in first 10 lines
- Total length > 80 words
- File size ≤100 lines (Claude Code performance requirement)

<!-- DOCUMENT_END: PROJECT.md -->

---

<!-- DOCUMENT_START: docs/README.md -->
## docs/README.md

**File:** docs/README.md (documentation hub)
**Target Sections:** Overview, Standards, Writing Guidelines, Standards Compliance, Contributing, Quick Navigation, Maintenance

**Rules:**
- Must have SCOPE tag in first 10 lines
- Hub file - navigation to subdirectories
- General standards only - NO project-specific content

---

<!-- QUESTION_START: 7 -->
### Question 7: What is the documentation structure?

**Expected Answer:** Overview of documentation areas (Project, Reference, Task Management)
**Target Section:** ## Overview

**Validation Heuristics:**
- Has "## Overview" mentioning project/, reference/, tasks/, >30 words

**Auto-Discovery:** Scan docs/ for subdirectories
<!-- QUESTION_END: 7 -->

---

<!-- QUESTION_START: 8 -->
### Question 8: What are general documentation standards?

**Expected Answer:** SCOPE Tags, Maintenance Sections, Sequential Numbering, Placeholders
**Target Section:** ## General Documentation Standards

**Validation Heuristics:**
- Has subsections (SCOPE Tags, Maintenance, Numbering, Placeholders), >100 words

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 8 -->

---

<!-- QUESTION_START: 9 -->
### Question 9: What are writing guidelines?

**Expected Answer:** Progressive Disclosure Pattern, token efficiency, table-first format
**Target Section:** ## Writing Guidelines

**Validation Heuristics:**
- Mentions Progressive Disclosure/token efficiency, >50 words

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 9 -->

---

<!-- QUESTION_START: 10 -->
### Question 10: When should docs/README.md be updated?

**Expected Answer:** Update triggers + verification
**Target Section:** ## Maintenance

**Validation Heuristics:**
- Has "## Maintenance" with "Update Triggers" and "Verification"

**Auto-Discovery:** None (standard)
<!-- QUESTION_END: 10 -->

---

<!-- QUESTION_START: 11 -->
### Question 11: What standards does this documentation comply with?

**Expected Answer:** Standards table with ISO/IEC/IEEE, arc42, C4 Model, ADR Format
**Target Section:** ## Standards Compliance

**Validation Heuristics:**
- Has table with 5+ standards (ISO/IEC/IEEE, arc42, C4, ADR, MoSCoW)

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 11 -->

---

<!-- QUESTION_START: 12 -->
### Question 12: How to contribute to documentation?

**Expected Answer:** Numbered steps (SCOPE, Last Updated, registry, numbering, links)
**Target Section:** ## Contributing to Documentation

**Validation Heuristics:**
- Has 6+ steps mentioning SCOPE, Last Updated, >40 words

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 12 -->

---

<!-- QUESTION_START: 13 -->
### Question 13: How to quickly navigate to key areas?

**Expected Answer:** Quick Navigation table (Area, Key Documents, Skills)
**Target Section:** ## Quick Navigation

**Validation Heuristics:**
- Has table with 4 rows (Standards, Project, Reference, Tasks)

**Auto-Discovery:** Scan docs/ structure
<!-- QUESTION_END: 13 -->

---

**Overall File Validation:**
- Has SCOPE tag in first 10 lines
- Total length > 100 words

<!-- DOCUMENT_END: docs/README.md -->

---

<!-- DOCUMENT_START: docs/documentation_standards.md -->
## docs/documentation_standards.md

**File:** docs/documentation_standards.md (count defined in template SCOPE tag)
**Target Sections:** Quick Reference, 12 main sections, Maintenance

**Rules:**
- Must have SCOPE tag in first 10 lines
- Requirements count matches template's SCOPE tag and Quick Reference heading
- 12 main sections covering industry standards
- References to ISO/IEC/IEEE, DIATAXIS, arc42

---

<!-- QUESTION_START: 14 -->
### Question 14: What are the comprehensive documentation requirements?

**Expected Answer:** Quick Reference table with all requirements from template, organized in 12 categories
**Target Section:** ## Quick Reference

**Validation Heuristics:**
- Has table with row count matching template's "Total:" line

**Auto-Discovery:** None (use template as-is)
<!-- QUESTION_END: 14 -->

---

<!-- QUESTION_START: 15 -->
### Question 15: What are detailed requirements for each category?

**Expected Answer:** 12 main sections with detailed explanations
**Target Sections:** 12 sections (Claude Code Integration, AI-Friendly Writing, etc.)

**Validation Heuristics:**
- Has 12+ main sections, mentions ISO/IEC/IEEE/DIATAXIS/arc42, >300 lines

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 15 -->

---

<!-- QUESTION_START: 16 -->
### Question 16: When should documentation standards be updated?

**Expected Answer:** Update triggers + verification
**Target Section:** ## Maintenance

**Validation Heuristics:**
- Has "## Maintenance" with "Update Triggers" and "Verification"

**Auto-Discovery:** None (standard)
<!-- QUESTION_END: 16 -->

---

**Overall File Validation:**
- Has SCOPE tag in first 10 lines
- File size > 300 lines
- Mentions ISO/IEC/IEEE 29148:2018
- Mentions DIATAXIS framework
- Mentions arc42

<!-- DOCUMENT_END: docs/documentation_standards.md -->

---

<!-- DOCUMENT_START: docs/principles.md -->
## docs/principles.md

**File:** docs/principles.md (9 principles + Decision Framework)
**Target Sections:** Core Principles, Decision Framework, Trade-offs, Anti-Patterns, Verification, Maintenance

**Rules:**
- Must have SCOPE tag in first 10 lines
- 9 core principles (Standards First, YAGNI, KISS, DRY, Consumer-First, No Legacy, Docs-as-Code, Security, Auto-Generated Migrations Only)
- Decision-Making Framework (7 steps)
- Verification Checklist (9 items)
- NO code examples in Anti-Patterns section (keep language-agnostic)

---

<!-- QUESTION_START: 17 -->
### Question 17: What are the core development principles?

**Expected Answer:** 9 principles in table format
**Target Section:** ## Core Principles

**Validation Heuristics:**
- Has 9-row table: Standards First, YAGNI, KISS, DRY, Consumer-First, No Legacy, Docs-as-Code, Security, Auto-Generated Migrations Only

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 17 -->

---

<!-- QUESTION_START: 18 -->
### Question 18: How to make decisions when principles conflict?

**Expected Answer:** Decision Framework with priority order (Security -> Standards -> ...)
**Target Section:** ## Decision-Making Framework

**Validation Heuristics:**
- Has 7 steps (Security, Standards, Correctness, Simplicity, Necessity, Maintainability, Performance), >30 words

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 18 -->

---

<!-- QUESTION_START: 19 -->
### Question 19: How to resolve conflicts when principles contradict?

**Expected Answer:** Trade-offs table (Conflict, Lower Priority, Higher Priority, Resolution)
**Target Section:** ### Trade-offs

**Validation Heuristics:**
- Has table with 3+ conflicts

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 19 -->

---

<!-- QUESTION_START: 20 -->
### Question 20: What are common anti-patterns to avoid?

**Expected Answer:** List of anti-patterns across principles
**Target Section:** ## Anti-Patterns to Avoid

**Validation Heuristics:**
- Has 5+ anti-patterns, >20 words

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 20 -->

---

<!-- QUESTION_START: 21 -->
### Question 21: How to verify principles compliance?

**Expected Answer:** Verification checklist with 9 items
**Target Section:** ## Verification Checklist

**Validation Heuristics:**
- Has 9-item checklist (- [ ] format) covering all 9 principles

**Auto-Discovery:** None (universal)
<!-- QUESTION_END: 21 -->

---

<!-- QUESTION_START: 22 -->
### Question 22: When should principles be updated?

**Expected Answer:** Update triggers + verification
**Target Section:** ## Maintenance

**Validation Heuristics:**
- Has "## Maintenance" with "Update Triggers" and "Verification"

**Auto-Discovery:** None (standard)
<!-- QUESTION_END: 22 -->

---

**Overall File Validation:**
- Has SCOPE tag in first 10 lines
- File size > 100 lines
- All 9 core principles present
- NO code syntax in Anti-Patterns (no: if/else, ==, def, class, import)

<!-- DOCUMENT_END: docs/principles.md -->

---

**Total Questions:** 22
**Total Documents:** 4

---
**Version:** 1.0.0
**Last Updated:** 2025-12-19
