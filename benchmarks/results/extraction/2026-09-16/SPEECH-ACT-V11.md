# Speech-act v11 initial confirmation and scorer audit

The initial v11 execution was complete and correctly bound, but its reported
quality failure is invalid. Raw output and materialized nodes show that v11
retained both `tried_to_install = CUDA 12.4` and
`attempted_install_count = twice`, with the full source citation and the
registered v5/v11 policies.

The assay expected the semantic stem `try`. Its scorer used plain substring
matching, so `try` matched `trying` but not the irregular inflection `tried`.
It consequently counted the valid target as both missing and unsafe. This is a
benchmark defect, not a product regression.

The result artifact is retained unchanged. A corrected scorer must explicitly
recognize `try`, `tried`, `tries`, and `trying`, add a regression test, receive a
new registration hash, and rerun the unchanged cases and product implementation.
No v11 quality claim is made from this initial score.
