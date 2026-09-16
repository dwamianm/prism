# Speech-act v11 corrected-scorer confirmation

The corrected scorer recognized irregular `tried` predicates and the new run
again had zero unsafe claims, zero policy errors, and zero execution-binding
errors. It passed 13/14 cases and preserved 14/15 targets.

The missing target changed. CUDA passed with its structured attempt target, but
the hosted model returned the exact Phoenix entity and an accurate summary for
“We hope to deploy Phoenix...” while emitting no fact or relationship. This
temperature-zero variation shows that the remaining utility gap is not specific
to attempt counts. It affects nonactual cues when the model recognizes the
target entity but omits the claim.

The narrow source/entity recovery should therefore cover the same explicit cue
family already enforced by the validator, rather than only tried/attempted. It
must still select only the first exact returned entity after the action verb,
avoid duplicate qualified targets, and preserve or skip explicit conditions
rather than flattening them.

This is an authored development result from one hosted model profile, not
held-out accuracy or competitive evidence.
