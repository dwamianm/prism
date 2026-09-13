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
