"""Which graph nodes the derived search indexes leave out (dwamianm/prism#534).

The ``summarize`` organizer job publishes hierarchical daily, weekly and
monthly summary nodes that quote the most salient source text. Those summaries
must not compete with their own sources in default retrieval: a summary that
quotes several facts can take a lexical or semantic rank above the single
source that answers a narrow question, and adding its text changes BM25 corpus
statistics for every other candidate. The measurement on the member, chat and
defect probe packs is in dwamianm/prism#534.

So the default search indexes do not hold a hierarchical summary document:
the Tantivy index gets no lexical document, and the native USearch index gets
no vector (the staged vector, payload and metadata stay for recovery,
embedding coverage and privacy migration, which expect a vector per active
node). The graph, the node routes and ``GET /v1/nodes`` with a summary type
filter keep the summaries readable. Retrieval reaches them only through an
explicit ``include_node_types`` request, which reads them from the graph.

A summary written by the ``consolidate`` job is a different record: it
retires the sources it represents, so the default indexes keep it. Only the
hierarchical summaries of the ``summarize`` job are left out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from prme.types import NodeType

HIERARCHICAL_SUMMARY_KIND = "hierarchical_source_excerpts_v2"
SUMMARY_LEVELS = ("daily", "weekly", "monthly")


def is_unindexed_summary(
    node_type: str | NodeType,
    metadata: Mapping[str, Any] | None,
) -> bool:
    """Whether a node is a hierarchical ``summarize`` summary the indexes leave out.

    ``node_type`` accepts a ``NodeType`` or its string value. The current
    publication marks its summaries with ``summary_publication_kind``; a
    summary written before that marker names its level, period and format and
    carries no consolidation key.
    """
    value = getattr(node_type, "value", node_type)
    if value != "summary":
        return False
    meta = metadata or {}
    kind = meta.get("summary_publication_kind")
    if kind is not None:
        return kind == HIERARCHICAL_SUMMARY_KIND
    return (
        meta.get("consolidation_key") is None
        and meta.get("summarization_level") in SUMMARY_LEVELS
        and meta.get("summary_format") == "source-excerpts-v1"
        and meta.get("period_key") is not None
    )


def unindexed_summary_sql(table: str, *, dialect: str = "duckdb") -> str:
    """``is_unindexed_summary`` as a SQL condition on one table alias.

    The expression reads the node's metadata. On PostgreSQL pass
    ``dialect="postgres"``; the DuckDB form uses ``json_extract_string``.
    """
    if dialect == "postgres":
        kind = f"({table}.metadata->>'summary_publication_kind')"
        key = f"({table}.metadata->>'consolidation_key')"
        level = f"({table}.metadata->>'summarization_level')"
        summary_format = f"({table}.metadata->>'summary_format')"
        period = f"({table}.metadata->>'period_key')"
    else:
        kind = f"json_extract_string({table}.metadata, '$.summary_publication_kind')"
        key = f"json_extract_string({table}.metadata, '$.consolidation_key')"
        level = f"json_extract_string({table}.metadata, '$.summarization_level')"
        summary_format = f"json_extract_string({table}.metadata, '$.summary_format')"
        period = f"json_extract_string({table}.metadata, '$.period_key')"
    levels = ", ".join(f"'{value}'" for value in SUMMARY_LEVELS)
    # COALESCE keeps a summary with no metadata from making the whole
    # condition NULL, which would drop it from a NOT(...) filter.
    return (
        f"({table}.node_type = 'summary' AND COALESCE("
        f"{kind} = '{HIERARCHICAL_SUMMARY_KIND}' OR ("
        f"{key} IS NULL AND {level} IN ({levels}) "
        f"AND {summary_format} = 'source-excerpts-v1' AND {period} IS NOT NULL), FALSE))"
    )
