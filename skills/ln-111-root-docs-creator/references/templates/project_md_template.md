# {{PROJECT_NAME}}

{{PROJECT_DESCRIPTION}}

<!-- SCOPE: Entry point with project overview and navigation ONLY. Contains project summary, documentation rules, and links to detailed docs. -->
<!-- DO NOT add here: business logic → architecture.md + ADRs, principles → principles.md, API specs → api_spec.md, patterns → guides/ -->

## ⚠️ Critical Rules for AI Agents

**Read this table BEFORE starting any work.**

| Category | Rule | When to Apply | Rationale |
|----------|------|---------------|-----------|
| **Standards Hierarchy** | Industry Standards → Security → Principles | All work | ISO/IEC/IEEE, RFC, OWASP override YAGNI/KISS |
| **Documentation** | Read README before folder work | Before creating/editing files | Understand structure and conventions |
| **Documentation Navigation** | Read SCOPE tag first in each document | Before reading any doc | DAG structure - understand boundaries |
| **Testing** | Read tests/README.md before tests | Before test work | Story-Level Test Task Pattern |
| **Research** | Use research tools per docs/tools_config.md | Before code changes | Official docs prevent reinventing wheel |
| **Task Management** | Use task provider per docs/tools_config.md | All task operations | See docs/tasks/README.md |
| **Skills** | Use built-in skills proactively | Documentation/Planning/Execution | Skills automate workflows |
| **Language** | English for all content, Russian for chat | All project content | Code/docs/tasks/commits in English only |

**Key Principles:**
- **Standards First**: Industry standards (ISO, RFC, OWASP, WCAG 2.1 AA) override development principles
- **Token Efficiency**: Progressive Disclosure Pattern (tables > paragraphs), no duplication
- **Quality**: Risk-Based Testing (Priority ≥15, Usefulness Criteria)
- **No Legacy Code**: Remove backward compatibility shims immediately

---

## Documentation Navigation Rules

**Graph Structure:** All documentation organized as Directed Acyclic Graph (DAG) with PROJECT.md as entry point.

**Reading Order:**
1. **Read SCOPE first** - Every document starts with `> **SCOPE:**` tag defining its boundaries
2. **Follow links down** - Navigate from parent to child documents through links
3. **Respect boundaries** - SCOPE tells you what IS and what IS NOT in each document

**Example Navigation:**
```
PROJECT.md (SCOPE: Entry point, project overview)
  → Read SCOPE: "Contains project summary, NOT implementation details"
  → Need principles? Follow link → docs/principles.md
    → Read SCOPE: "Contains development principles, NOT architecture"
    → Need architecture? Follow link → docs/Architecture.md
```

---

## Documentation

Project documentation: [docs/README.md](docs/README.md)

Documentation standards: [docs/documentation_standards.md](docs/documentation_standards.md)

Development principles: [docs/principles.md](docs/principles.md)

Tools configuration: [docs/tools_config.md](docs/tools_config.md)

## Development Commands

| Task | Windows | Bash |
|------|---------|------|
| **Install Dependencies** | `[Add your command]` | `[Add your command]` |
| **Run Tests** | `[Add your command]` | `[Add your command]` |
| **Start Dev Server** | `[Add your command]` | `[Add your command]` |
| **Build** | `[Add your command]` | `[Add your command]` |
| **Lint/Format** | `[Add your command]` | `[Add your command]` |

> [!NOTE]

> Update this table with project-specific commands during project setup

---

## Documentation Maintenance Rules

### For AI Agents (Claude Code)

**Principles:**
1. **Single Source of Truth:** Each fact exists in ONE place only - link, don't duplicate
2. **Graph Structure:** All docs reachable from `PROJECT.md` → `docs/README.md` (DAG, no cycles)
3. **SCOPE Tag Required:** Every document MUST start with `> **SCOPE:**` tag defining boundaries (what IS and what IS NOT in document)
4. **DRY (Don't Repeat Yourself):** Reference existing docs instead of copying content
5. **Update Immediately:** When code changes, update related docs while context is fresh
6. **Context-Optimized:** Keep `PROJECT.md` concise (≤100 lines recommended) - detailed info in `docs/`
7. **English Only:** ALL project content (code, comments, documentation, tasks, commit messages, variable names) MUST be in English

For document responsibilities and scope, see [docs/README.md](docs/README.md).

**Avoiding Duplication:**

**BAD:**
- Same architecture description in 3 files
- Development commands duplicated in multiple docs
- Full specs repeated across multiple guides

**GOOD:**
- `PROJECT.md`: "See [docs/project/architecture.md](docs/project/architecture.md) for component structure (C4 diagrams)"
- Guides reference each other: "See [guide_name.md](./guide_name.md)"
- One canonical source per concept with links from other docs

**Best Practices (Claude Code 2025):**
- Use subagents for complex doc updates to avoid context pollution
- Update docs immediately after feature completion (context is fresh)
- Use `/clear` after big doc refactors to start fresh
- Keep `PROJECT.md` as the always-loaded entry point with links to detailed docs

---

## Maintenance

**Update Triggers:**
- When changing project navigation (new/renamed docs)
- When updating critical rules for agents
- When modifying development commands
- When adding/removing key principles
- When documentation structure changes

**Verification:**
- [ ] All links resolve to existing files
- [ ] SCOPE tag clearly defines document boundaries
- [ ] Critical rules align with project requirements
- [ ] Command examples match actual project setup
- [ ] No duplicated content across documents
- [ ] Documentation Standards link correct

---

**Last Updated:** {{DATE}}
