# Speech-act v9 development confirmation

The confirmation reused the unchanged 14 cases, 15 targets, model digest, and
zero-tolerance gates from the v8 development assay. Its registration bound the
v9 prompt, schema, implementation files, and `speech_act_v3`/`speech_act_v9`
policy identities before execution.

V9 removed the unsafe component relationship found by v8. The complete run had
zero unsafe nonactual claims and zero policy or execution-binding errors. It
passed 13/14 cases and preserved 14/15 structured targets, improving from
11/14 and 13/15 under v8.

The remaining miss was an incomplete structured representation rather than a
false current-state assertion. For “I've tried to install CUDA 12.4 twice...,”
the model emitted the cited fact `attempted_install_count = twice`, retained the
CUDA entity, and kept an accurate summary, but omitted a separate
`attempted_to_install = CUDA 12.4` claim. The full source therefore remained
searchable while the target relation required by the preregistered utility gate
was absent.

The registered gate remains failed. The next candidate should require the
attempted action target even when a count or failure detail is also extracted;
it must retain v9's zero unsafe-claim result.

This is an authored development contrast set using one hosted model profile. It
does not estimate held-out accuracy or establish competitive quality.
