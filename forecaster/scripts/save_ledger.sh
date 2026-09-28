#!/usr/bin/env bash
# Commits whatever the run appended to the ledger and pushes it to `node-data`.
# Runs never overlap (the workflows share one concurrency group), so a push is
# retried only for network errors.
set -euo pipefail
if [ ! -d data/.git ] && [ ! -f data/.git ]; then
  echo "No ledger checkout; nothing to save."
  exit 0
fi
cd data
git add -A
if git diff --cached --quiet; then
  echo "Ledger unchanged."
  exit 0
fi
git commit -q -m "ledger: ${GITHUB_WORKFLOW:-local} run ${GITHUB_RUN_ID:-manual}"
for delay in 2 4 8 16; do
  if git push -q origin node-data; then
    echo "Ledger saved."
    exit 0
  fi
  sleep "$delay"
done
echo "Could not push the ledger after retries." >&2
exit 1
