# Annotation-selected source control: completed diagnostic

Supplying the reader with annotation-selected passages from actual retained
history produced 66/96 correct answers (68.75%), versus 34/96 (35.42%) with no
memory. The paired persona-cluster difference is +33.33 percentage points,
95% interval [22.92, 42.71], using 2,000 samples and seed 42.

This is a diagnostic of short, annotation-selected evidence, **not product
retrieval**. Selection and source-only formatting change together. It was
designed after inspecting the completed packing pilot and is neither a new
holdout nor a leaderboard result. No default or production score changes follow.

All 96 cases and 192 primary answers completed with native exit 0 under the
[registered plan](personamem-v2-annotated-reader-plan.json), with code `c183780`.
The [independent verification](personamem-v2-annotated-reader-completion-c183780.json)
also completed with native exit 0. It reconstructs source-selected contexts,
checks all requests/raw responses, then joins labels. It records raw-output
hashes, every case, all four category dimensions and the bootstrap comparison.
Fourteen authored verifier/runner checks passed before new correctness was read.

Contexts contain only exact role/text matches drawn from original source history,
in its original order, with source/role markers and no clipped passages. They
require at most 1,102 `cl100k_base` tokens. No initial persona, unmatched annotation
text or correct-answer label reaches the reader. All six cases without a matched
source receive empty context and remain in the denominator; both arms answered
the same 4/6 correctly there.

No primary answer was malformed. The 24 repeated answers had no disagreements.
All 96 no-memory requests exactly match the earlier pilot's request hashes, but
one answer changed, increasing its score from 33 to 34. Fixed temperature and
seed therefore did not guarantee identical answers across these two runs.

## What remains unexplained

The source-selected reader answered 66/86 self-related questions correctly and
0/10 questions about other people. Updated-preference questions reached 13/18;
therapy-background questions remained 3/9. All categories are retained in the
verification report, including these failures.

Two manually inspected other-person cases show direct attribution mistakes:

- Case `58623f06…`: an email proposes a colleague's coffee-themed fundraiser.
  The selected answer invents the user's personal preference for craft coffee.
- Case `0e0ecffaa…`: a colleague's email hints at discomfort during pollen season.
  The selected answer assigns that condition to the user.

These are observed reader failures in this diagnostic, not evidence that the
ingestion extractor made the same claims: both studies used raw source storage
without extraction. Source speaker/role is not the subject of every assertion,
particularly inside quoted documents. The next implementation audit should
check this distinction through extraction, entity resolution, organizer merges,
profiles and context presentation. Preserve the source and unresolved attribution
rather than converting a quoted first-person statement into a verified user fact.

The diagnostic supports work on precise evidence selection, while the remaining
30 errors require reader, attribution and label-support analysis. Annotated
snippets are not an independently audited, exhaustive set of sufficient evidence.
