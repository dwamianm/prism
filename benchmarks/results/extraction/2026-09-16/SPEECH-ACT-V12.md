# Speech-act v12 development confirmation

V12 passed every preregistered gate on the unchanged authored contrast set:

- 14/14 cases passed;
- 15/15 required actual or qualified targets were materialized;
- 0 unsafe nonactual claims survived in facts or relationships;
- 0 grounding/materialization policy mismatches occurred;
- 0 model or execution-binding errors occurred.

The registration bound the exact 14 cases, 15 targets, prompt, response schema,
implementation files, zero-tolerance gates, temperature, retry count, local
Ollama endpoint, and `deepseek-v4.1-flash:cloud` digest before execution. The
worker exited cleanly and reported `speech_act_v6` extraction records with
`speech_act_v12` plans.

The development sequence retained each failure rather than tuning it away:
v8 passed 11/14 with one unsafe component relationship and two missing targets;
v9 removed the unsafe claim and passed 13/14; v10 showed stronger prompt wording
alone did not reliably retain CUDA; corrected v11 retained CUDA but a separate
run omitted the Phoenix hope claim. V12 generalizes the narrow recovery to the
same explicit nonactual cue family already checked by admission. Recovery may
use only the first exact entity returned by the model after the source action
verb, preserves explicit conditions, skips existing qualified targets, and
does not search memory or infer an unreturned entity.

The live v12 run happened to receive complete claims from the hosted model, so
the deterministic recovery is established by focused causal tests rather than
by an activated fallback in this one execution. The earlier temperature-zero
runs demonstrate that hosted output can still vary.

This is an authored development assay against one hosted model profile. It does
not estimate held-out accuracy, cover third-person or indirect speech acts, or
establish competitive leadership.
