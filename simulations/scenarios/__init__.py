"""Simulation scenario registry.

All built-in scenarios are exported in SCENARIOS for CLI discovery.
"""

from simulations.scenarios.bi_temporal import BI_TEMPORAL_SCENARIO
from simulations.scenarios.changing_facts import CHANGING_FACTS_SCENARIO
from simulations.scenarios.consolidation import CONSOLIDATION_SCENARIO
from simulations.scenarios.decay_mechanics import DECAY_MECHANICS_SCENARIO
from simulations.scenarios.deduplication import DEDUPLICATION_SCENARIO
from simulations.scenarios.dual_stream import DUAL_STREAM_SCENARIO
from simulations.scenarios.entity_snapshots import ENTITY_SNAPSHOTS_SCENARIO
from simulations.scenarios.eval_retrieval import (
    FACTUAL_RETRIEVAL_SCENARIO,
    SUPERSEDENCE_HANDLING_SCENARIO,
    TEMPORAL_RETRIEVAL_SCENARIO,
)
from simulations.scenarios.information_accumulation import (
    generate_accumulation_scenario,
)
from simulations.scenarios.oscillation import OSCILLATION_SCENARIO
from simulations.scenarios.procedural_memory import PROCEDURAL_MEMORY_SCENARIO
from simulations.scenarios.quality_tuning import QUALITY_TUNING_SCENARIO
from simulations.scenarios.reinforcement import REINFORCEMENT_SCENARIO
from simulations.scenarios.remention import REMENTION_SCENARIO
from simulations.scenarios.summarization import SUMMARIZATION_SCENARIO
from simulations.scenarios.surprise_gating import SURPRISE_GATING_SCENARIO
from simulations.scenarios.ttl_archival import TTL_ARCHIVAL_SCENARIO

SCENARIOS: dict = {
    "bi_temporal": BI_TEMPORAL_SCENARIO,
    "changing_facts": CHANGING_FACTS_SCENARIO,
    "consolidation": CONSOLIDATION_SCENARIO,
    "decay_mechanics": DECAY_MECHANICS_SCENARIO,
    "deduplication": DEDUPLICATION_SCENARIO,
    "dual_stream": DUAL_STREAM_SCENARIO,
    "entity_snapshots": ENTITY_SNAPSHOTS_SCENARIO,
    "eval_factual_retrieval": FACTUAL_RETRIEVAL_SCENARIO,
    "eval_temporal_retrieval": TEMPORAL_RETRIEVAL_SCENARIO,
    "eval_supersedence_handling": SUPERSEDENCE_HANDLING_SCENARIO,
    "information_accumulation": generate_accumulation_scenario(),
    "oscillation": OSCILLATION_SCENARIO,
    "procedural_memory": PROCEDURAL_MEMORY_SCENARIO,
    "quality_tuning": QUALITY_TUNING_SCENARIO,
    "reinforcement": REINFORCEMENT_SCENARIO,
    "remention": REMENTION_SCENARIO,
    "summarization": SUMMARIZATION_SCENARIO,
    "surprise_gating": SURPRISE_GATING_SCENARIO,
    "ttl_archival": TTL_ARCHIVAL_SCENARIO,
}
