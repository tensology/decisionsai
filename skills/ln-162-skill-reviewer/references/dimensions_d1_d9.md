# Structural Review Dimensions (D1-D9)

<!-- DO NOT add here: Workflow phases -> ln-162-skill-reviewer SKILL.md -->

Check ALL dimensions across ALL skills in scope. Phase 2 failures are pre-verified -- include directly.

## D1: Flow Integrity
- Every `ln-NNN` reference in workflow/worker tables points to existing `ln-NNN-*/SKILL.md`
- No circular delegation loops (A -> B -> A)
- Every worker invocation has matching caller reference in target skill
- No dead-end flows (delegation to worker with no output/return path)
- Peer coordinators (L2 siblings under same L1) do not reference each other
- Peer workers (L3 siblings under same L2) do not reference each other

## D2: Cross-Reference Consistency
- `MANDATORY READ` paths exist on disk (Glob each path)
- `Reference Files` section paths exist on disk
- Caller/callee references bidirectional: if A lists B as worker, B mentions A as caller
- Skill invocation names (`skill: "ln-NNN-*"`) match actual directory names
- No passive file references (`See`, `Per`, `Follows` pointing to files) -- must be `**MANDATORY READ:** Load`
- Multiple `**MANDATORY READ:**` in same section -> group into ONE block at section start
- `> **Paths:**` note present after frontmatter if SKILL.md contains any file references

## D3: Duplication
- Same instructions/rules not repeated across multiple skills
- Shared logic lives in `shared/references/`, skill-specific in SKILL.md (SRP)
- No copy-pasted blocks that should be a MANDATORY READ to a shared file

## D4: Contradiction Detection
- Thresholds (confidence, impact, scores) consistent across related skills
- Status transitions consistent (who sets which status)
- Rules about same concept do not conflict between caller and callee
- Verdict names and categories match across prompt templates, workflows, SKILL.md files

## D5: Context Economy
- No large inline blocks that could be conditional MANDATORY READs
- Metadata in table format where possible
- No verbose explanations where table/list suffices
- No filler words: "simply", "quickly", "easily", "on top of that", "in many cases"
- No AI vocabulary: "Additionally", "Furthermore", "Moreover", "testament", "landscape", "showcasing", "leverage", "utilize", "foster", "streamline", "delve", "tapestry", "multifaceted", "serves as", "functions as", "stands as". Use: "also", "shows", "use", "help", "explore", "is", "has"
- Passive voice where active is clearer ("File should be loaded" -> "Load file")
- Sentences over 25 words -- flag for splitting
- Verbose phrases not applied from `shared/concise_terms.md` ("in order to" -> "to", "make sure that" -> "ensure")
- Every content block must enable a specific agent action or decision -- remove if agent behavior unchanged without it
- Tables must add information beyond adjacent text -- no restating
- 1:1 mapping tables (each row = one input -> one output, no conditions) -> convert to inline list
- Tables echoing template section names/structure -> reference template, don't duplicate
- **Tiebreaker re-read:** re-read each changed section end-to-end. If same instruction can be expressed in fewer words without losing agent actionability -- compress. Apply `code_efficiency_criterion` tiebreaker to skill text: among equivalent formulations, choose shorter

## D6: Stale Artifacts
- No references to removed/renamed skills or files
- No outdated caller names (skill renamed but old name in callers)
- No instructions about features that no longer exist
- No placeholder/TODO markers left from previous edits

## D7: Structural Compliance
- YAML frontmatter has `name` and `description` fields
- If `description` contains `:`, wrapped in double quotes
- `**Version:** X.Y.Z` and `**Last Updated:** YYYY-MM-DD` present at end of file
- No `**Changes:**` section exists
- Files in `references/` are actually referenced from SKILL.md (no orphan reference files)
- `## Definition of Done` section present (all skills -- L1, L2, L3) with items as checkboxes (`- [ ]`)
- `## Meta-Analysis` phase present with `MANDATORY READ` to `shared/references/meta_analysis_protocol.md` (L1 orchestrators and L2 coordinators only)
- Publishing skills (contain `gh api graphql.*mutation` or `gh issue comment`) must have: a Fact-Check phase AND `MANDATORY READ` to `shared/references/humanizer_checklist.md`

## D8: Architecture Conformance
- SKILL.md <= 800 lines total
- Frontmatter `description` <= 200 chars
- Phase/step numbering sequential (1, 2, 3, 4 -- no gaps). Exception: 4a/4b for CREATE/REPLAN
- Orchestrators (L1/L2) delegate work, not execute directly -- no detailed implementation logic
- Workers (L3) execute, not decide workflow -- no routing/priority logic
- L2->L2 cross-category delegation follows forward-flow (0XX->1XX->...->6XX), except 0XX shared services
- Coupling reduction in `shared/` files -- shared references describe patterns, NOT consumers. Forbidden in any form: `Used by`, `Skills using this:`, `For ln-NNN`, `via ln-NNN` suffixes, skill names in role descriptions, skill IDs in code examples. Use generic role names (`task executor`, `review worker`). Consumers reference shared via MANDATORY READ; reverse direction is never needed

## D9: Pattern Compliance (conditional -- `ln-6*` audit skills only)
- References `shared/references/two_layer_detection.md` via MANDATORY READ
- Scoring formula consistent: `penalty = (C*2.0) + (H*1.0) + (M*0.5) + (L*0.2)`
- Report structure follows `shared/templates/audit_worker_report_template.md`
