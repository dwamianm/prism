"""Installer checks for the pinned MemoryAgentBench adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.integrations import install_memoryagentbench as installer


def upstream_layout(root: Path) -> None:
    (root / "methods").mkdir(parents=True)
    (root / "utils").mkdir()
    (root / "agent.py").write_text(
        """        self.temperature = agent_config.get('temperature', 0.0)

        response = self._create_oai_client().chat.completions.create(
            model=self.model,
            messages=format_message,
            temperature=self.temperature,
            max_tokens=self.max_tokens if \"gpt-4\" in self.model else None
        )

        elif self._is_agent_type(\"zep\"):
            self._initialize_zep_agent(agent_config)
        elif self._is_agent_type(\"knowl\"):
            from methods.knowl import initialize_knowl_agent

        elif any(self._is_agent_type(agent_type) for agent_type in [\"letta\", \"cognee\", \"mem0\", \"zep\", \"knowl\", \"agentmemory\"]):
            return self._handle_memory_agent(message, memorizing, query_id, context_id)

        elif self._is_agent_type(\"knowl\"):
            from methods.knowl import handle_knowl_agent
            return handle_knowl_agent(self, message, memorizing, query_id, context_id)

            memorize_template = get_template(self.sub_dataset, 'memorize', self.agent_name)
            formatted_message = memorize_template.format(context=message, **({'time_stamp': time.strftime(\"%Y-%m-%d %H:%M:%S\")} if '{time_stamp}' in memorize_template else {}))
            self.context += \"\\n\" + formatted_message
            self.context = self.context.strip()
            self.chunks.append(formatted_message)
            self.context_len = self.context_len + self.chunk_size

        BM25_LEGACY_CALL

        if output.get(\"retrieval_context\"):
            save_dir = f\"./outputs/rag_retrieved/{self.agent_name}/k_{self.retrieve_num}/{self.sub_dataset}/chunksize_{self.chunk_size}/query_{query_id}_context_{context_id}.json\"

        # Currently only implemented for Letta agents
        if not self._is_agent_type(\"letta\") and not self._is_agent_type(\"zep\"):
            return

        agent_save_folder = self.agent_save_to_folder
        assert os.path.exists(agent_save_folder), f\"Folder {agent_save_folder} does not exist.\"

        if not self._is_agent_type(\"letta\") and not self._is_agent_type(\"zep\"):
            return
""".replace(
            "        BM25_LEGACY_CALL\n",
            "        bm25_documents = self.bm25_retriever.get_relevant_documents(retrieval_query)   \n",
        ),
        encoding="utf-8",
    )
    (root / "utils" / "eval_data_utils.py").write_text(
        'raw_data = load_dataset(dataset_name, split=split_name, revision="main")\n',
        encoding="utf-8",
    )
    (root / "initialization.py").write_text(
        "        answer = (saved_data_entry['answer'][0] \n"
        "                 if isinstance(saved_data_entry['answer'], list) \n"
        "                 else saved_data_entry['answer'])\n\n"
        '    if any(agent_type in agent_name for agent_type in ["mem0", "cognee", "letta", "zep"]):\n'
        "        base_path = _generate_memory_agent_base_path(agent_config, dataset_config)\n"
        '        return f"{base_path}/exp_{current_context_index}"\n',
        encoding="utf-8",
    )


def test_installer_is_idempotent_and_pins_dataset(
    tmp_path: Path, monkeypatch
) -> None:
    upstream_layout(tmp_path)
    monkeypatch.setattr(
        installer, "_checkout_revision", lambda root: installer.UPSTREAM_REVISION
    )

    first = installer.install(tmp_path)
    second = installer.install(tmp_path)

    assert first["adapter"] == first["config"] == first["source_patches"] == "installed"
    assert second["adapter"] == second["config"] == second["source_patches"] == "unchanged"
    agent = (tmp_path / "agent.py").read_text(encoding="utf-8")
    assert agent.count("initialize_prme_agent") == 2
    assert agent.count("handle_prme_agent") == 2
    assert agent.count("save_prme_agent") == 2
    assert agent.count("load_prme_agent") == 2
    assert "reader_reasoning_effort" in agent
    assert "completion_options['seed']" in agent
    assert "'max_tokens': self.max_tokens" in agent
    assert "retrieval_run_id" in agent
    assert "memory_timestamp" in agent
    assert "/run_{self.retrieval_run_id}/" in agent
    assert "self.bm25_retriever.invoke(retrieval_query)" in agent
    assert "get_relevant_documents(retrieval_query)" not in agent
    data = (tmp_path / "utils" / "eval_data_utils.py").read_text(encoding="utf-8")
    assert f'revision="{installer.DATASET_REVISION}"' in data
    initialization = (tmp_path / "initialization.py").read_text(encoding="utf-8")
    assert "prme_{dataset_config['sub_dataset']}" in initialization
    assert "prme_run_id" in initialization
    assert "_run{run_id}" in initialization
    assert "answer = saved_data_entry['answer']" in initialization
    assert "saved_data_entry['answer'][0]" not in initialization


def test_installer_rejects_revision_drift_and_rolls_back(
    tmp_path: Path, monkeypatch
) -> None:
    upstream_layout(tmp_path)
    monkeypatch.setattr(installer, "_checkout_revision", lambda root: "drift")
    with pytest.raises(RuntimeError, match="unsupported MemoryAgentBench revision"):
        installer.install(tmp_path)

    monkeypatch.setattr(
        installer, "_checkout_revision", lambda root: installer.UPSTREAM_REVISION
    )
    original_agent = (tmp_path / "agent.py").read_bytes()
    (tmp_path / "utils" / "eval_data_utils.py").write_text("conflicting source\n")
    with pytest.raises(RuntimeError, match="unsupported or ambiguous upstream source"):
        installer.install(tmp_path)

    assert (tmp_path / "agent.py").read_bytes() == original_agent
    assert not (tmp_path / "methods" / "prme.py").exists()
    assert not (tmp_path / installer._CONFIG_RELATIVE).exists()
