# Aggregation coverage and regenerated-history stability

Natural-language count and list requests previously widened the candidate pool
but exposed no machine-readable statement about completeness. The supplementary
keyword path also used a fixed per-term limit while its comments described it as
exhaustive. A model reading the packed context could produce a confident complete
count after candidate generation, explicit selection, or token packing had
omitted records.

PRME now returns `RetrievalMetadata.aggregation_coverage` for detected count and
list queries. It deliberately reports `exhaustive=false` and records unique
candidates before selection, returned candidates, packed-context candidates,
stable limitation codes, and candidate paths observed at their configured cap.
The exact structure is retained in the retrieval operation and receipt execution
descriptor. Python, HTTP, and MCP expose the same data.

The packed context starts with a system-authored non-exhaustive warning counted
by the configured tokenizer. If the warning cannot fit, packing returns no
unqualified memory evidence. The alternate `format_for_llm()` renderer carries
the same boundary, including an empty candidate set. Complete stored-record
traversal remains the separate `scan_nodes()` / `iter_nodes()` contract; neither
API claims to resolve semantic qualification or real-world duplicate identity.

## Simulation defect found during verification

The first full simulation run after the coverage change scored 73/74. The failed
consolidation checkpoint did not involve aggregation: an unrelated Kubernetes
fact moved between ranks four and seven across regenerated runs. A ten-run
reproduction passed only 3/10. Greedy clustering began in generated UUID order,
so equivalent source histories selected different clusters and retirement sets.

Cluster discovery now orders by owner, scope, source time, and content, using UUID
only for exact ties. Centroid and extract selection use the same stable source
order after confidence. That made the rankings identical across ten regenerated
consolidation runs. A singular relational question such as “What infrastructure
does the team use?” now assigns its lexical relevance mass to semantic similarity
when no update evidence exists; generic literal subject/verb overlap no longer
swamps an answer-class paraphrase. Broad multi-answer questions containing
`and`/`or` retain hybrid lexical evidence. Both targeted consolidation and
surprise-gating scenarios then passed five consecutive runs.

The complete 19-scenario gate passed **74/74** twice with native exit zero and no
scenario errors. Report SHA-256 digests were
`cc3b4a1211cbed2da4e01ef4de49c53f171ec8386d70f801d283bf5361efea37`
and
`da29ecb3c202bdcd563b19587ed88a5f2f52d84145090f776b9a193cdbdbfbbb`.
The original consolidation failure report was
`0ea36391bae0affb0fee9aa67d39cd116cc5851722b65686cbdbb7eb907bf6bc`;
an intermediate broad semantic-answer rule exposed a surprise-gating regression
in report
`154c9b132a00afb705d22f568e7707b62860c7a76e003529a29f21e2e8118d19`
and was narrowed before final verification.

These authored checks establish the coverage contract and protect the observed
ranking failures. They do not establish exhaustive natural-language aggregation,
held-out answer accuracy, or competitive leadership.

The complete test suite also passed with live PostgreSQL and pgvector:
**3,279 passed, 93 skipped** in 511.33 seconds. A Python 3.13 wheel built as
`prme-0.11.0-py3-none-any.whl`, installed with resolved dependencies into a
fresh environment, and ran outside the repository. Its sync workflow stored two
records, returned aggregation coverage `(candidate=2, selected=1, context=1)`,
rendered the warning, and reopened the durable receipt successfully.
