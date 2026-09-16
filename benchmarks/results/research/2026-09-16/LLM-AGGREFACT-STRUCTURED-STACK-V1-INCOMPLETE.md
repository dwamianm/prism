# LLM-AggreFact structured stack v1 — input identity failure

The first registered execution stopped during selected-cohort identity
validation. The runner passed `development` to the frozen selector, while the
FactCG registration used the split key `dev` in its selection hash. The exact
identity check rejected the mismatch before deterministic features were
extracted, a model was fitted, or predictions were produced.

No result file was written and the runner had no test-file argument. This run
supports no quality claim. A retry must bind the corrected runner hash in a new
registration; the model, features, document grouping, folds and gates may remain
unchanged.
