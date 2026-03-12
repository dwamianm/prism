"""Benchmark: VSA memory accuracy on the changing_facts scenario.

Runs the full scenario and measures retrieval quality metrics:
- Precision@k: fraction of top-k results that are relevant
- Recall@k: fraction of relevant items found in top-k
- MRR (Mean Reciprocal Rank): 1/rank of first relevant result
- Hit Rate: whether any relevant result appears in top-k
- Supersedence accuracy: correct lifecycle state assignments
"""

import pytest
from research.vsa.memory import VSAMemory


def _build_scenario() -> VSAMemory:
    """Build the full changing_facts scenario."""
    mem = VSAMemory(dim=10_000)

    # Week 1
    mem.store("Our backend database is MySQL 8.0 and it handles all our data storage needs.",
              node_type="fact", day=1, tags=["database", "mysql"])
    mem.store("I use VS Code as my primary editor with the Python extension.",
              node_type="preference", day=1, tags=["editor", "vscode"])
    mem.store("Our API layer is built on REST with Flask as the web framework.",
              node_type="fact", day=2, tags=["api", "rest", "flask"])
    mem.store("The team consists of Alice (frontend lead), Bob (backend), and Charlie (devops).",
              node_type="fact", day=2, tags=["team", "people"])
    mem.store("We deploy our services on AWS EC2 instances managed by Ansible.",
              node_type="fact", day=3, tags=["infrastructure", "ec2", "aws"])
    mem.store("Our project Alpha is the main revenue-generating product.",
              node_type="fact", day=4, tags=["project", "alpha"])
    mem.store("We use pytest for all our Python testing with 85% code coverage.",
              node_type="fact", day=5, tags=["testing", "pytest"])

    # Day 12: database migration
    id_pg = mem.store("We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.",
                      node_type="fact", day=12, tags=["database", "postgresql", "migration"])
    mem.detect_supersedence("We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.", id_pg)

    id_nm = mem.store("MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.",
                      node_type="fact", day=12, tags=["database", "mysql", "deprecated"])
    mem.detect_supersedence("MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.", id_nm)

    # Settled fact: clean current-state record (what an organizer would generate)
    mem.store("Our database is PostgreSQL with JSON support and strong performance.",
              node_type="fact", day=13, tags=["database", "postgresql"])

    # Day 14: editor switch
    id_nvim = mem.store("I switched from VS Code to Neovim for all my development work. The modal editing is much faster.",
                        node_type="preference", day=14, tags=["editor", "neovim"])
    mem.detect_supersedence("I switched from VS Code to Neovim for all my development work. The modal editing is much faster.", id_nvim)

    # Settled fact
    mem.store("My primary editor is Neovim with modal editing.",
              node_type="preference", day=14, tags=["editor", "neovim"])

    # Day 20: team update
    mem.store("Diana joined our team as a data engineer. She specializes in ETL pipelines.",
              node_type="fact", day=20, tags=["team", "people", "diana"])

    # Day 32: API migration
    id_gql = mem.store("We migrated our API from REST to GraphQL using Strawberry. The developer experience is much better.",
                       node_type="fact", day=32, tags=["api", "graphql", "strawberry"])
    mem.detect_supersedence("We migrated our API from REST to GraphQL using Strawberry. The developer experience is much better.", id_gql)

    id_rest = mem.store("Our REST API is deprecated and will be removed next quarter.",
                        node_type="fact", day=32, tags=["api", "rest", "deprecated"])
    mem.detect_supersedence("Our REST API is deprecated and will be removed next quarter.", id_rest)

    # Settled fact
    mem.store("Our API layer is GraphQL powered by Strawberry framework.",
              node_type="fact", day=33, tags=["api", "graphql", "strawberry"])

    # Day 35: infrastructure migration
    id_k8s = mem.store("We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling and deployment.",
                       node_type="fact", day=35, tags=["infrastructure", "kubernetes", "eks"])
    mem.detect_supersedence("We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling and deployment.", id_k8s)

    # Settled fact
    mem.store("Our services run on Kubernetes (EKS) for scaling and deployment.",
              node_type="fact", day=36, tags=["infrastructure", "kubernetes", "eks"])

    # Day 45: new project
    mem.store("We started project Beta, a new analytics platform built on the PostgreSQL data warehouse.",
              node_type="fact", day=45, tags=["project", "beta", "analytics"])

    # Day 55: editor switch back
    id_vsc = mem.store("I went back to VS Code from Neovim. The integrated debugging and extensions are too valuable to give up.",
                       node_type="preference", day=55, tags=["editor", "vscode"])
    mem.detect_supersedence("I went back to VS Code from Neovim. The integrated debugging and extensions are too valuable to give up.", id_vsc)

    # Settled fact
    mem.store("My primary editor is VS Code with integrated debugging and extensions.",
              node_type="preference", day=55, tags=["editor", "vscode"])

    return mem


# Ground truth: for each query at each day, which keywords MUST be in the
# top result(s) and which MUST NOT be there.
BENCHMARKS = [
    # (query_day, query, must_contain_top1, must_contain_top5, must_not_top5, description)
    (15, "What database does the project use?",
     ["postgresql"], ["postgresql"], ["mysql"],
     "PostgreSQL should be current after migration"),
    (15, "What editor do I use for development?",
     ["neovim"], ["neovim"], [],
     "Neovim should be current after switch"),
    (25, "Who is on the team?",
     ["alice"], ["alice", "bob"], [],
     "Team members should persist"),
    (40, "What API technology do we use?",
     ["graphql"], ["graphql"], [],
     "GraphQL should be current after migration"),
    (40, "How do we deploy our services?",
     ["kubernetes"], ["kubernetes"], [],
     "Kubernetes should be current after migration"),
    (60, "What editor do I use?",
     ["vs code"], ["vs code"], [],
     "VS Code after switching back"),
    (90, "Who are the team members?",
     ["alice"], ["alice"], [],
     "Team facts should persist at 90 days"),
    (90, "What database does the project use currently?",
     ["postgresql"], ["postgresql"], [],
     "Database fact should persist at 90 days"),
    # Additional stress queries
    (50, "What projects are we working on?",
     [], ["alpha"], [],
     "Project Alpha should be retrievable"),
    (40, "What testing framework do we use?",
     ["pytest"], ["pytest"], [],
     "Testing facts should persist"),
    (25, "Who joined the team recently?",
     ["diana"], ["diana"], [],
     "Recent team addition should be findable"),
    (60, "What infrastructure do we use?",
     ["kubernetes"], ["kubernetes"], ["ec2"],
     "Kubernetes should be current, EC2 superseded"),
]


def _check_keywords(content: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    """Check which keywords are found/missing in content."""
    found = [kw for kw in keywords if kw.lower() in content.lower()]
    missing = [kw for kw in keywords if kw.lower() not in content.lower()]
    return found, missing


def test_benchmark_accuracy():
    """Run all benchmarks and compute aggregate metrics."""
    mem = _build_scenario()

    total = len(BENCHMARKS)
    top1_hits = 0
    top5_hits = 0
    exclusion_passes = 0
    mrr_sum = 0.0

    results_report = []

    for day, query, must_top1, must_top5, must_not, desc in BENCHMARKS:
        mem_copy = _build_scenario()  # fresh copy to avoid state leak
        mem_copy.organize(current_day=day)

        results = mem_copy.retrieve(query, query_day=day, top_k=10)

        # Top-1 content
        top1_content = results[0].record.content.lower() if results else ""

        # Top-5 content
        top5_content = " ".join(r.record.content.lower() for r in results[:5])

        # Top-1 accuracy
        _, top1_missing = _check_keywords(top1_content, must_top1)
        top1_hit = len(top1_missing) == 0 if must_top1 else True

        # Top-5 recall
        top5_found, top5_missing = _check_keywords(top5_content, must_top5)
        top5_hit = len(top5_missing) == 0

        # Exclusion check
        excluded_found, _ = _check_keywords(top5_content, must_not)
        exclusion_pass = len(excluded_found) == 0

        # MRR: find rank of first relevant result
        rr = 0.0
        if must_top5:
            primary_kw = must_top5[0].lower()
            for i, r in enumerate(results[:10]):
                if primary_kw in r.record.content.lower():
                    rr = 1.0 / (i + 1)
                    break

        if top1_hit:
            top1_hits += 1
        if top5_hit:
            top5_hits += 1
        if exclusion_pass:
            exclusion_passes += 1
        mrr_sum += rr

        status = "PASS" if (top5_hit and exclusion_pass) else "FAIL"
        results_report.append(
            f"  [{status}] Day {day:2d}: {desc}\n"
            f"         Query: \"{query}\"\n"
            f"         Top-1 hit: {top1_hit} | Top-5 recall: {len(top5_found)}/{len(must_top5)} | "
            f"Exclusion: {exclusion_pass} | RR: {rr:.2f}"
        )

    # Compute metrics
    precision_at_1 = top1_hits / total
    recall_at_5 = top5_hits / total
    exclusion_rate = exclusion_passes / total
    mrr = mrr_sum / total

    # Print report
    print("\n" + "=" * 70)
    print("  VSA Memory Benchmark: changing_facts scenario")
    print("=" * 70)
    for line in results_report:
        print(line)
    print("-" * 70)
    print(f"  Precision@1:    {precision_at_1:.1%} ({top1_hits}/{total})")
    print(f"  Recall@5:       {recall_at_5:.1%} ({top5_hits}/{total})")
    print(f"  Exclusion rate: {exclusion_rate:.1%} ({exclusion_passes}/{total})")
    print(f"  MRR:            {mrr:.3f}")
    print(f"  Overall grade:  ", end="")

    # Grade
    composite = (precision_at_1 * 0.3 + recall_at_5 * 0.3 + exclusion_rate * 0.2 + mrr * 0.2)
    if composite >= 0.9:
        grade = "A"
    elif composite >= 0.8:
        grade = "B"
    elif composite >= 0.7:
        grade = "C"
    elif composite >= 0.6:
        grade = "D"
    else:
        grade = "F"
    print(f"{grade} ({composite:.1%})")
    print("=" * 70)

    # Assert minimum quality bar
    assert recall_at_5 >= 0.75, f"Recall@5 too low: {recall_at_5:.1%}"
    assert exclusion_rate >= 0.75, f"Exclusion rate too low: {exclusion_rate:.1%}"
    assert mrr >= 0.3, f"MRR too low: {mrr:.3f}"


def test_supersedence_accuracy():
    """Measure accuracy of automatic supersedence detection."""
    mem = _build_scenario()

    # Ground truth: which memories should be superseded
    expected_superseded_keywords = [
        "mysql 8.0",        # superseded by PostgreSQL migration
        "vs code as my primary editor",  # superseded by Neovim switch
        "rest with flask",  # superseded by GraphQL migration
        "ec2 instances",    # superseded by Kubernetes migration
    ]

    expected_active_keywords = [
        "postgresql",       # current database
        "graphql",          # current API
        "kubernetes",       # current infrastructure
        "alice",            # team member (never superseded)
        "diana",            # team member (never superseded)
        "pytest",           # testing (never superseded)
    ]

    superseded_correct = 0
    superseded_total = len(expected_superseded_keywords)
    active_correct = 0
    active_total = len(expected_active_keywords)

    for kw in expected_superseded_keywords:
        for rec in mem._memories:
            if kw.lower() in rec.content.lower():
                if rec.lifecycle_state == "superseded":
                    superseded_correct += 1
                else:
                    print(f"  Should be superseded: [{rec.lifecycle_state}] {rec.content[:60]}")
                break

    for kw in expected_active_keywords:
        for rec in mem._memories:
            if kw.lower() in rec.content.lower():
                if rec.lifecycle_state != "superseded":
                    active_correct += 1
                else:
                    print(f"  Should be active: [{rec.lifecycle_state}] {rec.content[:60]}")
                break

    supersedence_precision = superseded_correct / superseded_total if superseded_total else 1.0
    active_precision = active_correct / active_total if active_total else 1.0

    print(f"\n  Supersedence accuracy:")
    print(f"    Correctly superseded: {superseded_correct}/{superseded_total} ({supersedence_precision:.0%})")
    print(f"    Correctly active:     {active_correct}/{active_total} ({active_precision:.0%})")
    print(f"    Combined:             {(supersedence_precision + active_precision) / 2:.0%}")

    assert supersedence_precision >= 0.75, f"Supersedence precision too low: {supersedence_precision:.0%}"
    assert active_precision >= 0.75, f"Active precision too low: {active_precision:.0%}"
