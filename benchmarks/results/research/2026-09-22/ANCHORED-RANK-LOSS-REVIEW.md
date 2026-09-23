# Original-anchor answer trial: all ten paired losses

Date: 2026-09-22 (completed 2026-09-23 UTC). This is post-hoc review of the
complete registered 500+500 comparison, with no relabeling or replacement runs.
The machine [all-case audit](opt-in-anchored-answer-audit-v1.json) retains all
21 answer transitions, original answers/judgments, source groups and hashes.

| Question | Input changed? | Observed failure |
|---|---|---|
| `5a7937c8` | Yes | Excluded the church food drive from faith activities despite extracting it; category interpretation changed from three to two. Annotated sources present in both. |
| `bf659f65` | No | Counted only explicit purchase/download statements; the passing control inferred an owned signed vinyl was purchased. Same incomplete context, no retrieval effect. |
| `gpt4_372c3eed` | No | Omitted the two-year associate degree from total education despite quoting it. Same complete source context. |
| `51c32626` | Yes | Claimed no submission date was available. The ACL February 1 deadline sentence is packed in both arms; source retention did not prevent reader omission/inference differences. |
| `gpt4_a2d1d1f6` | Yes | Used elapsed full 24-hour periods (two) instead of the reference's calendar-day difference (three). Same annotated date source retained. |
| `a3838d2b` | Yes | Missed the July 17 charity golf event while counting. That exact event sentence remains packed in both arms; four of six annotated source turns retained in each. |
| `gpt4_2c50253f` | No | Used the older 8:30 wake-up time as the base instead of the stated newer 7:00 time. Same complete source context. |
| `gpt4_93159ced_abs` | Yes | Acknowledged the unsupported Google premise but answered a substituted NovaTech question. Required abstention lost. |
| `852ce960` | Yes | Preferred the older $350,000 mortgage claim to the later $400,000 statement, labeling the later one a misremembering without evidence. Both claims remain packed. No truth-by-recency rule is added to memory. |
| `6222b6eb` | Yes | Both contexts omit the required assistant answer. The passing control supplied the reference from outside the packed evidence; the candidate declined. This score transition is not a new loss of the annotated source. |

Seven changed-input losses and three identical-input losses are retained. Among
367 changed inputs there were eight wins and seven losses; among 133 identical
inputs there were three wins and three losses. Seven complete-source gains
produced three wins and zero losses; the sole complete-source loss was wrong in
both arms. Source completeness is useful but does not establish semantic
sufficiency, temporal interpretation or correct use by the reader.

The primary outcome is 430/500 versus 429/500, 11 wins/10 losses, +0.2 percentage
points [−1.6,+2.0]. No significant answer gain or untouched confirmation is
established. Preserve this policy as opt-in; do not promote it from this trial.
