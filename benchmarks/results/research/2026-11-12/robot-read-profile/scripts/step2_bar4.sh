#!/bin/zsh
# Task 22 step 2, bar 4: determinism and replay tests with the read profile default and robot.
set -u
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked PRME_EXTRACTION_API_KEY= \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 TOKENIZERS_PARALLELISM=false
cd /tmp/task22/robot
T=(tests/test_determinism_rebuild.py tests/test_packing_receipt_compatibility.py tests/test_score_provenance.py
   tests/test_rank_fusion.py tests/test_read_profile.py)
for profile in default robot; do
  PRME_READ_PROFILE=$profile /tmp/task22/stack/.venv/bin/python -m pytest $T -q -p no:cacheprovider \
    > /tmp/task22/reports-new/bar4-$profile.log 2>&1
  echo "bar4 $profile exit $?"
done
