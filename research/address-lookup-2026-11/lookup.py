"""P7 prototype part 2: the indexed lookup, as a page source behind the unchanged lookup.

The current lookup (``prme.retrieval.temporal.get_assertion_state``) pages every fact, decision and preference of one
owner through ``engine.scan_nodes`` and compares the normalized subject and predicate in Python. On the 20,500
fact pack that is 42 pages, and each page pays the privacy range check again.

This module replaces only the page source. Everything after the page stays in
``prme.retrieval.temporal.classify_page`` and ``prme.retrieval.temporal.build_state``: the owner view, the eligibility
rules, contradiction edges, status, value groups, ordering and truncation. The point is that a prototype result can be
compared with the current path field by field, not that it reimplements it.

How the two scan counts stay identical
--------------------------------------
The current path counts, over the whole queried node type:

- ``missing_structured_assertion``, a node whose metadata has no usable subject/predicate/object/polarity.
- ``selector_mismatch``, a node that is usable but sits at another address.

An address read sees neither, because a node with no usable assertion stores a NULL key and a node at another address
stores another key. Both numbers therefore come from one indexed count over the key columns: the total of the queried
node type, and how many of those have a NULL key. ``selector_mismatch`` is then the total minus the unkeyed minus the
rows the address read returned.

Privacy
-------
The read goes through the same graph object the current path uses (``engine._ordinary_graph``), under the same
connection lock, and applies the same owner view. The index narrows which rows are read; it does not decide what may
be read. The privacy range check still runs, once per lookup instead of once per page, because the lookup now reads
one page. That is a change in cost, not in permission.
"""
from __future__ import annotations

from collections import Counter

from prme.retrieval.aggregation import normalize_assertion_value
from prme.storage._threading import run_to_completion
from prme.types import LifecycleState

from keys import PREDICATE_COLUMN, SUBJECT_COLUMN


def address_page_sql(node_types) -> str:
    """One owner's claims at one address, in the stable-id order the current path pages in.

    Lifecycle is a filter here and not an index column, for the reason ``keys.py`` gives. Every lifecycle state is
    named because the current path pages over all of them and excludes by rule afterwards.
    """
    types = ",".join("?" for _ in node_types)
    states = ",".join("?" for _ in LifecycleState)
    return (
        f"SELECT * FROM nodes WHERE user_id = ? AND scope = ? AND node_type IN ({types}) "
        f"AND {SUBJECT_COLUMN} = ? AND {PREDICATE_COLUMN} = ? "
        f"AND lifecycle_state IN ({states}) ORDER BY id ASC"
    )


def address_page_params(user_id: str, scope, node_types, subject_key: str, predicate_key: str) -> list:
    return [
        user_id,
        getattr(scope, "value", scope),
        *(node_type.value for node_type in node_types),
        subject_key,
        predicate_key,
        *(state.value for state in LifecycleState),
    ]


def selector_count_sql(node_types) -> str:
    """Total claims, and claims with no usable assertion, for one owner, scope and node type set."""
    types = ",".join("?" for _ in node_types)
    states = ",".join("?" for _ in LifecycleState)
    return (
        f"SELECT count(*), count(*) FILTER (WHERE {SUBJECT_COLUMN} IS NULL OR {PREDICATE_COLUMN} IS NULL) "
        f"FROM nodes WHERE user_id = ? AND scope = ? AND node_type IN ({types}) "
        f"AND lifecycle_state IN ({states})"
    )


def selector_count_params(user_id: str, scope, node_types) -> list:
    return [
        user_id,
        getattr(scope, "value", scope),
        *(node_type.value for node_type in node_types),
        *(state.value for state in LifecycleState),
    ]


async def address_rows(engine, *, user_id: str, scope, node_types, subject_key: str, predicate_key: str):
    """The candidate rows at one address, plus the two numbers the scan counts need.

    Returns ``(rows, total, unkeyed)``, or ``None`` when the store cannot take an indexed read under its privacy
    rules. The rows pass through the owner view the current path applies to every page.

    A protected store resolves to ``AdmittedGraphView``, which deliberately exposes only its own methods and does
    not hand out a connection. An indexed read there would have to bypass the admitted projection to reach the SQL,
    which is exactly the boundary that class exists to hold. So this returns ``None`` and the caller keeps the
    current path. A real change would add the indexed page as a method on both facades; a prototype must not widen
    the fence to save a scan.
    """
    graph = await engine._ordinary_graph(user_id, scopes=[scope])
    if not hasattr(graph, "_conn_lock"):
        return None
    page_sql = address_page_sql(node_types)
    page_params = address_page_params(user_id, scope, node_types, subject_key, predicate_key)
    count_sql = selector_count_sql(node_types)
    count_params = selector_count_params(user_id, scope, node_types)

    def read():
        rows = [graph._row_to_node(row) for row in graph._conn.execute(page_sql, page_params).fetchall()]
        total, unkeyed = graph._conn.execute(count_sql, count_params).fetchone()
        return graph._owned_view_sync(rows, user_id), int(total), int(unkeyed)

    async with graph._conn_lock:
        return await run_to_completion(read)


async def indexed_state(engine, query, *, user_id: str):
    """Run the unchanged lookup with its page source swapped for the indexed read.

    The records are classified by ``prme.retrieval.temporal.classify_page`` and the state is built by
    ``prme.retrieval.temporal.build_state``, the same two functions the current path calls. The only values supplied
    from here are the page and the two scan counts.
    """
    from prme.retrieval.temporal import build_state, classify_page

    subject_key = normalize_assertion_value("subject", query.subject)
    predicate_key = normalize_assertion_value("predicate", query.predicate)
    node_types = tuple(query.node_types)
    found = await address_rows(
        engine, user_id=user_id, scope=query.scope, node_types=node_types,
        subject_key=subject_key, predicate_key=predicate_key,
    )
    if found is None:
        from prme.retrieval.temporal import get_assertion_state
        return await get_assertion_state(engine, query, user_id=user_id)
    page, total, unkeyed = found
    exclusions: Counter[str] = Counter()
    records, matched = classify_page(
        page, engine=engine, query=query,
        normalized_subject=subject_key, normalized_predicate=predicate_key,
        exclusions=exclusions,
    )
    # The two scan counts are assigned, not added: they describe the whole queried node type, and the address read
    # can only see this address. A page row always has a key, so the missing count classify_page made is zero.
    #
    # Both keys are dropped when the count is zero, because the current path only ever increments a counter it
    # reaches, so a zero is absent from its map rather than present as 0. The state reports the map as stored.
    for name, count in (("missing_structured_assertion", unkeyed),
                        ("selector_mismatch", total - unkeyed - matched)):
        if count:
            exclusions[name] = count
        else:
            exclusions.pop(name, None)
    return await build_state(
        engine, query, user_id=user_id, records=records,
        normalized_subject=subject_key, normalized_predicate=predicate_key,
        exclusions=exclusions, scanned_nodes=total, matched_records=matched,
    )
