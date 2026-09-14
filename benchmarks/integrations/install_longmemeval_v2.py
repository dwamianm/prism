"""Install PRME's adapter into a pinned LongMemEval-V2 checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from benchmarks.integrations.longmemeval_v2 import UPSTREAM_REVISION


_IMPORT_LINE = "from .prme import PRMEMemory  # noqa: F401"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_SOURCE = Path(__file__).with_name("longmemeval_v2.py")
_CONFIG_SOURCE = Path(__file__).with_name("longmemeval_v2_config.json")


def _checkout_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"could not read the upstream Git revision at {root}") from error
    return result.stdout.strip()


def _require_upstream_layout(root: Path) -> tuple[Path, Path, Path]:
    memory_dir = root / "memory_modules"
    init_path = memory_dir / "__init__.py"
    config_dir = root / "evaluation" / "memory_configs"
    if not (memory_dir / "memory.py").is_file() or not init_path.is_file():
        raise RuntimeError(f"not a LongMemEval-V2 checkout: {root}")
    if not config_dir.is_dir():
        raise RuntimeError(f"missing evaluation/memory_configs in {root}")
    return memory_dir / "prme.py", config_dir / "prme.json", init_path


def _copy_exact(source: Path, destination: Path) -> str:
    expected = source.read_bytes()
    if destination.exists():
        if not destination.is_file():
            raise RuntimeError(f"adapter destination is not a file: {destination}")
        if destination.read_bytes() == expected:
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


def install(upstream_root: Path, *, allow_revision_mismatch: bool = False) -> dict[str, str]:
    """Install once or verify an identical existing installation."""
    root = upstream_root.expanduser().resolve()
    adapter_path, config_path, init_path = _require_upstream_layout(root)
    revision = _checkout_revision(root)
    if revision != UPSTREAM_REVISION and not allow_revision_mismatch:
        raise RuntimeError(
            f"unsupported LongMemEval-V2 revision {revision}; expected {UPSTREAM_REVISION}"
        )

    adapter_status = _copy_exact(_ADAPTER_SOURCE, adapter_path)
    try:
        config_status = _copy_exact(_CONFIG_SOURCE, config_path)
    except BaseException:
        if adapter_status == "installed":
            adapter_path.unlink(missing_ok=True)
        raise

    init_text = init_path.read_text(encoding="utf-8")
    import_status = "unchanged"
    if _IMPORT_LINE not in init_text.splitlines():
        suffix = "" if not init_text or init_text.endswith("\n") else "\n"
        updated = f"{init_text}{suffix}{_IMPORT_LINE}\n"
        temporary = init_path.with_suffix(".py.tmp")
        try:
            temporary.write_text(updated, encoding="utf-8")
            temporary.replace(init_path)
        finally:
            temporary.unlink(missing_ok=True)
        import_status = "installed"

    return {
        "revision": revision,
        "adapter": adapter_status,
        "config": config_status,
        "registry_import": import_status,
        "project_root": str(_PROJECT_ROOT),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install PRME's adapter into a LongMemEval-V2 checkout."
    )
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument(
        "--allow-revision-mismatch",
        action="store_true",
        help="install into an unvalidated upstream revision (results must disclose it)",
    )
    args = parser.parse_args()
    try:
        statuses = install(
            args.upstream_root,
            allow_revision_mismatch=args.allow_revision_mismatch,
        )
    except RuntimeError as error:
        parser.exit(2, f"error: {error}\n")
    print(f"LongMemEval-V2 revision: {statuses['revision']}")
    print(f"Adapter: {statuses['adapter']}")
    print(f"Configuration: {statuses['config']}")
    print(f"Registry import: {statuses['registry_import']}")
    print(f"Install PRME from: {statuses['project_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
