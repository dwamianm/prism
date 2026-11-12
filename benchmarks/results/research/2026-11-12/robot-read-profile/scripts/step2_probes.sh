#!/bin/zsh
# Task 22 step 2, bar 5: probe packs measured with no model, off and on, both memory modes, on read-profile-robot.
# Each side measures its own copy of the main build (rc-M) with the build's own settings plus the side's settings.
set -u
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked PRME_EXTRACTION_API_KEY= PRME_BENCH_CACHE_ONLY=1 \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 ANTHROPIC_BASE_URL=http://127.0.0.1:9 TOKENIZERS_PARALLELISM=false
PY=/tmp/task22/stack/.venv/bin/python
R=/tmp/task22/probes2
cd /tmp/task22/robot
for C in defects-v1 chat-v1 member-v1; do
  for side in classic-off:'{}' classic-on:'{"read_profile":"robot"}' \
              af-off:'{"memory_mode":"address_first"}' af-on:'{"memory_mode":"address_first","read_profile":"robot"}'; do
    name=${side%%:*}; extra=${side#*:}
    rm -rf $R/$C/$name; mkdir -p $R/$C
    cp -cR ~/Sites/prism/data/chat-probe-v1/$C/rc-M $R/$C/$name
    $PY - $R/$C/$name/build.json "$extra" <<'PYEOF'
import json, sys
path, extra = sys.argv[1], json.loads(sys.argv[2])
build = json.load(open(path)); build["overrides"] = {**build.get("overrides", {}), **extra}
json.dump(build, open(path, "w"), indent=1)
PYEOF
    PYTHONPATH=src:. $PY -m benchmarks.diagnostics.chat_probe --conversation benchmarks/conversations/$C.json \
       --root $R measure --label $name > $R/$C/$name.out 2>&1
    echo "probe $C $name exit $?"
  done
done
