# PRME Simulation Harness

A developer tool for validating PRME's memory retrieval behavior over simulated time. The harness stores facts through `MemoryEngine.store()`, manipulates timestamps to simulate time passing, and evaluates retrieval results at defined checkpoints.

No LLM API keys are required -- everything runs locally.

## Quick Start

```bash
# List available scenarios
python -m simulations --list

# Run all scenarios
python -m simulations

# Run a specific scenario
python -m simulations changing_facts

# Compare with/without organizing
python -m simulations --compare changing_facts
```

## How It Works

1. **Scenario definition** -- Messages are defined with a simulated `day` number, content, and ground-truth tags.
2. **Bulk storage** -- All messages are stored via `engine.store()` (no LLM extraction).
3. **Time simulation** -- At each checkpoint, node timestamps in DuckDB are rewritten so that the relative age of each message matches the checkpoint's day number. This means `engine.retrieve()` sees correct recency decay.
4. **Evaluation** -- The harness queries the engine and checks whether expected keywords appear (and excluded keywords do not appear) in the top retrieval results.

## Built-in Scenarios

### `changing_facts`
Tests fact supersedence over ~60 days. A user's tech stack evolves (MySQL to PostgreSQL, VS Code to Neovim and back, REST to GraphQL, EC2 to Kubernetes). Verifies that current facts dominate retrieval and superseded facts fade.

### `decay_mechanics`
Tests epistemic decay profiles. Facts with different epistemic types (hypothetical, inferred, asserted, observed) are stored at day 0. Checkpoints at day 5, 15, 50, and 150 verify that hypothetical facts decay fastest and observed facts persist longest.

### `information_accumulation`
Tests scale behavior with 200 messages across 8 topics over 180 days. Includes topic drift (early focus on projects, later on learnings/goals). Verifies cross-topic retrieval accuracy and that recent topics dominate.

## Reading the Output

Each checkpoint shows:
- **[PASS]/[FAIL]** -- Whether the checkpoint's assertions held
- **Query** -- The retrieval query used
- **Found expected** -- Which expected keywords appeared in top-5 results
- **Missing expected** -- Which expected keywords were absent (causes FAIL)
- **Unwanted found** -- Which excluded keywords appeared (causes FAIL)
- **Top results** -- The top-5 retrieved nodes with scores

A summary line shows the overall pass rate.

## Creating Custom Scenarios

Define a `SimScenario` with messages and checkpoints:

```python
from simulations.harness import SimCheckpoint, SimMessage, SimScenario

my_scenario = SimScenario(
    name="my_test",
    description="Test custom behavior",
    messages=[
        SimMessage(day=1, role="user", content="...", tags=["topic"]),
        SimMessage(day=10, role="user", content="...", tags=["topic"]),
    ],
    checkpoints=[
        SimCheckpoint(
            day=15,
            query="What happened?",
            expected_keywords=["keyword"],
            excluded_keywords=["old_keyword"],
            description="Recent facts should appear",
        ),
    ],
)
```

Register it in `simulations/scenarios/__init__.py` to make it available via CLI.

## Metrics Tracked

- **Pass rate** -- Fraction of checkpoints that pass
- **Total nodes** -- Number of nodes stored in the engine
- **Duration** -- Wall-clock time for the full simulation
- **Per-checkpoint** -- Top-N results with composite scores, node types, and lifecycle states
