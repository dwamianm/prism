"""Simulation scenario registry.

All built-in scenarios are exported in SCENARIOS for CLI discovery.
"""

from simulations.scenarios.changing_facts import CHANGING_FACTS_SCENARIO
from simulations.scenarios.decay_mechanics import DECAY_MECHANICS_SCENARIO
from simulations.scenarios.information_accumulation import (
    generate_accumulation_scenario,
)
from simulations.scenarios.reinforcement import REINFORCEMENT_SCENARIO

SCENARIOS: dict = {
    "changing_facts": CHANGING_FACTS_SCENARIO,
    "decay_mechanics": DECAY_MECHANICS_SCENARIO,
    "information_accumulation": generate_accumulation_scenario(),
    "reinforcement": REINFORCEMENT_SCENARIO,
}
