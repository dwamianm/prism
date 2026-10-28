# Contributing to PRME

Thanks for your interest in contributing! PRME is an early-stage project and contributions of all kinds are welcome — bug reports, documentation improvements, feature ideas, and code.

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Setup

```bash
git clone https://github.com/dwamianm/prism.git
cd prism

# Using uv (recommended)
uv sync --all-extras

# Or using pip
pip install -e ".[postgres,dev]"
```

### Running Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_retrieval_scoring.py

# With verbose output
pytest -v
```

The PostgreSQL variants skip unless `PRME_TEST_DATABASE_URL` points at a live
database. The repository's `docker-compose.yml` starts one for local testing,
the same way CI does:

```bash
docker compose up -d --wait && \
  PRME_TEST_DATABASE_URL=postgresql://prme_test:prme_test@127.0.0.1:5432/prme_test pytest
docker compose down
```

Port 5432 on `127.0.0.1` must be free: if another PostgreSQL already listens
there, `docker compose up` fails. The service uses fixed test credentials and
publishes the port on `127.0.0.1` only. Keep it that way: do not expose it on a
network address. If you created the container before the port was restricted
to loopback, run `docker compose up -d` again so Compose recreates it with the
new mapping (`docker compose ps` should show `127.0.0.1:5432->5432/tcp`).
Deployed databases need their own credentials (see
`documentation/deployment.md`).

### Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check
ruff check .
ruff format --check .

# Fix
ruff check --fix .
ruff format .
```

### Type Checking

```bash
mypy src/prme
```

## Making Changes

1. **Fork** the repository
2. **Create a branch** from `main` (`git checkout -b my-feature`)
3. **Make your changes** — write tests for new functionality
4. **Run the checks** — `pytest`, `ruff check`, `ruff format`
5. **Commit** with a clear message describing what and why
6. **Push** and open a **Pull Request**

### Commit Messages

Write clear, descriptive commit messages. No strict format enforced, but prefer:

```
Short summary (50 chars or less)

Longer explanation if needed. Wrap at 72 characters.
Explain *what* changed and *why*, not *how*.
```

### PR Guidelines

- Keep PRs focused — one feature or fix per PR
- Include tests for new functionality
- Update documentation if behavior changes
- Link to relevant issues if applicable

## Releases

There are two release channels. They differ in the evidence a release needs, not
in how it is published. An alpha or beta (`0.16.0a3`, `0.16.0b1`) is a GitHub
prerelease that is gated by the fast tiers. A stable release (`0.16.0`) also
needs the latest scheduled full benchmark run to show no loss against the
previous stable. `CLAUDE.md` ("Release channels and default changes") is the
rule: the gates, the commands and their times, and who may turn a default on
with which evidence. This section is the mechanics.

A release does not mark stored memories stale: `reingest()` compares a settings
fingerprint that carries `DERIVATION_VERSION` (`src/prme/ingestion/reingest.py`),
not the package version. Bump that integer by one in the pull request of a change
that alters what an unchanged source derives, such as the grounding checks, planning,
linking, supersedence or claim text rules, or the extraction schema, and do not
bump it with a release or for a change that leaves the derived records as they
were. `tests/test_derivation_version.py` fails when the records derived from a
recorded extraction change, and its message gives the refresh command for
`tests/fixtures/derivation/trip_derived.json`.

A release is a GitHub release whose tag is the version (for example `v0.15.0`)
and whose assets include the wheel, the source archive and `SHA256SUMS`.
Publishing the release runs `.github/workflows/publish.yml`, which checks both
files against `SHA256SUMS` and the tag and uploads them to
[PyPI](https://pypi.org/project/prme/) through trusted publishing, so no PyPI
token is stored anywhere. To publish an existing release again, run the
workflow by hand with its tag (Actions, Publish to PyPI, Run workflow). Files
PyPI already has are skipped. PyPI never accepts changed files for a version it
already has, so fix a bad upload with a new version.

### Cutting an alpha or beta

These are the steps the release steward took on 2026-10-19 for 0.16.0a1 to
0.16.0a3 (pull requests #302, #308 and #311). Use `aN` while the line still
takes default flips and `bN` once it is a candidate for the stable release.

1. **Merge what ships first.** Everything in the prerelease is already on `main`
   with green CI. A default turned on since the last prerelease went through its
   own pull request with the gate numbers (`CLAUDE.md`), not through the version
   bump.
2. **Open the version bump pull request** from `main` (for example branch
   `release-0-16-0a4`). Change the version in `pyproject.toml`,
   `src/prme/__init__.py` and `uv.lock` (run `uv lock`, then
   `uv lock --check --offline` must pass). In `CHANGELOG.md`, move the
   `[Unreleased]` entries under `## [0.16.0a4] - <date>`, add upgrade notes, and
   update the compare links at the bottom. Open the section with the alpha note:

   > This is an alpha. It passed CI, the fast gate and the fast cycle, and it has
   > not been through the full benchmark. The stable release stays at 0.15.0
   > until the full benchmark audit, so `pip install prme` still installs 0.15.0.
   > Install the alpha with `pip install --pre prme` or
   > `pip install prme==0.16.0a4`.

   Change no code, and leave `README.md`, `AGENTS.md` and `CLAUDE.md` alone: they
   name a release only when it is stable. The pull request body says what is new
   since the last prerelease and which checks the tree passed. Wait for the
   required CI checks (10 for 0.16.0a3: DuckDB and PostgreSQL on Python 3.11 to
   3.13, framework integrations, simulations and lint), then merge.
3. **Build from the merged commit**, in a clean checkout of it.
   `uv build --out-dir <dir>` writes `prme-<version>-py3-none-any.whl` and
   `prme-<version>.tar.gz`. The build backend is `uv_build`, and two builds of
   one commit gave identical bytes (checked for 0.16.0a1). Check that both files
   hold every file that `git ls-files src/prme` lists, byte for byte (188 files
   for 0.16.0a3). Write `VALIDATION.json` with the keys of the previous
   release's (download it from that release): the version, the release commit,
   the tested pull request head and whether its tree matches the release commit,
   the CI run and its jobs, `local_checks`, `provider_boundary` (with the alpha
   note: validated on CI, the fast gate and the fast cycle, not on the full
   benchmark), `compatibility` (the defaults changed since the last prerelease
   and the environment variable that restores each) and `packaging` (the size
   and SHA-256 of each artifact and of each committed file). Record what you ran
   and what you did not run, and never claim a check you skipped. Write
   `SHA256SUMS` with one line for the wheel, the source archive and
   `VALIDATION.json`, in the form of `sha256sum` (the hash, two spaces, the file
   name), which the publish workflow reads.
4. **Check the wheel.** In a fresh virtual environment, install the wheel and run
   the repository's tests against it with `src/` off the path (0.16.0a3: 20 test
   files, 594 passed and 3 skipped, with the PostgreSQL variants on a live
   database). Run a store, `ingest()` (with a stubbed extractor) and `retrieve()`
   smoke test too (Python 3.13 for 0.16.0a3). List the known flaky tests in the
   release notes. For 0.16.0a3 they were
   `tests/test_ingested_corrections.py::test_mcp_owner_declaration_is_strict_and_persisted`
   (a DuckDB segmentation fault while the engine closes) and
   `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`
   (it fails inside a git worktree).
5. **Create the prerelease** from the merge commit:

   ```bash
   gh release create v0.16.0a4 --repo dwamianm/prism --prerelease \
     --target <merge commit sha> --title "v0.16.0a4: <short headline> (alpha)" \
     --notes-file <notes.md> \
     prme-0.16.0a4-py3-none-any.whl prme-0.16.0a4.tar.gz SHA256SUMS VALIDATION.json
   ```

   The notes, in plain US English, follow the 0.16.0a3 release: what the release
   is (an alpha that passed CI, the fast gate and the fast cycle, not the full
   benchmark, with the stable version `pip install prme` still installs);
   Install (`pip install --pre prme` and `pip install prme==0.16.0a4`); What
   changed since the last prerelease (each option, what it does and the
   environment variable that restores the old behavior); Why these are on (the
   gate table, off against on); Known weaknesses; Known flaky tests; Validation
   (the CI run, the installed-wheel result, that the fast gate and the fast cycle
   are regression screens and not accuracy estimates, and that no full benchmark
   ran); and the compare link from the last prerelease.
6. **Watch the `Publish to PyPI` run**, which publishing the release starts
   (`gh run list --repo dwamianm/prism --workflow publish.yml`). It took 30 to 40
   seconds for 0.16.0a1 to 0.16.0a3. If it fails, run the workflow once by hand
   with the tag. A bad upload needs a new version, never a changed file.
7. **Check PyPI from a clean directory** outside the repository, with Python 3.11
   or newer:

   ```bash
   python -m pip index versions prme --pre   # lists 0.16.0a4 first, then the earlier versions
   python -m venv check && check/bin/pip install prme==0.16.0a4
   check/bin/python -c "import prme; print(prme.__version__)"   # 0.16.0a4
   python -m pip install --dry-run prme      # still the last stable: "Would install prme-0.15.0"
   ```

   Then run the store, `ingest()` (stubbed extractor) and `retrieve()` smoke test
   with `check/bin/python`. If any of these fails, the release is not done:
   report it.

### Cutting a stable release

A stable release is cut the same way, with these differences. The gates in
`CLAUDE.md` have passed (the latest scheduled full benchmark run shows no loss,
and the answer track audit is done for each `ingest()` default that is on), and
the notes link their evidence. Whether to cut one is the owner's decision. The
version has no suffix (`0.16.0`), and the `CHANGELOG.md` section covers
everything since the previous stable release and drops the alpha note.
`README.md`, `CLAUDE.md`, `AGENTS.md` and `memory_bank/GOALS.md` name the new
release, as they did for v0.15.0 (pull request #272), and the defaults that the audit passed
move from the alpha-channel list into the plain list of notable defaults. The
release is created without `--prerelease`, so GitHub shows it as the latest
release and `pip install prme` installs it.

## Reporting Bugs

Open a [GitHub Issue](https://github.com/dwamianm/prism/issues) with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS
- Relevant error output or logs

## Feature Requests

Open a [GitHub Issue](https://github.com/dwamianm/prism/issues) with the `enhancement` label. Describe the use case and why it would be valuable.

## Security

If you discover a security vulnerability, **do not** open a public issue. See [SECURITY.md](SECURITY.md) for responsible disclosure instructions.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
