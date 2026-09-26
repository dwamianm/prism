# Coding memory development pilot, version 1

Registered before model answers on 2026-09-25. This is a small authored
development experiment on PRME, not an untouched quality confirmation.

## Question

Does automatically supplied PRME context improve a local coding agent's repair
of repository regressions when both arms can search the same documentation?

## Frozen inputs and arms

- Pin HEAD in the manifest. Read source and documents with `git show`, never
  from an evolving working tree.
- Import the twelve documents in `corpus.py` as complete consecutive passages.
  Retain exact text, paths, lines, commit and hashes. The corpus contains no
  fixtures, hidden checks, proposed repairs or trial outputs. Freeze it before
  any answer. This evaluates documentation memory, not accumulated debugging
  episodes.
- Four authored mutations replace existing functions: metadata admission,
  retrieval scope validation, legacy journal serialization, and assertion
  normalization. The implementation author selected these development tasks.
- Require every original to pass its separate regression checks and every
  mutation to fail them before any scored model call.
- Control gets the task, mutated source excerpt, public smoke checks, and
  read/search access to those documents plus the mutated target module.
- Memory gets the identical setup plus at most 2,048 tokens of PRME context,
  retrieved once with the unchanged task prompt. Reuse the exact context on
  repeats; no query tuning after viewing answers.
- Agents can read, search, replace the target function, run smoke checks and
  finish. They cannot read hidden checks, the gold function, another arm's
  output or the memory database. They cannot save memories during trials.

## Execution

Use installed local Ollama `qwen3.5:35b-a3b`: temperature 0, `think=false`,
32,768 context tokens, 2,048 generated tokens per action, six actions maximum.
Record full model manifest digest and Ollama version. No hosted or per-token
paid model is used. Each arm gets a fresh conversation. Seeds are 20260925 and
20260926 for two repeats; pair members share their seed.

Interleave pairs by task. Alternate the first arm using task index plus repeat
index. Warm the model before scoring. Recheck model/server identity before each
arm. Repeats are the same four tasks, not eight independent problems.

The host controller brokers constrained actions. Every smoke/final check runs
in a fresh Docker container with no network, a read-only root, unprivileged user,
dropped capabilities, PID limit, two CPUs, 1 GiB RAM and bounded temporary
filesystems. Mount only the candidate and check script, never host source, Git,
credentials or the Docker socket. Checks time out after 45 seconds. Ollama stays
on the host for Mac GPU acceleration. A separate PRME container has its own pack.
This constrained coding harness is not the full Codex or Claude Code product.

## Measurements and failure policy

Primary success requires all separate regression checks to pass. Retain paired
gains/losses, test/search/read actions, model input/output tokens and elapsed
time. Report PRME preparation/retrieval timing separately; agent time excludes
setup/import. Added memory counts in input totals. Provider failures count as
unsuccessful and remain explicit; never replace arms or choose the best repeat.
Invalid actions consume a slot and receive an error. No provider retry.

Save manifest, corpus, imports, retrieval, prompts, responses, edits, checks and
partial results. Execute the recorded image digest. Incomplete trials remain
incomplete, rather than becoming zero-score completed experiments. Report all
tasks/repeats. No promotion gate, significance claim or retrieval-default change
follows from this pilot. Positive results motivate fresh tasks and full-agent
evaluation. Negative/null results remain. Do not change a started trial's
corpus, tasks, settings or checks to improve its results.
