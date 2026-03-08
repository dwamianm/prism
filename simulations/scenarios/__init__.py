"""Simulation scenario registry.

All built-in scenarios are exported in SCENARIOS for CLI discovery.
"""

from simulations.scenarios.changing_facts import CHANGING_FACTS_SCENARIO
from simulations.scenarios.decay_mechanics import DECAY_MECHANICS_SCENARIO
from simulations.scenarios.information_accumulation import (
    generate_accumulation_scenario,
)
from simulations.scenarios.oscillation import OSCILLATION_SCENARIO
from simulations.scenarios.reinforcement import REINFORCEMENT_SCENARIO
from simulations.scenarios.remention import REMENTION_SCENARIO
from simulations.scenarios.surprise_gating import SURPRISE_GATING_SCENARIO

SCENARIOS: dict = {
    "changing_facts": CHANGING_FACTS_SCENARIO,
    "decay_mechanics": DECAY_MECHANICS_SCENARIO,
    "information_accumulation": generate_accumulation_scenario(),
    "oscillation": OSCILLATION_SCENARIO,
    "reinforcement": REINFORCEMENT_SCENARIO,
    "remention": REMENTION_SCENARIO,
    "surprise_gating": SURPRISE_GATING_SCENARIO,
}
