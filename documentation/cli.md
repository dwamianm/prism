# CLI Reference

PRME includes a command-line tool for setup, inspection, and maintenance.

Every command that takes `<db_path>` accepts either a directory pack's DuckDB
file (`memory.duckdb`) or a single-file pack, a file whose name ends in `.prme`
(see [single-file packs](../docs/INTEGRATION.md#single-file-packs)). These
commands open an encrypted pack with `PRME_ENCRYPTION_ENABLED` and
`PRME_ENCRYPTION_KEY` from the environment or a `.env` file, as an engine does.
`prme doctor`, `prme compact` and `prme migrate` also take
`--encryption-key KEY`, where an empty value means no key. Without it, for a
`.prme` pack, they use `PRME_ENCRYPTION_KEY` when `PRME_ENCRYPTION_ENABLED` is
true, which keeps the key off the command line. They read only those two
settings, from the environment and then a `.env` file, so an invalid setting
elsewhere does not stop them. A directory pack's `memory.duckdb` never takes
them, because for a directory pack they configure Fernet file encryption.

## Setup Commands

### prme init

Initialize a new memory directory, or create a single-file pack.

```bash
prme init [path]
```

- Default path: `.` (current directory)
- For a directory: creates the directory, its `lexical_index/` subdirectory and
  an annotated `.env.example`. Safe to run on existing directories
- For a path ending in `.prme`: creates exactly that one file, holding the
  schema, the vector snapshot and the pack manifest, and writes no
  `.env.example`; it prints a configuration hint instead. With
  `PRME_ENCRYPTION_ENABLED` and `PRME_ENCRYPTION_KEY` set, the new pack is
  encrypted. An existing pack is opened and checked, and a file that is not a
  pack makes the command fail and is left as it is

```bash
prme init ./my_memories
# → Initialized PRME memory directory: /path/to/my_memories

prme init ./memories.prme
# → Initialized PRME memory pack: /path/to/memories.prme
```

### prme doctor

Check memory pack health and report issues.

```bash
prme doctor [path] [--encryption-key KEY] [--verify-extraction] [--provider-timeout SECONDS]
```

For a memory directory, it checks that:
- The directory exists
- The DuckDB database opens and has the `events` and `nodes` tables
- The vector index file exists
- The lexical index directory exists

For a single-file pack (a path ending in `.prme`), it checks that:
- The file exists, and warns when a write-ahead log (`<pack>.wal`) is beside it
- DuckDB opens the file read only, with the key for an encrypted pack, and it
  has the `events`, `nodes`, `prme_manifest` and `vector_snapshot` tables
- The pack manifest is present, and prints its format version, PRME version,
  embedding model, and created and updated times
- The vector snapshot is stored, and warns when it is missing or holds a
  different number of vectors than the pack (the next open restores or
  reconciles it)

Both forms then report whether an extraction credential is configured.
`--verify-extraction` also contacts the configured model endpoint, without
generating or storing content, and waits up to `--provider-timeout` seconds
(default: 10).

```bash
prme doctor ./my_memories
# →   [OK]   Memory directory exists
# →   [OK]   DuckDB database valid (19 tables)
# →   [OK]   Vector index exists (1,792 bytes)
# →   [OK]   Lexical index exists
# →   [OK]   openai extraction credential configured (not verified)
# →   5/5 passed, 0 warnings, 0 failures

prme doctor ./memories.prme
# →   [OK]   Memory pack file exists (1,847,296 bytes)
# →   [OK]   DuckDB database valid (21 tables)
# →   [OK]   Pack manifest present
# →            Format version:   1
# →            ...
# →   [OK]   Vector snapshot stored (0 vectors, 112 bytes)
# →   [OK]   openai extraction credential configured (not verified)
# →   5/5 passed, 0 warnings, 0 failures
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

## Visual Memory

### prme observe

Remember what photos and videos showed (RFC-0018, phase 1).

```bash
prme observe <db_path> <file>... --user-id ID [--captured-at ISO8601] [--session-id ID]
             [--scope {personal,project,org}] [--model NAME] [--base-url URL]
             [--extract | --no-extract] [--no-thumbnails] [--format {table,json}]
```

Each file is observed in turn: a photo becomes one observation event, a video
one per keyframe that changed and whose description does not repeat the
previous kept frame's. The command turns `vision.enabled` on for its own run;
every other vision setting comes from `PRME_VISION_*` unless a flag overrides
it, and extraction (on by default) sends each description to the configured
extraction provider (`PRME_EXTRACTION_*`), which may be a hosted API; the
command names the provider it will use in its output.
`--captured-at` is the capture clock for every file given (a video's start);
without it the photo's EXIF time, the video's container time or the file time
stands in. A frame already observed for the owner by the same model and prompt
is skipped, so a second run over the same files adds nothing. Video needs
`ffmpeg`; a local Ollama server serving the vision model (`qwen3.5:9b` by
default) must be running. The command exits 1 when any file fails.

```bash
prme observe ./memory.duckdb ~/Pictures/receipt.jpg --user-id alice --no-extract
prme observe ./memories.prme meeting.mp4 --user-id alice --captured-at 2026-10-14T10:00:00-07:00 --format json
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

## Pack Files

### prme compact

Rewrite a closed pack file into a fresh copy that holds only live data. DuckDB
never gives freed space back to the file system, so a pack that has seen many
deletes or vector snapshot rewrites keeps the size of its largest past state.

```bash
prme compact <pack> [--encryption-key KEY] [--format {table,json}]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--encryption-key` | Key of an encrypted pack, in any form `PRME_ENCRYPTION_KEY` takes; an empty value means no key | For a `.prme` pack, `PRME_ENCRYPTION_KEY` when `PRME_ENCRYPTION_ENABLED` is true; none for a `memory.duckdb` |
| `--format` | Output format | `table` |

- `<pack>` is a single-file pack (`.prme`) or a directory pack's
  `memory.duckdb`. Rows, node IDs and vector keys do not change, so a directory
  pack's other files stay valid
- A `<pack>` given through a symbolic link compacts the file the link names,
  whose path the output reports, and the link stays in place. The new file
  keeps the pack's permission bits
- Copies every table, index and sequence into a hidden file beside the pack
  with `COPY FROM DATABASE`, then replaces the pack with one rename, so a
  failure leaves the original untouched
- Keeps the source's DuckDB storage version and, for an encrypted pack, its
  key and cipher
- Refuses while `<pack>.wal` exists, because the pack is open or was not closed
  cleanly. Close every program that uses the pack, or open and close it once
  (for example with `prme info`) to fold the log into the file. DuckDB's file
  lock also refuses a pack that another process has open

It prints the pack's path and its size in bytes before and after; the JSON
output has `path`, `bytes_before`, `bytes_after` and `encrypted`. On a copy of
a LoCoMo benchmark pack, it took a 743.5 MiB `memory.duckdb` to 510.5 MiB in
1.0 s.

```bash
prme compact ./memories.prme
prme compact ./my_memories/memory.duckdb --format json
```

### prme migrate

Copy a directory pack into a new single-file pack, leaving the directory in
place.

```bash
prme migrate <directory> <pack.prme> [--encryption-key KEY] [--format {table,json}]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--encryption-key` | Key of an encrypted directory pack, in any form `PRME_ENCRYPTION_KEY` takes. Whenever a key is given, the new pack is encrypted with it; an empty value means no key | `PRME_ENCRYPTION_KEY` when `PRME_ENCRYPTION_ENABLED` is true |
| `--format` | Output format | `table` |

- The new pack's name must end in `.prme`, and it must not exist yet: migrate
  never overwrites a file
- Opens the directory pack once with an engine, which folds in pending writes
  and backfills vector payloads from a legacy `vectors.usearch`, and copies its
  database through that engine's connection. A Fernet-encrypted directory pack
  needs its key and is encrypted again when that engine closes
- Builds the new pack in a hidden temporary file, opens it once to write the
  vector snapshot and the manifest, checks both, and only then moves it into
  place, so a failure never leaves a partial pack
- With a key, the new pack uses DuckDB's native encryption under that key, even
  when the directory pack was not encrypted. Because the key falls back to
  `PRME_ENCRYPTION_KEY`, migrating a plain directory pack while
  `PRME_ENCRYPTION_ENABLED` is true gives an encrypted pack. Without a key, the
  new pack is not encrypted

It prints the node, edge, event and vector counts of the new pack, the size of
the directory pack's files and of the new pack, and whether the new pack is
encrypted. The JSON output has `source`, `destination`, `nodes`, `edges`,
`events`, `vectors`, `bytes_before`, `bytes_after` and `encrypted`.

```bash
prme migrate ./my_memories ./memories.prme
# → Migrated memory pack: /path/to/my_memories
# →   New pack:      /path/to/memories.prme
# →   Nodes:         1
# →   Edges:         0
# →   Events:        1
# →   Vectors:       1
# →   Size before:   7,881,823 bytes (directory pack files)
# →   Size after:    8,925,184 bytes
# →   Encrypted:     no
# →   The directory pack was left in place.
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
