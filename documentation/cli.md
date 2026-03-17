# CLI Reference

PRME includes a command-line tool for setup, inspection, and maintenance.

## Setup Commands

### prme init

Initialize a new memory directory with the required structure.

```bash
prme init [directory]
```

- Creates the directory, `lexical_index/` subdirectory, and `.env.example`
- Default directory: `.` (current directory)
- Safe to run on existing directories

```bash
prme init ./my_memories
# → Initialized PRME memory directory at ./my_memories
```

### prme doctor

Check memory pack health and report issues.

```bash
prme doctor [directory]
```

Checks:
- Directory exists
- DuckDB database is valid and readable
- Vector index file exists
- Lexical index directory exists
- Node and event counts

```bash
prme doctor ./my_memories
# → [OK] Memory directory exists
# → [OK] DuckDB database valid (42 nodes, 85 events)
# → [OK] Vector index exists
# → [OK] Lexical index exists
# → 4 checks passed, 0 warnings, 0 failures
```

## Inspection Commands

### prme info

Show summary information about a memory pack.

```bash
prme info <db_path>
```

```bash
prme info ./my_memories/memory.duckdb
```

### prme nodes

List nodes with optional filters.

```bash
prme nodes <db_path> [--type TYPE] [--state STATE] [--limit N] [--format {table,json}]
```

| Flag | Description | Values |
|------|-------------|--------|
| `--type` | Filter by node type | entity, event, fact, decision, preference, task, summary, note, instruction |
| `--state` | Filter by lifecycle state | tentative, stable, contested, superseded, deprecated, archived |
| `--limit` | Max results | Default: 20 |
| `--format` | Output format | `table` (default) or `json` |

```bash
# All stable facts
prme nodes ./memory.duckdb --type fact --state stable

# All nodes as JSON
prme nodes ./memory.duckdb --format json --limit 100
```

### prme edges

List graph edges with optional filters.

```bash
prme edges <db_path> [--type TYPE] [--source ID] [--target ID] [--format {table,json}]
```

| Flag | Description | Values |
|------|-------------|--------|
| `--type` | Filter by edge type | relates_to, supersedes, derived_from, mentions, part_of, caused_by, supports, contradicts, has_fact |
| `--source` | Filter by source node ID | UUID |
| `--target` | Filter by target node ID | UUID |
| `--format` | Output format | `table` (default) or `json` |

```bash
# All supersedence edges
prme edges ./memory.duckdb --type supersedes
```

### prme node

Show detailed information about a single node.

```bash
prme node <db_path> <node_id>
```

```bash
prme node ./memory.duckdb 550e8400-e29b-41d4-a716-446655440000
```

### prme chain

Show the supersedence chain for a node — its history of being replaced or replacing other nodes.

```bash
prme chain <db_path> <node_id>
```

```bash
prme chain ./memory.duckdb 550e8400-e29b-41d4-a716-446655440000
```

### prme stats

Show detailed statistics: nodes by type, state, scope; edges by type; average confidence and salience.

```bash
prme stats <db_path> [--format {table,json}]
```

```bash
prme stats ./memory.duckdb
```

## Search

### prme search

Run the hybrid retrieval pipeline from the command line.

```bash
prme search <db_path> <query> [--user-id ID] [--format {table,json}]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--user-id` | User ID for scoping | `_cli` |
| `--format` | Output format | `table` |

```bash
prme search ./memory.duckdb "What programming languages does Alice use?"
prme search ./memory.duckdb "recent decisions" --user-id alice --format json
```

## Maintenance

### prme organize

Run organizer jobs manually.

```bash
prme organize <db_path> [--jobs JOB1,JOB2,...] [--budget-ms MS] [--format {table,json}]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--jobs` | Comma-separated job names | All jobs |
| `--budget-ms` | Time budget in ms | 5000 |

Available jobs: `promote`, `decay_sweep`, `archive`, `deduplicate`, `alias_resolve`, `summarize`, `feedback_apply`, `centrality_boost`, `tombstone_sweep`, `snapshot_generation`, `consolidate`.

```bash
# Run all jobs
prme organize ./memory.duckdb

# Run specific jobs with 10s budget
prme organize ./memory.duckdb --jobs promote,deduplicate --budget-ms 10000
```

## Export

### prme export

Export the entire memory pack as JSON.

```bash
prme export <db_path>
```

```bash
prme export ./memory.duckdb > backup.json
```
