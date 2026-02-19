"""PRME - Portable Relational Memory Engine.

A local-first, embeddable memory substrate for LLM-powered systems.
Combines event sourcing, graph-based relational modeling, hybrid retrieval,
and scheduled memory reorganization.
"""

__version__ = "0.1.0"

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.types import EdgeType, LifecycleState, NodeType, Scope

__all__ = [
    "EdgeType",
    "LifecycleState",
    "MemoryEngine",
    "NodeType",
    "PRMEConfig",
    "Scope",
]
