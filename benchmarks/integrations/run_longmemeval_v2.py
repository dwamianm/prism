"""Run the pinned LongMemEval-V2 harness with durable reader checkpoints.

The upstream harness writes reader outputs only after every generation finishes
and while scoring begins.  A late evaluator failure can therefore discard hours
of successful reader work.  This launcher keeps initial prompt construction and
scoring intact, durably appends each reader result, and reuses those exact prompts
when resuming.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Sequence

from benchmarks.integrations.install_longmemeval_v2 import install


CHECKPOINT_SCHEMA_VERSION = 1
DEFAULT_CHECKPOINT_FILENAME = "reader_outputs.checkpoint.jsonl"
_READER_CONFIG_FIELDS = (
    "model",
    "base_url",
    "max_completion_tokens",
    "reasoning_effort",
    "temperature",
    "top_p",
    "presence_penalty",
    "top_k",
    "repetition_penalty",
    "reader_enable_thinking",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _request_descriptor(args: argparse.Namespace, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "question_id": row["question_id"],
        "messages": row["messages"],
        "reader": {field: getattr(args, field, None) for field in _READER_CONFIG_FIELDS},
    }


def _request_sha256(args: argparse.Namespace, row: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(_request_descriptor(args, row))).hexdigest()


def _repair_incomplete_tail(path: Path) -> None:
    """Repair only an unterminated final record left by an interrupted append."""
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    tail_start = data.rfind(b"\n") + 1
    try:
        json.loads(data[tail_start:])
    except (UnicodeDecodeError, json.JSONDecodeError):
        with path.open("r+b") as handle:
            handle.truncate(tail_start)
            handle.flush()
            os.fsync(handle.fileno())
        return
    with path.open("ab") as handle:
        handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_checkpoints(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise RuntimeError(f"reader checkpoint is not a file: {path}")
    _repair_incomplete_tail(path)

    checkpoints: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"invalid reader checkpoint JSON at {path}:{line_number}"
                ) from error
            if record.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported reader checkpoint schema at {path}:{line_number}"
                )
            question_id = record.get("question_id")
            request_sha256 = record.get("request_sha256")
            output = record.get("output")
            if not isinstance(question_id, str) or not isinstance(request_sha256, str):
                raise RuntimeError(
                    f"invalid reader checkpoint identity at {path}:{line_number}"
                )
            if not isinstance(output, dict):
                raise RuntimeError(
                    f"invalid reader checkpoint output at {path}:{line_number}"
                )
            if not isinstance(output.get("response_raw"), str):
                raise RuntimeError(
                    f"invalid reader response at {path}:{line_number}"
                )
            if not isinstance(output.get("response_parsed_boxed"), str):
                raise RuntimeError(
                    f"invalid parsed reader response at {path}:{line_number}"
                )
            if not isinstance(output.get("is_unknown"), bool) or not isinstance(
                output.get("usage"), dict
            ):
                raise RuntimeError(
                    f"invalid reader output metadata at {path}:{line_number}"
                )
            prior = checkpoints.get(question_id)
            if prior is not None and prior != record:
                raise RuntimeError(
                    f"conflicting reader checkpoints for question_id={question_id}"
                )
            checkpoints[question_id] = record
    return checkpoints


def _append_checkpoint(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(record) + b"\n"
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _load_prompt_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError(
            f"reader checkpoints exist but the original prompt rows are missing: {path}"
        )
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid prompt row JSON at {path}:{line_number}") from error
            question_id = row.get("question_id")
            if not isinstance(question_id, str) or not question_id:
                raise RuntimeError(f"invalid prompt row identity at {path}:{line_number}")
            if question_id in rows:
                raise RuntimeError(f"duplicate prompt row for question_id={question_id}")
            rows[question_id] = row
    return rows


def _resume_prompt_row(
    cached_rows: dict[str, dict[str, Any]],
    item: dict[str, Any],
    haystack_ids: list[str],
) -> dict[str, Any]:
    question_id = item["question_id"]
    cached = cached_rows.get(question_id)
    if cached is None:
        raise RuntimeError(f"original prompt row is missing question_id={question_id}")
    for field, value in item.items():
        if field == "query_invocation_id":
            continue
        if cached.get(field) != value:
            raise RuntimeError(
                f"original prompt row does not match current {field} for "
                f"question_id={question_id}"
            )
    if cached.get("haystack_ids") != haystack_ids:
        raise RuntimeError(
            f"original prompt row does not match the current haystack for "
            f"question_id={question_id}"
        )
    return cached


async def generate_reader_outputs_checkpointed(
    harness: ModuleType,
    args: argparse.Namespace,
    prompt_rows: list[dict[str, Any]],
    checkpoint_path: Path,
) -> dict[str, dict[str, Any]]:
    """Return reader outputs, resuming exact requests from a durable JSONL file."""
    harness.require(
        args.reader_max_concurrent_requests > 0,
        "reader_max_concurrent_requests must be positive",
    )
    rows_by_id = {row["question_id"]: row for row in prompt_rows}
    if len(rows_by_id) != len(prompt_rows):
        raise RuntimeError("prompt rows contain duplicate question IDs")

    checkpoints = _load_checkpoints(checkpoint_path)
    unknown_ids = sorted(set(checkpoints) - set(rows_by_id))
    if unknown_ids:
        raise RuntimeError(
            "reader checkpoint contains questions outside this run: "
            + ", ".join(unknown_ids[:5])
        )

    outputs: dict[str, dict[str, Any]] = {}
    pending_rows: list[dict[str, Any]] = []
    for question_id, row in rows_by_id.items():
        checkpoint = checkpoints.get(question_id)
        if checkpoint is None:
            pending_rows.append(row)
            continue
        expected_sha256 = _request_sha256(args, row)
        if checkpoint["request_sha256"] != expected_sha256:
            raise RuntimeError(
                "reader checkpoint does not match the prompt or reader settings for "
                f"question_id={question_id}; use a new output directory"
            )
        outputs[question_id] = checkpoint["output"]

    if not pending_rows:
        print(
            f"[reader-checkpoint] resumed {len(outputs)}/{len(prompt_rows)} outputs",
            flush=True,
        )
        return outputs

    client = harness.create_async_client(
        args.base_url,
        args.api_key_env,
        args.api_key_file,
    )
    semaphore = asyncio.Semaphore(args.reader_max_concurrent_requests)

    async def run_one(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            try:
                response_raw, usage = await harness.call_reader_model_async(
                    client,
                    args,
                    row["messages"],
                )
            except harness.BadRequestError as error:
                print(
                    f"Reader request failed for question_id={row['question_id']}: "
                    f"{error}. Using empty response and continuing.",
                    file=sys.stderr,
                    flush=True,
                )
                response_raw = ""
                usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        parsed_answer = harness.extract_boxed_answer(response_raw)
        output = {
            "response_raw": response_raw,
            "response_parsed_boxed": parsed_answer,
            "is_unknown": harness.is_unknown(parsed_answer),
            "usage": usage,
        }
        record = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "question_id": row["question_id"],
            "request_sha256": _request_sha256(args, row),
            "output": output,
        }
        _append_checkpoint(checkpoint_path, record)
        return row["question_id"], output

    tasks = [asyncio.create_task(run_one(row)) for row in pending_rows]
    print(
        f"[reader-checkpoint] resumed {len(outputs)}, generating {len(tasks)}",
        flush=True,
    )
    try:
        with harness.tqdm(total=len(tasks), desc="Generating", unit="q") as progress:
            for task in asyncio.as_completed(tasks):
                question_id, output = await task
                outputs[question_id] = output
                progress.update(1)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await client.close()
    return outputs


def _parse_launcher_args(argv: Sequence[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Run the pinned LongMemEval-V2 harness with resumable reader outputs. "
            "Place official harness arguments after '--'."
        )
    )
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument(
        "--reader-reasoning-effort",
        choices=["none", "low", "medium", "high"],
        default=None,
        help=(
            "override the reader reasoning effort, including Ollama's documented "
            "'none' setting"
        ),
    )
    parser.add_argument(
        "--checkpoint-filename",
        default=DEFAULT_CHECKPOINT_FILENAME,
        help="filename within the official --output-dir",
    )
    parsed, harness_args = parser.parse_known_args(argv)
    if harness_args and harness_args[0] == "--":
        harness_args = harness_args[1:]
    if not harness_args:
        parser.error("official harness arguments are required after '--'")
    checkpoint = Path(parsed.checkpoint_filename)
    if checkpoint.is_absolute() or checkpoint.name != parsed.checkpoint_filename:
        parser.error("--checkpoint-filename must be a plain filename")
    return parsed, harness_args


def run(argv: Sequence[str] | None = None) -> None:
    launcher_args, harness_args = _parse_launcher_args(argv)
    upstream_root = launcher_args.upstream_root.expanduser().resolve()
    install(upstream_root)

    upstream_path = str(upstream_root)
    sys.path.insert(0, upstream_path)
    previous_cwd = Path.cwd()
    previous_argv = sys.argv
    harness: ModuleType | None = None
    original_parse_args = None
    original_generate = None
    original_build_prompt = None
    original_build_per_question_prompt = None
    try:
        os.chdir(upstream_root)
        sys.argv = ["evaluation.harness", *harness_args]
        from evaluation import harness

        harness_path = Path(harness.__file__).resolve()
        if not harness_path.is_relative_to(upstream_root):
            raise RuntimeError(
                f"loaded LongMemEval-V2 harness from unexpected path: {harness_path}"
            )
        original_parse_args = harness.parse_args
        original_generate = harness.generate_all_reader_outputs
        original_build_prompt = harness.build_prompt_row
        original_build_per_question_prompt = harness.build_prompt_row_with_per_question_memory

        def parse_args_with_overrides() -> argparse.Namespace:
            args = original_parse_args()
            if launcher_args.reader_reasoning_effort is not None:
                args.reasoning_effort = launcher_args.reader_reasoning_effort
            return args

        async def checkpointed_generate(
            args: argparse.Namespace,
            prompt_rows: list[dict[str, Any]],
        ) -> dict[str, dict[str, Any]]:
            checkpoint_path = (
                Path(args.output_dir).resolve() / launcher_args.checkpoint_filename
            )
            return await generate_reader_outputs_checkpointed(
                harness,
                args,
                prompt_rows,
                checkpoint_path,
            )

        harness.parse_args = parse_args_with_overrides
        harness.generate_all_reader_outputs = checkpointed_generate

        preview_args = parse_args_with_overrides()
        checkpoint_path = (
            Path(preview_args.output_dir).resolve() / launcher_args.checkpoint_filename
        )
        checkpoints = _load_checkpoints(checkpoint_path)
        if checkpoints:
            cached_rows = _load_prompt_rows(
                Path(preview_args.output_dir).resolve() / "prompt_rows.jsonl"
            )
            for question_id, checkpoint in checkpoints.items():
                cached = cached_rows.get(question_id)
                if cached is None or checkpoint["request_sha256"] != _request_sha256(
                    preview_args, cached
                ):
                    raise RuntimeError(
                        "reader checkpoint does not match its original prompt for "
                        f"question_id={question_id}"
                    )

            def resume_shared_prompt(
                item: dict[str, Any],
                *,
                haystack_ids: list[str],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                return _resume_prompt_row(cached_rows, item, haystack_ids)

            def resume_per_question_prompt(
                item: dict[str, Any],
                *,
                haystack_ids: list[str],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                return _resume_prompt_row(cached_rows, item, haystack_ids)

            harness.build_prompt_row = resume_shared_prompt
            harness.build_prompt_row_with_per_question_memory = resume_per_question_prompt
            print(
                f"[reader-checkpoint] preserving {len(cached_rows)} original prompt rows",
                flush=True,
            )
        harness.main()
    finally:
        if harness is not None and original_parse_args is not None:
            harness.parse_args = original_parse_args
        if harness is not None and original_generate is not None:
            harness.generate_all_reader_outputs = original_generate
        if harness is not None and original_build_prompt is not None:
            harness.build_prompt_row = original_build_prompt
        if harness is not None and original_build_per_question_prompt is not None:
            harness.build_prompt_row_with_per_question_memory = original_build_per_question_prompt
        sys.argv = previous_argv
        os.chdir(previous_cwd)
        if sys.path and sys.path[0] == upstream_path:
            sys.path.pop(0)


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
