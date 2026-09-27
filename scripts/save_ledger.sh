#!/usr/bin/env bash
# Commits whatever the run appended to the ledger and pushes it to `node-data`.
# Retries on a push race, since runs are queued but a manual run can overlap.
set -euo pipefail
cd data
git add -A
if git diff --cached --quiet; then
  echo "Ledger unchanged."
  exit 0
fi
git commit -q -m "ledger: ${GITHUB_WORKFLOW:-local} run ${GITHUB_RUN_ID:-manual}"
for attempt in 1 2 3 4; do
  if git push -q origin node-data; then
    echo "Ledger saved."
    exit 0
  fi
  sleep $((attempt * 3))
  git pull -q --rebase origin node-data
done
echo "Could not push the ledger after 4 attempts." >&2
exit 1
