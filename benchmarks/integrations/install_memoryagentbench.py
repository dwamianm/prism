"""Install PRME's adapter into a pinned MemoryAgentBench checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from benchmarks.integrations.memoryagentbench import (
    DATASET_REVISION,
    UPSTREAM_REVISION,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_SOURCE = Path(__file__).with_name("memoryagentbench.py")
_CONFIG_SOURCE = Path(__file__).with_name("memoryagentbench_config.yaml")
_CONFIG_RELATIVE = Path(
    "configs/agent_conf/RAG_Agents/gpt-4o-mini/PRME_gpt-4o-mini.yaml"
)

_PATCHES = {
    "agent.py": (
        (
            "        self.temperature = agent_config.get('temperature', 0.0)\n",
            "        self.temperature = agent_config.get('temperature', 0.0)\n"
            "        self.reader_reasoning_effort = agent_config.get('reader_reasoning_effort')\n"
            "        if self.reader_reasoning_effort not in (None, 'none', 'low', 'medium', 'high'):\n"
            "            raise ValueError('reader_reasoning_effort must be none, low, medium, high, or omitted')\n"
            "        self.reader_seed = agent_config.get('reader_seed')\n"
            "        if isinstance(self.reader_seed, bool) or (self.reader_seed is not None and not isinstance(self.reader_seed, int)):\n"
            "            raise ValueError('reader_seed must be an integer or omitted')\n"
            "        self.reader_output_contract = str(agent_config.get('reader_output_contract', 'upstream')).strip()\n"
            "        if self.reader_output_contract not in ('upstream', 'numeric-label-v1', 'answer-only-v1', 'choice-only-v1'):\n"
            "            raise ValueError('invalid reader_output_contract')\n"
            "        self.retrieval_run_id = str(agent_config.get('retrieval_run_id', 'default'))\n"
            "        if (not self.retrieval_run_id or len(self.retrieval_run_id) > 64 or\n"
            "                any(not char.isalnum() and char not in '._-' for char in self.retrieval_run_id)):\n"
            "            raise ValueError('invalid retrieval_run_id')\n"
            "        self.memory_timestamp = agent_config.get('memory_timestamp')\n"
            "        if self.memory_timestamp is not None and (not isinstance(self.memory_timestamp, str) or not self.memory_timestamp):\n"
            "            raise ValueError('memory_timestamp must be a non-empty string or omitted')\n",
        ),
        (
            "        response = self._create_oai_client().chat.completions.create(\n"
            "            model=self.model,\n"
            "            messages=format_message,\n"
            "            temperature=self.temperature,\n"
            "            max_tokens=self.max_tokens if \"gpt-4\" in self.model else None\n"
            "        )\n",
            "        completion_options = {\n"
            "            'model': self.model,\n"
            "            'messages': format_message,\n"
            "            'temperature': self.temperature,\n"
            "            'max_tokens': self.max_tokens,\n"
            "        }\n"
            "        if self.reader_reasoning_effort is not None:\n"
            "            completion_options['reasoning_effort'] = self.reader_reasoning_effort\n"
            "        if self.reader_seed is not None:\n"
            "            completion_options['seed'] = self.reader_seed\n"
            "        response = self._create_oai_client().chat.completions.create(**completion_options)\n",
        ),
        (
            "        retrieval_query = self._extract_retrieval_query(message)\n",
            "        from methods.prme import _retrieval_query\n"
            "        retrieval_query = _retrieval_query(\n"
            "            message, upstream_query=self._extract_retrieval_query(message)\n"
            "        )\n",
        ),
        (
            "        ask_llm_message = retrieval_memory_string + \"\\n\" + message\n",
            "        from methods.prme import reader_message\n"
            "        ask_llm_message = reader_message(\n"
            "            retrieval_memory_string + \"\\n\" + message,\n"
            "            sub_dataset=self.sub_dataset,\n"
            "            contract=self.reader_output_contract,\n"
            "        )\n",
        ),
        (
            '        elif self._is_agent_type("zep"):\n'
            '            self._initialize_zep_agent(agent_config)\n'
            '        elif self._is_agent_type("knowl"):\n',
            '        elif self._is_agent_type("zep"):\n'
            '            self._initialize_zep_agent(agent_config)\n'
            '        elif self._is_agent_type("prme"):\n'
            '            from methods.prme import initialize_prme_agent\n'
            '            initialize_prme_agent(self, agent_config)\n'
            '        elif self._is_agent_type("knowl"):\n',
        ),
        (
            'for agent_type in ["letta", "cognee", "mem0", "zep", "knowl", "agentmemory"]',
            'for agent_type in ["letta", "cognee", "mem0", "zep", "prme", "knowl", "agentmemory"]',
        ),
        (
            '        elif self._is_agent_type("knowl"):\n'
            '            from methods.knowl import handle_knowl_agent\n',
            '        elif self._is_agent_type("prme"):\n'
            '            from methods.prme import handle_prme_agent\n'
            '            return handle_prme_agent(self, message, memorizing, query_id, context_id)\n'
            '        elif self._is_agent_type("knowl"):\n'
            '            from methods.knowl import handle_knowl_agent\n',
        ),
        (
            "            memorize_template = get_template(self.sub_dataset, 'memorize', self.agent_name)\n"
            "            formatted_message = memorize_template.format(context=message, **({'time_stamp': time.strftime(\"%Y-%m-%d %H:%M:%S\")} if '{time_stamp}' in memorize_template else {}))\n"
            "            self.context += \"\\n\" + formatted_message\n"
            "            self.context = self.context.strip()\n"
            "            self.chunks.append(formatted_message)\n"
            "            self.context_len = self.context_len + self.chunk_size\n",
            "            memorize_template = get_template(self.sub_dataset, 'memorize', self.agent_name)\n"
            "            timestamp = self.memory_timestamp or time.strftime(\"%Y-%m-%d %H:%M:%S\")\n"
            "            formatted_message = memorize_template.format(context=message, **({'time_stamp': timestamp} if '{time_stamp}' in memorize_template else {}))\n"
            "            self.context += \"\\n\" + formatted_message\n"
            "            self.context = self.context.strip()\n"
            "            self.chunks.append(formatted_message)\n"
            "            self.context_len = self.context_len + self.chunk_size\n",
        ),
        (
            "        bm25_documents = self.bm25_retriever.get_relevant_documents(retrieval_query)   \n",
            "        bm25_documents = self.bm25_retriever.invoke(retrieval_query)\n",
        ),
        (
            '        if output.get("retrieval_context"):\n'
            '            save_dir = f"./outputs/rag_retrieved/{self.agent_name}/k_{self.retrieve_num}/{self.sub_dataset}/chunksize_{self.chunk_size}/query_{query_id}_context_{context_id}.json"\n',
            '        if output.get("retrieval_context"):\n'
            '            save_dir = f"./outputs/rag_retrieved/{self.agent_name}/run_{self.retrieval_run_id}/k_{self.retrieve_num}/{self.sub_dataset}/chunksize_{self.chunk_size}/query_{query_id}_context_{context_id}.json"\n',
        ),
        (
            '        # Currently only implemented for Letta agents\n'
            '        if not self._is_agent_type("letta") and not self._is_agent_type("zep"):\n',
            '        if self._is_agent_type("prme"):\n'
            '            from methods.prme import save_prme_agent\n'
            '            save_prme_agent(self)\n'
            '            return\n'
            '        # Currently only implemented for Letta agents\n'
            '        if not self._is_agent_type("letta") and not self._is_agent_type("zep"):\n',
        ),
        (
            '        agent_save_folder = self.agent_save_to_folder\n'
            '        assert os.path.exists(agent_save_folder), f"Folder {agent_save_folder} does not exist."\n\n'
            '        if not self._is_agent_type("letta") and not self._is_agent_type("zep"):\n',
            '        agent_save_folder = self.agent_save_to_folder\n'
            '        assert os.path.exists(agent_save_folder), f"Folder {agent_save_folder} does not exist."\n\n'
            '        if self._is_agent_type("prme"):\n'
            '            from methods.prme import load_prme_agent\n'
            '            load_prme_agent(self)\n'
            '            return\n\n'
            '        if not self._is_agent_type("letta") and not self._is_agent_type("zep"):\n',
        ),
    ),
    "utils/eval_data_utils.py": (
        (
            'load_dataset(dataset_name, split=split_name, revision="main")',
            'load_dataset(dataset_name, split=split_name, revision="'
            + DATASET_REVISION
            + '")',
        ),
    ),
    "initialization.py": (
        (
            '    if any(agent_type in agent_name for agent_type in ["mem0", "cognee", "letta", "zep"]):\n'
            '        base_path = _generate_memory_agent_base_path(agent_config, dataset_config)\n'
            '        return f"{base_path}/exp_{current_context_index}"\n',
            '    if "prme" in agent_name:\n'
            '        run_id = str(agent_config.get("prme_run_id", "default"))\n'
            '        if (not run_id or len(run_id) > 64 or\n'
            '                any(not char.isalnum() and char not in "._-" for char in run_id)):\n'
            '            raise ValueError("invalid prme_run_id")\n'
            '        base_path = (f"./agents/prme_{dataset_config[\'sub_dataset\']}"\n'
            '                     f"_model{agent_config[\'model\']}_run{run_id}")\n'
            '        return f"{base_path}/exp_{current_context_index}"\n'
            '    elif any(agent_type in agent_name for agent_type in ["mem0", "cognee", "letta", "zep"]):\n'
            '        base_path = _generate_memory_agent_base_path(agent_config, dataset_config)\n'
            '        return f"{base_path}/exp_{current_context_index}"\n',
        ),
        (
            "        answer = (saved_data_entry['answer'][0] \n"
            "                 if isinstance(saved_data_entry['answer'], list) \n"
            "                 else saved_data_entry['answer'])\n",
            "        answer = saved_data_entry['answer']\n",
        ),
    ),
}


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


def _require_upstream_layout(root: Path) -> tuple[Path, Path]:
    agent = root / "agent.py"
    data = root / "utils" / "eval_data_utils.py"
    initialization = root / "initialization.py"
    if (
        not agent.is_file()
        or not data.is_file()
        or not initialization.is_file()
        or not (root / "methods").is_dir()
    ):
        raise RuntimeError(f"not a MemoryAgentBench checkout: {root}")
    return root / "methods" / "prme.py", root / _CONFIG_RELATIVE


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


def _patch_exact(path: Path, replacements: tuple[tuple[str, str], ...]) -> str:
    original = path.read_text(encoding="utf-8")
    updated = original
    changed = False
    for before, after in replacements:
        if after in updated:
            continue
        if updated.count(before) != 1:
            raise RuntimeError(f"unsupported or ambiguous upstream source at {path}")
        updated = updated.replace(before, after, 1)
        changed = True
    if not changed:
        return "unchanged"
    temporary = path.with_suffix(path.suffix + ".prme.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return "installed"


def install(
    upstream_root: Path, *, allow_revision_mismatch: bool = False
) -> dict[str, str]:
    """Install once or verify the exact adapter on the pinned upstream tree."""
    root = upstream_root.expanduser().resolve()
    adapter_path, config_path = _require_upstream_layout(root)
    revision = _checkout_revision(root)
    if revision != UPSTREAM_REVISION and not allow_revision_mismatch:
        raise RuntimeError(
            f"unsupported MemoryAgentBench revision {revision}; expected {UPSTREAM_REVISION}"
        )

    created: list[Path] = []
    originals: dict[Path, bytes] = {}
    try:
        adapter = _copy_exact(_ADAPTER_SOURCE, adapter_path)
        if adapter == "installed":
            created.append(adapter_path)
        config = _copy_exact(_CONFIG_SOURCE, config_path)
        if config == "installed":
            created.append(config_path)
        patches: list[str] = []
        for relative, replacements in _PATCHES.items():
            path = root / relative
            originals[path] = path.read_bytes()
            patches.append(_patch_exact(path, replacements))
    except BaseException:
        for path, content in originals.items():
            path.write_bytes(content)
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise

    return {
        "adapter": adapter,
        "config": config,
        "source_patches": (
            "installed" if any(status == "installed" for status in patches) else "unchanged"
        ),
        "upstream_revision": revision,
        "dataset_revision": DATASET_REVISION,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install PRME into a pinned MemoryAgentBench checkout"
    )
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument("--allow-revision-mismatch", action="store_true")
    args = parser.parse_args()
    result = install(
        args.upstream_root,
        allow_revision_mismatch=args.allow_revision_mismatch,
    )
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
