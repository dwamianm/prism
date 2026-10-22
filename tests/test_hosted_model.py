"""The hosted model baseline is an explicit opt in, counted and limited, and CI never runs it."""

import json
import sys
import types

import pytest

from benchmarks.diagnostics import chat_probe, extracted_packs, hosted_model
from benchmarks.fast_cycle import compare, cycle, ingest, store

RULES = compare.load_rules()
MODEL_ID = "example/hosted-model-id"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (hosted_model.FLAG, hosted_model.MODEL_ENV, hosted_model.REQUESTS_ENV, hosted_model.TOKENS_ENV,
                 hosted_model.LEDGER_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(hosted_model, "METER", hosted_model.Meter())


class TestTheFlag:
    def test_without_it_nothing_changes(self, tmp_path):
        assert not hosted_model.enabled()
        config = extracted_packs.build_config(tmp_path, {}, model="local-model")
        assert config.extraction.provider == "ollama" and config.extraction.model == "local-model"
        args = cycle.build_parser().parse_args(["run", "--label", "x", "--out", str(tmp_path)])
        assert (args.model, args.baseline, args.cache_dir) == (
            ingest.DEFAULT_MODEL, str(cycle.DEFAULT_BASELINE), ingest.CACHE_DIR)

    @pytest.mark.parametrize("value", ["", "0", "true", "yes"])
    def test_only_one_turns_it_on(self, monkeypatch, value):
        monkeypatch.setenv(hosted_model.FLAG, value)
        assert not hosted_model.enabled()

    def test_it_needs_the_model_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv(hosted_model.FLAG, "1")
        with pytest.raises(ValueError, match=hosted_model.MODEL_ENV):
            extracted_packs.model_identity("hosted")
        with pytest.raises(ValueError, match=hosted_model.MODEL_ENV):
            extracted_packs.build_config(tmp_path, {})

    def test_with_it_a_build_extracts_through_the_hosted_model_and_never_asks_ollama(self, monkeypatch, tmp_path):
        monkeypatch.setenv(hosted_model.FLAG, "1")
        monkeypatch.setenv(hosted_model.MODEL_ENV, MODEL_ID)
        monkeypatch.setattr(hosted_model, "install_meter", lambda: None)
        identity = extracted_packs.model_identity("hosted")
        assert identity["provider"] == "hosted" and MODEL_ID not in json.dumps(identity)
        # The id is read before the environment is cleared, and it is the engine's own provider and model.
        config = extracted_packs.build_config(tmp_path, {}, model="ignored")
        assert (config.extraction.provider, config.extraction.model) == ("bedrock", MODEL_ID)
        assert chat_probe.model_identity is extracted_packs.model_identity

    def test_with_it_the_cycle_defaults_to_the_hosted_baseline_and_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv(hosted_model.FLAG, "1")
        args = cycle.build_parser().parse_args(["run", "--label", "x", "--out", str(tmp_path)])
        assert (args.model, args.baseline, args.cache_dir) == (
            hosted_model.LABEL, str(cycle.HOSTED_BASELINE), cycle.HOSTED_CACHE_DIR)


class TestThePronounRepairSetting:
    """The benchmark caches hold a request for each setting they were measured with, so a build names it."""

    def test_a_harness_build_keeps_the_repair_off_unless_a_run_names_it(self, tmp_path):
        assert extracted_packs.gate_config(tmp_path).enable_pronoun_repair is False
        assert extracted_packs.build_config(tmp_path, {}, model="local-model").enable_pronoun_repair is False
        named = extracted_packs.build_config(tmp_path, {"enable_pronoun_repair": True}, model="local-model")
        assert named.enable_pronoun_repair is True

    def test_the_hosted_baseline_keeps_it_on_unless_a_run_names_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv(hosted_model.FLAG, "1")
        monkeypatch.setenv(hosted_model.MODEL_ENV, MODEL_ID)
        monkeypatch.setattr(hosted_model, "install_meter", lambda: None)
        assert extracted_packs.build_config(tmp_path, {}).enable_pronoun_repair is True
        assert extracted_packs.build_config(tmp_path, {"enable_pronoun_repair": False}).enable_pronoun_repair is False


class _Sdk:
    """The AWS SDK's runtime client, as far as the meter touches it."""

    def __init__(self, sent=1000, received=200):
        self.usage = {"inputTokens": sent, "outputTokens": received}
        self.requests = []

    def client(self, service_name, *args, **kwargs):
        sdk = self

        class Runtime:
            def converse(self, **request):
                sdk.requests.append(request)
                return {"usage": sdk.usage}

        return Runtime()


@pytest.fixture
def sdk(monkeypatch):
    fake = _Sdk()
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=fake.client))
    monkeypatch.setattr(hosted_model, "_installed", False)
    hosted_model.install_meter()
    import boto3

    return fake, boto3.client("bedrock-runtime")


REQUEST = {"messages": [{"role": "user", "content": [{"text": "Hello."}]}], "inferenceConfig": {"maxTokens": 100}}


class TestTheMeter:
    def test_every_request_is_counted_with_its_tokens_and_written_to_the_ledger(self, sdk, monkeypatch, tmp_path):
        fake, runtime = sdk
        ledger = tmp_path / "ledger.jsonl"
        monkeypatch.setenv(hosted_model.LEDGER_ENV, str(ledger))
        runtime.converse(**REQUEST)
        runtime.converse(**REQUEST)
        assert (hosted_model.METER.requests, hosted_model.METER.tokens) == (2, 2400)
        lines = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert [(line["tokens_in"], line["tokens_out"], line["requests"]) for line in lines] == [
            (1000, 200, 1), (1000, 200, 2)]
        assert len(fake.requests) == 2

    def test_a_request_past_the_request_limit_is_refused_before_it_is_sent(self, sdk, monkeypatch):
        fake, runtime = sdk
        monkeypatch.setenv(hosted_model.REQUESTS_ENV, "1")
        runtime.converse(**REQUEST)
        with pytest.raises(hosted_model.HostedBudgetExceeded):
            runtime.converse(**REQUEST)
        assert len(fake.requests) == 1

    def test_a_request_that_could_pass_the_token_limit_is_refused_before_it_is_sent(self, sdk, monkeypatch):
        fake, runtime = sdk
        monkeypatch.setenv(hosted_model.TOKENS_ENV, "1500")
        runtime.converse(**REQUEST)
        # 1,200 spent; the next could add its prompt and up to 100 more, past the limit.
        with pytest.raises(hosted_model.HostedBudgetExceeded):
            runtime.converse(**{**REQUEST, "inferenceConfig": {"maxTokens": 400}})
        assert len(fake.requests) == 1

    def test_a_request_without_an_output_limit_is_judged_at_the_largest_answer(self, sdk, monkeypatch):
        fake, runtime = sdk
        monkeypatch.setenv(hosted_model.TOKENS_ENV, "8000")
        with pytest.raises(hosted_model.HostedBudgetExceeded):
            runtime.converse(messages=REQUEST["messages"])
        assert fake.requests == []

    def test_the_limit_error_is_not_an_exception_a_handler_could_swallow(self):
        assert not issubclass(hosted_model.HostedBudgetExceeded, Exception)

    def test_without_the_sdk_the_error_says_how_to_get_it(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "boto3", None)
        monkeypatch.setattr(hosted_model, "_installed", False)
        with pytest.raises(RuntimeError, match="boto3"):
            hosted_model.install_meter()

    def test_the_progress_counter_reads_the_sdks_own_names(self):
        usage = extracted_packs._Usage.__new__(extracted_packs._Usage)
        usage.calls = usage.prompt_tokens = usage.completion_tokens = 0
        usage._record({"usage": {"inputTokens": 7, "outputTokens": 3}})
        assert usage.snapshot() == (1, 7, 3)


# --- The committed baseline ----------------------------------------------------------------------------


def test_the_committed_hosted_baseline_is_a_replay_that_matches_the_fixtures():
    baseline = json.loads(cycle.HOSTED_BASELINE.read_text())
    assert baseline["kind"] == compare.REPORT_KIND and set(baseline["stages"]) == {
        "store", "ingest", "store-extract", "reingest"}
    assert baseline["stages"]["store"]["fixture_sha256"] == store.fixture_digest()
    for stage in ("store-extract", "reingest"):
        entry = baseline["stages"][stage]
        assert entry["fixture_sha256"] == store.fixture_digest(), stage
        assert (entry["mode"], entry["model"], entry["live_calls"], entry["cache_hits"]) == (
            "replay", hosted_model.LABEL, 0, entry["calls"]), stage
    assert baseline["stages"]["ingest"]["mode"] == "replay" and baseline["stages"]["ingest"]["model"] == hosted_model.LABEL
    for name, chat in baseline["stages"]["ingest"]["conversations"].items():
        path = ingest.CONVERSATION_DIR / f"{name}.json"
        assert chat["conversation_sha256"] == chat_probe.conversation_digest(path), name
    assert compare.grouped(RULES, baseline["stages"]) == baseline["measures"]
    names = {compare.split_id(key)[1] for group in baseline["measures"].values() for key in group}
    assert names <= set(RULES["measures"])


def test_the_hosted_cache_holds_one_response_for_each_request_of_the_baseline():
    files, size = ingest.cache_size(cycle.HOSTED_CACHE_DIR)
    # 84 chat turns and 75 stored memories (the reingest stage asks the same requests), and the repair requests.
    assert sum(1 for _ in cycle.HOSTED_CACHE_DIR.rglob("*.batch.json")) == 159
    assert files == 159 + sum(1 for _ in cycle.HOSTED_CACHE_DIR.rglob("*.repair.json")) and size < 400_000
    usage = json.loads((cycle.HOSTED_CACHE_DIR / ingest.USAGE_FILE).read_text())
    assert {name: len(turns) for name, turns in usage.items() if name in {"defects-v1", "corrections-v1", "chat-v1"}} == {
        "defects-v1": 14, "corrections-v1": 16, "chat-v1": 54}
