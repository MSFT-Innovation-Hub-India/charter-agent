# Consolidation reconciliation rules — SOW

> Forward-dated. The consolidation pass (Phase 6 in [AGENTS.md](../../../../AGENTS.md)) is not yet implemented. When it lands, these are the heuristics it should apply. Until then, this file exists so the rules don't get re-invented.

When the final SOW Word document is assembled from each task's accepted submission, three cross-section checks matter. None of them should ever be silently "fixed" — surface a finding and let the SOW Owner decide.

1. **Effort reconciliation.** The total effort hours in the commercial section equal the sum of effort hours in the PM-scope WBS, per role and per phase. Tolerance: zero. If totals differ, the finding names both numbers.
2. **Component coverage.** Every component named in the technical scope appears in (a) the PM-scope WBS and (b) the commercial pricing breakdown. Missing entries are named explicitly.
3. **Milestone consistency.** Every milestone in the commercial payment schedule corresponds to a deliverable named in either the technical or the PM scope.

If the RFP mandated a specific SOW template, honour its styles, headers, footers, and section ordering. Otherwise use a clean default (1" margins, Heading 1 per section, embedded TOC). Never pre-create a template path at charter time and never share folders with external collaborators — the deliverable is assembled at consolidation time and lands in the SOW Owner's OneDrive (or emailed back for review).

Per-section ordering defaults live in `commit_charter`'s `consolidation_section_order` argument; override at charter time only if the RFP demands a different structure.
