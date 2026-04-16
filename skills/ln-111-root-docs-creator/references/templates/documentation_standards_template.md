# Documentation Standards

**Comprehensive Requirements for Claude Code Skills Documentation (2024-2026)**

**Last Updated:** {{DATE}}

<!-- SCOPE: 82 universal documentation requirements for Claude Code skills. Based on industry standards (ISO/IEC/IEEE, DIATAXIS, RFC), Claude Code best practices, and AI-friendly documentation research. NO project-specific details (→ project/requirements.md), NO skill-specific workflows (→ SKILL.md). -->

---

## Quick Reference (82 Requirements)

**Legend:** 🔴 Critical | 🟡 Important | 🟢 Desired | ⚠️ Conditional | ✅ Already implemented

| Category | Count | 🔴 | 🟡 | 🟢 | ⚠️ | ✅ | Validator |
|----------|-------|-----|-----|-----|-----|-----|-----------|
| **Core Documentation** | 25 | 8 | 12 | 5 | 0 | 0 | ln-121, ln-122 |
| **Claude Code Integration** | 5 | 1 | 2 | 2 | 0 | 0 | ln-121 v2.1.0+ |
| **AI-Friendly Writing** | 6 | 0 | 5 | 1 | 0 | 0 | ln-121 warning |
| **Markdown Best Practices** | 6 | 0 | 4 | 2 | 0 | 0 | ln-121 v2.1.0+ |
| **Code Examples Quality** | 5 | 1 | 2 | 2 | 0 | 0 | Manual + CI |
| **DIATAXIS Framework** | 5 | 0 | 1 | 2 | 0 | 2 | Manual |
| **Project Files** | 6 | 1 | 3 | 2 | 0 | 0 | Manual |
| **Quality Checks** | 5 | 0 | 4 | 1 | 0 | 0 | markdownlint, Vale |
| **Front Matter (SSG)** | 3 | 0 | 0 | 2 | 1 | 0 | Conditional |
| **Visual Documentation** | 5 | 0 | 0 | 4 | 0 | 1 | Manual |
| **Conventional Commits** | 4 | 0 | 1 | 1 | 0 | 2 | commitlint |
| **Security & Compliance** | 4 | 1 | 3 | 0 | 0 | 0 | Manual |
| **Performance** | 3 | 0 | 1 | 2 | 0 | 0 | Manual |

**Total:** 82 requirements | 🔴 12 Critical | 🟡 38 Important | 🟢 24 Desired | ⚠️ 1 Conditional | ✅ 5 Implemented

---

## Key Requirements by Priority

### Critical (Must Have)

| Requirement | Rationale | Validator |
|------------|-----------|-----------|
| PROJECT.md ≤100 lines | Claude Code performance optimization | ln-121 v2.1.0+ |
| All code examples runnable | Prevent documentation drift | Manual + CI |
| LICENSE file exists | Legal compliance | Manual |
| Never commit secrets | Security breach prevention | Manual |

### Important (Should Have)

**Claude Code Integration:**
- @-sourcing support in PROJECT.md (DRY pattern)
- Explicitly specify `setting_sources=["project"]`

**AI-Friendly Writing:**
- Use second person ("you" vs "users")
- Active voice instead of passive
- Short sentences (max 25 words)
- Prohibited phrases ("please note", "simply", "just", "easily")
- Don't assume prior knowledge

**Markdown Best Practices:**
- Header depth ≤ h3 (rarely h4)
- Descriptive links (not "click here")
- Callouts/Admonitions for important info
- Files end with single blank line (POSIX)

**Code Examples Quality:**
- Test documentation examples in CI/CD
- Include setup context (directory, prerequisites)

**Project Files:**
- CONTRIBUTING.md (contribution process)
- SECURITY.md (vulnerability reporting)
- .gitignore for docs (exclude generated files)

**Quality Checks:**
- markdownlint-cli2 (.markdownlint.jsonc)
- Vale.sh (.vale.ini for editorial checks)
- Build verification (prevent broken deployments)
- Link checking (dead link detection)

**Security & Compliance:**
- GitHub Secrets for CI/CD
- .env.example instead of .env
- Vulnerability reporting process (SECURITY.md)

**Performance:**
- Optimize PROJECT.md size (-30 to -40% tokens via @-sourcing)

### Desired (Nice to Have)

**Documentation Structure:** DIATAXIS framework (Tutorial/How-to/Reference/Explanation sections), How-to guides ✅, Reference docs ✅

**Visual Elements:** Mermaid diagrams ✅, workflow diagrams, sequence diagrams, light/dark theme support, centralized image storage

**Version Control:** Conventional Commits format, auto-generate CHANGELOG, Keep a Changelog ✅, Semantic versioning ✅

**Code Quality:** Realistic variable names (not foo/bar), show expected output, code blocks in step lists

**Project Files:** CODE_OF_CONDUCT.md, README badges, vocabulary files for terminology

**Advanced Features:** SessionStart hooks, subagents in .claude/agents/*.md, Front Matter for SSG (Hugo/Docusaurus) ⚠️, lazy loading, caching strategy

**Writing Style:** Avoid first-person pronouns, Title case for h1/Sentence case for h2+

---

## Standards Compliance

| Standard | Reference |
|----------|-----------|
| **ISO/IEC/IEEE 29148:2018** | Requirements Engineering |
| **ISO/IEC/IEEE 42010:2022** | Architecture Description |
| **DIATAXIS Framework** | diataxis.fr - Documentation structure |
| **RFC 2119, WCAG 2.1 AA** | Requirement keywords, Accessibility |
| **OWASP Top 10** | Security requirements |
| **Conventional Commits** | conventionalcommits.org |
| **Keep a Changelog** | Changelog format |
| **Semantic Versioning** | Major.Minor.Patch |

**Sources:** Claude Code docs, Clever Cloud guide, DIATAXIS framework, Matter style guide

---

## Verification Checklist

Before submitting documentation:

- [ ] **PROJECT.md ≤100 lines** - Concise and focused
- [ ] **All code examples runnable** - No placeholders, tested
- [ ] **LICENSE file exists** - Legal compliance
- [ ] **No secrets committed** - API keys in .env only
- [ ] **Header depth ≤ h3, files end with blank line** - Markdown standards
- [ ] **Active voice, second person, short sentences** - AI-friendly writing
- [ ] **SCOPE tag in docs/**, Maintenance section** - Core requirements
- [ ] **Descriptive links, callouts for important info** - Best practices

---

## Maintenance

**Update Triggers:**
- When Claude Code releases new best practices
- When industry standards evolve (ISO/IEC/IEEE updates)
- When new validation tools become available
- When ln-121-structure-validator or ln-122-content-updater add new checks
- Annual review (Q1 each year)

**Verification:**
- [ ] All 82 requirements documented with rationale
- [ ] Priority levels assigned (Critical/Important/Desired)
- [ ] Validators identified for automated checks
- [ ] Standards compliance table complete
- [ ] References link to authoritative sources
- [ ] Verification checklist covers all critical requirements

**Last Updated:** {{DATE}}

---

**Template Version:** 2.0.0 (MAJOR: Progressive Disclosure - reduced from 390→160 lines (-59%), removed detailed sections 1-12 and Implementation Roadmap, converted to compact table format, added SCOPE tag)
**Template Last Updated:** {{DATE}}
