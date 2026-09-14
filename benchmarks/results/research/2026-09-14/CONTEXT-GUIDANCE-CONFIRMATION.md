# Context-guidance answer confirmation

Status: **automatic gate passed; expert audit approves temporal-only product wiring**.

This prospectively registered confirmation added compact task guidance only
after the balanced 4K packer had selected its records. The complete guided
output was token-counted, and guidance was omitted unless it fit without
evicting or downgrading any memory. The fixed 381-question confirmation
partition yielded 70 eligible contexts: 50 temporal, 16 personalization, and
4 current-state. All 70 frozen Qwen reader calls and 70 unique Gemma judge
calls completed without failures or retries.

## Result

| Guidance | Questions | Baseline correct | Guided correct | Gains | Losses |
|---|---:|---:|---:|---:|---:|
| Temporal | 50 | 31 | 34 | 4 | 1 |
| Personalization | 16 | 9 | 9 | 1 | 1 |
| Current state | 4 | 3 | 3 | 0 | 0 |
| **All** | **70** | **43** | **46** | **5** | **2** |

The preregistered automatic gate passed. Source retention was identical in
every pair because the implementation selects records before considering the
guidance prefix.

## Expert transition audit

All four temporal gains are credible. Guidance corrected a chandelier interval
from three to four weeks, identified a March 3 train ride as later than a March
2 bus ride whose note said “today,” computed March 17 through April 24 as 38
days, and resolved February 26 through March 20 as about three weeks.

The apparent temporal loss, `c8090214`, is judge variance rather than a
demonstrated regression. Both responses declined to give an exact day count
despite evidence that the events were one week apart. The prior baseline judge
accepted that refusal while the confirmation judge rejected the guided
response. There is no paired semantic deterioration to attribute to guidance.

Personalization had one credible gain: `1a1907b4` used the user's mixology
class, Pimm's Cup, and Hendrick's interests in its cocktail suggestions. It also
had one real loss: `1c0ddc50` suggested true-crime podcasts and meditation even
though the user wanted to branch away from true crime and self-improvement.
Equal aggregate accuracy therefore does not justify enabling personalization
guidance by default. Current-state guidance produced no transitions in its four
eligible cases.

## Product decision

PRME enables only temporal guidance by default. It includes the normalized
question time and tells the reader to resolve note-relative dates against each
note's `event_time` before doing explicit arithmetic. `PackingConfig` exposes
`context_guidance_mode="off"` for exact pre-guidance behavior and
`context_guidance_mode="all"` for deliberate evaluation of the experimental
current-state and personalization prompts. New retrieval receipts record the
mode; historical receipts preserve their original bytes and mean guidance was
off.

Artifacts:

- Registration: `context-guidance-answer-confirmation-v1-registration.json`
- Exact results: `context-guidance-answer-confirmation-v1-results.json`
- Registration SHA-256: `c88d8b246c26590cf7c445357a522d9769a37acd76038263886b3bfe5dfea398`
- Results SHA-256: `63729ae47b6e493501ab2c5934915270b5aa8b55db91cfe77b8463c4bbddcc0a`

Limits: this partition and its baseline results had been examined in the prior
packing study; one fixed local reader and one calibrated local judge do not
establish universal superiority; and only 70 contexts had enough remaining
budget to include guidance. The experiment supports the temporal default and
the non-displacement policy, not a competitive claim about every workload.
