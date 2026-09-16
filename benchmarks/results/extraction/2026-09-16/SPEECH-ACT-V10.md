# Speech-act v10 development confirmation

V10 kept the unchanged 14-case, 15-target contrast set and zero-tolerance
gates. It added an explicit instruction that an attempt count or failure detail
cannot substitute for the attempted target relation.

The run reproduced v9 exactly at the aggregate level: zero unsafe nonactual
claims, zero policy or execution-binding errors, 13/14 cases passed, and 14/15
targets preserved. The CUDA case again retained the full cited source, entity,
summary, and `attempted_install_count = twice`, but omitted
`attempted_to_install = CUDA 12.4`.

The stronger prompt therefore did not close the remaining utility gap. Further
prompt tuning on this inspected development set is rejected. A subsequent
candidate should use a narrow deterministic recovery rule whose inputs and
output are source-verifiable: a literal first-person tried/attempted clause, the
first exact extracted entity named after the action verb, and no existing
qualified claim for that target.

This authored development result uses one hosted model profile. It is neither
held-out accuracy nor competitive evidence.
