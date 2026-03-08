"""Self-organizing memory organizer (RFC-0015).

Provides virtual decay computation, opportunistic maintenance, and
explicit organize/end_session job execution for memory lifecycle
management.
"""

from __future__ import annotations

from prme.organizer.consolidation import (
    MemoryCluster,
    cluster_similar_memories,
    consolidate_cluster,
    forget_consolidated,
    run_consolidation_pipeline,
)
from prme.organizer.decay import (
    REINFORCEMENT_DECAY_RATE,
    apply_virtual_decay,
    compute_effective_confidence,
    compute_effective_salience,
)
from prme.organizer.jobs import ALL_JOBS, run_job
from prme.organizer.maintenance import MaintenanceRunner
from prme.organizer.models import (
    ConsolidationResult,
    JobResult,
    MaintenanceResult,
    OrganizeResult,
)

__all__ = [
    "ALL_JOBS",
    "ConsolidationResult",
    "MemoryCluster",
    "REINFORCEMENT_DECAY_RATE",
    "JobResult",
    "MaintenanceResult",
    "MaintenanceRunner",
    "OrganizeResult",
    "apply_virtual_decay",
    "cluster_similar_memories",
    "compute_effective_confidence",
    "compute_effective_salience",
    "consolidate_cluster",
    "forget_consolidated",
    "run_consolidation_pipeline",
    "run_job",
]
