"""End-to-end wiring fix tests for Phase 3.5.

Milestone gate test asserting all 29 v1.0 Phase 1-3.5 requirements
are satisfied in REQUIREMENTS.md.
"""

from pathlib import Path


def test_milestone_all_v1_requirements_satisfied():
    """Milestone gate: all 29 v1.0 Phase 1-3.5 requirements are satisfied.

    Reads REQUIREMENTS.md and verifies every requirement that should be
    satisfied by the end of Phase 3.5 is checked off. This is a one-time
    gate test -- if this passes, the v1.0 audit has no remaining gaps.
    """
    req_path = Path(__file__).parent.parent / ".planning" / "REQUIREMENTS.md"
    content = req_path.read_text()

    # All requirements that must be [x] for the Phase 1-3.5 milestone
    required_satisfied = [
        # Storage (Phase 1)
        "STOR-01", "STOR-02", "STOR-03", "STOR-04",
        "STOR-05", "STOR-06", "STOR-07", "STOR-08",
        # Ingestion (Phase 2)
        "INGE-01", "INGE-02", "INGE-03", "INGE-04", "INGE-05",
        # Retrieval (Phase 3)
        "RETR-01", "RETR-02", "RETR-03", "RETR-04", "RETR-05", "RETR-06",
        # Epistemic (Phases 3.1, 3.3)
        "EPIS-01", "EPIS-02", "EPIS-04", "EPIS-05",
        # Namespace (Phases 3.2, 3.4)
        "NSPC-01", "NSPC-05",
        # Trust (Phase 3.4)
        "TRST-07",
        # Context Packing (Phase 3, traceability fixed in Phase 3.5)
        "CTXP-01", "CTXP-02", "CTXP-03",
    ]

    assert len(required_satisfied) == 29, (
        f"Expected 29 milestone requirements, got {len(required_satisfied)}"
    )

    unsatisfied = []
    for req_id in required_satisfied:
        if f"[x] **{req_id}**" not in content:
            unsatisfied.append(req_id)

    assert not unsatisfied, (
        f"Milestone gate FAILED: {len(unsatisfied)} requirements not marked "
        f"satisfied in REQUIREMENTS.md: {unsatisfied}"
    )
