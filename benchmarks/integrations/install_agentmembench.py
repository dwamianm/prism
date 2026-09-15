"""Install PRME's adapter into a pinned AgentMemBench checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from benchmarks.integrations.agentmembench import UPSTREAM_REVISION


_ADAPTER_SOURCE = Path(__file__).with_name("agentmembench.py")
_ADAPTER_RELATIVE = Path("agentmembench/prme_adapter.py")
_HARNESS_RELATIVE = Path("agentmembench/evaluation/unified_benchmark.py")

_ADAPTER_ANCHOR = (
    '    if system == "letta":\n'
    "        return LettaAdapter(config, collection)\n"
    '    raise ValueError(f"Unknown system: {system}")\n'
)
_ADAPTER_PATCH = (
    '    if system == "letta":\n'
    "        return LettaAdapter(config, collection)\n"
    '    if system == "prme":\n'
    "        from agentmembench.prme_adapter import PRMEAdapter\n"
    "        return PRMEAdapter(config, collection)\n"
    '    raise ValueError(f"Unknown system: {system}")\n'
)
_CHOICES_ANCHOR = (
    '        choices=("naive_rag", "mem0", "langmem", "graphiti", "letta"),\n'
)
_CHOICES_PATCH = (
    '        choices=("naive_rag", "mem0", "langmem", "graphiti", "letta", "prme"),\n'
)
_JUDGE_FAILURE_ANCHOR = (
    '                    parsed = json.loads(response.choices[0].message.content or "{}")\n'
    '                    return parsed.get("hit") is True\n'
    "                except Exception:\n"
    "                    if attempt == 2:\n"
    "                        return False\n"
    "                    await asyncio.sleep(2**attempt)\n"
    "        return False\n"
)
_JUDGE_FAILURE_PATCH = (
    '                    parsed = json.loads(response.choices[0].message.content or "{}")\n'
    '                    if not isinstance(parsed.get("hit"), bool):\n'
    '                        raise ValueError("retrieval judge returned no boolean hit")\n'
    '                    return bool(parsed["hit"])\n'
    "                except Exception as error:\n"
    "                    if attempt == 2:\n"
    "                        raise RuntimeError(\n"
    '                            "retrieval judge failed after retries"\n'
    "                        ) from error\n"
    "                    await asyncio.sleep(2**attempt)\n"
    '        raise AssertionError("unreachable retrieval judge state")\n'
)
_JUDGE_REASONING_ANCHOR = (
    "                        temperature=0,\n"
    "                        max_tokens=32,\n"
    '                        response_format={"type": "json_object"},\n'
)
_JUDGE_REASONING_PATCH = (
    "                        temperature=0,\n"
    '                        reasoning_effort="none",\n'
    "                        max_tokens=32,\n"
    '                        response_format={"type": "json_object"},\n'
)


def _checkout_revision(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            f"could not read the upstream Git revision at {root}"
        ) from error


def _require_layout(root: Path) -> tuple[Path, Path]:
    adapter = root / _ADAPTER_RELATIVE
    harness = root / _HARNESS_RELATIVE
    if not harness.is_file() or not (root / "agentmembench" / "config.py").is_file():
        raise RuntimeError(f"not an AgentMemBench checkout: {root}")
    return adapter, harness


def _copy_exact(source: Path, destination: Path) -> str:
    expected = source.read_bytes()
    if destination.exists():
        if destination.is_file() and destination.read_bytes() == expected:
            return "unchanged"
        raise RuntimeError(
            f"refusing to overwrite a conflicting file: {destination}; "
            "move or remove it before reinstalling"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(expected)
    try:
        shutil.copymode(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return "installed"


def _replace_exact(text: str, old: str, new: str, label: str) -> tuple[str, str]:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        return text.replace(old, new), "installed"
    if old_count == 0 and new_count == 1:
        return text, "unchanged"
    raise RuntimeError(
        f"ambiguous AgentMemBench {label} patch: "
        f"expected one source or installed fragment, found {old_count} and {new_count}"
    )


def install(
    upstream_root: Path,
    *,
    allow_revision_mismatch: bool = False,
) -> dict[str, str]:
    """Install once or verify an identical existing installation."""
    root = upstream_root.expanduser().resolve()
    adapter_path, harness_path = _require_layout(root)
    revision = _checkout_revision(root)
    if revision != UPSTREAM_REVISION and not allow_revision_mismatch:
        raise RuntimeError(
            f"unsupported AgentMemBench revision {revision}; "
            f"expected {UPSTREAM_REVISION}"
        )

    adapter_status = _copy_exact(_ADAPTER_SOURCE, adapter_path)
    original = harness_path.read_bytes()
    try:
        text = original.decode("utf-8")
        text, dispatch_status = _replace_exact(
            text, _ADAPTER_ANCHOR, _ADAPTER_PATCH, "adapter dispatch"
        )
        text, choices_status = _replace_exact(
            text, _CHOICES_ANCHOR, _CHOICES_PATCH, "CLI choices"
        )
        text, judge_status = _replace_exact(
            text,
            _JUDGE_FAILURE_ANCHOR,
            _JUDGE_FAILURE_PATCH,
            "retrieval judge failure policy",
        )
        text, reasoning_status = _replace_exact(
            text,
            _JUDGE_REASONING_ANCHOR,
            _JUDGE_REASONING_PATCH,
            "retrieval judge reasoning control",
        )
        if "installed" in (
            dispatch_status,
            choices_status,
            judge_status,
            reasoning_status,
        ):
            with tempfile.NamedTemporaryFile(
                dir=harness_path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(text.encode("utf-8"))
            try:
                shutil.copymode(harness_path, temporary)
                temporary.replace(harness_path)
            finally:
                temporary.unlink(missing_ok=True)
            harness_status = "installed"
        else:
            harness_status = "unchanged"
    except BaseException:
        harness_path.write_bytes(original)
        if adapter_status == "installed":
            adapter_path.unlink(missing_ok=True)
        raise

    return {
        "revision": revision,
        "adapter": adapter_status,
        "harness": harness_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install PRME's adapter into an AgentMemBench checkout."
    )
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument(
        "--allow-revision-mismatch",
        action="store_true",
        help="install into an unvalidated revision (results must disclose it)",
    )
    args = parser.parse_args()
    try:
        statuses = install(
            args.upstream_root,
            allow_revision_mismatch=args.allow_revision_mismatch,
        )
    except RuntimeError as error:
        parser.exit(2, f"error: {error}\n")
    print(f"AgentMemBench revision: {statuses['revision']}")
    print(f"Adapter: {statuses['adapter']}")
    print(f"Harness: {statuses['harness']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
