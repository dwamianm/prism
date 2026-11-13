#!/bin/zsh
# Task 27 bar 5: determinism, receipt compatibility, routing and walk tests with PostgreSQL on.
# Postgres.app 16 on 127.0.0.1 uses trust auth, so the password in the URL is a placeholder, not a secret.
cd ~/Sites/prism-routing-cues
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= TOKENIZERS_PARALLELISM=false
export PRME_TEST_DATABASE_URL=postgresql://dwamianm:placeholder@127.0.0.1:5432/prme_task27
uv run --no-sync pytest -q -p no:cacheprovider tests/test_determinism_rebuild.py tests/test_packing_receipt_compatibility.py \
  tests/test_relation_routing.py tests/test_seed_walk.py tests/test_pg_seed_walk.py tests/test_belief_state.py \
  tests/test_graph_oracle.py tests/test_relationship_claims.py tests/test_privacy_source_routing.py > /tmp/task27/bar5-serial.log 2>&1
echo "bar5 exit $?"; tail -3 /tmp/task27/bar5-serial.log
