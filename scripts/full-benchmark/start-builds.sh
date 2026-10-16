#!/bin/bash
# Prepare the worktree at the pinned commit, then start run-builds.sh detached,
# with caffeinate keeping the Mac awake. Closing the terminal does not stop it.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/settings.sh"

if [ ! -d "$WORKTREE" ]; then
  git -C "$PRISM" fetch -q origin || exit 1
  git -C "$PRISM" worktree add --detach "$WORKTREE" "$COMMIT" || exit 1
fi
if [ "$(git -C "$WORKTREE" rev-parse HEAD)" != "$COMMIT" ]; then
  echo "$WORKTREE exists but is not at ${COMMIT:0:8}; remove it (git -C $PRISM worktree remove $WORKTREE) and start again"
  exit 1
fi
(cd "$WORKTREE" && uv sync -q --dev --extra api --extra mcp) || exit 1

mkdir -p "$RESULTS_DIR"
nohup caffeinate -ims /bin/bash "$HERE/run-builds.sh" > "$RESULTS_DIR/driver.out" 2>&1 < /dev/null &
echo "Started the driver (pid $!). Progress: scripts/full-benchmark/status.sh"
echo "Log: $RESULTS_DIR/driver.log"
