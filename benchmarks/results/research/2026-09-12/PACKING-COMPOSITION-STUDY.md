# Fixed head reservation and quarter-length packing

The registered development experiment combines the separately studied reserved
top multipath candidate with a score divided by token length to the power 0.25.
It compares density, quarter-length, one-head density and their combination on
all 119 existing development questions at 2K, 4K and 8K tokens. The primary
comparison is combination versus quarter-length at 4K. No production default
changes follow from this development experiment.

Frozen source `273fd96` (root copy `80bf660`) uses the verified hybrid study's
native-parser snapshots. All 714 density and quarter-length controls must
reproduce before results count. Candidate selection receives no answer labels;
all 1,428 rendered contexts and source-credit measurements require independent
verification after native completion. Existing priority tiers, qualification
text, whole-source accounting and token limits remain in force.

The focused 16 tests passed before registration. The last registration-only
change adds source-module hashes and requires exactly 119 reference rows.
This cohort has already been examined. Source retention is not answer accuracy,
and this capture source is separate from the two incomplete one-head reader
trials. A held-out decision and complete answer comparison remain necessary.

See [the immutable registration](packing-composition-dev-plan.json).

The capture and independent verifier both exited zero. All 1,428 contexts and
714 controls passed; the verifier counted serialized tokens, rejected unknown
or duplicate candidate IDs, required whole source text for credit, excluded
pointers and recomputed all category totals and history-cluster intervals.
There are 114 labelled questions in 111 source-history groups; five questions
without source labels receive no source-recall value.

| Budget | Density | Quarter-length | One-head density | Combination |
|---|---:|---:|---:|---:|
| 2K | 65.13% | 92.54% | 73.98% | 93.13% |
| 4K | 74.85% | 95.03% | 81.51% | 95.91% |
| 8K | 82.02% | 96.05% | 88.45% | 96.93% |

The registered primary comparison gains 0.88 percentage points over
quarter-length at 4K: one win, no losses and 113 ties. Its 95% history-cluster
interval is 0.00 to 2.68 points. The additional retained source is in the
assistant category, moving that category from 7/9 to 8/9. At 2K the combination
has one win and one loss versus quarter-length; its interval includes zero.
It has no per-question source losses versus density at any examined budget.

This does not establish that the extra reservation is better than the simpler
quarter-length rule. Most of the gain over density comes from reducing the
length penalty. Keep both as research candidates until complete answer and
broader regression evidence exist. The 2K native-parser controls differ from
the earlier native-context head study; those separate artifacts must not be
treated as identical trials.

See [native capture completion](packing-composition-dev-completion-273fd96.json),
[all verified results](packing-composition-dev-verification.json) and
[native verification completion](packing-composition-dev-verifier-completion.json).
