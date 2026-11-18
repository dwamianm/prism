# Issue 474: bounded named claim guards

Approved acceptance criteria (2026-11-18): with named claim text enabled,
preserve meaning in `I breastfeed my newborn.`, `My uncle owns an old gold mine.`,
`Henry I was king.`, and `world war I was a global conflict.` Preserve genuine
first-person neighbors, source evidence, and default-off behavior.

Approach approved before implementation: extend realistic base-form exclusions;
walk mine's noun modifiers to a determiner or clause boundary; recognize lowercase
title heads before copulas and clause-initial conventional regnal names. Reuse
whole-sentence label fallback. No NLP dependency, default flip, model evaluation,
or live reingestion. RFC-0016 saved-plan publication is unchanged.

Files: named_text.py, reingest.py, test_named_claim_text.py, derivation fixture,
ENTITY-IDENTITY.md, this plan and self-review evidence.

Finite validation: targeted named-text, derivation and reingest tests; scoped Ruff;
final `uv run pytest -q`. Exactly one implementer, zero independent reviewers,
maximum two unsuccessful repair cycles per behavior. Review policy's independent
roster is overridden by the explicit approved zero-reviewer budget.

Risks: English guards remain heuristic, not general grammar recognition. Required
DERIVATION_VERSION 2 to 3 makes all older derivations stale on the next scan,
including option-off stores; existing rows change only on explicit reingestion.
Source events and saved plans remain unchanged. No new security boundary.

Delivery: issue-only commit, push and PR targeting main; hold before auto-merge.
