#!/usr/bin/env bash
# Checks out the `node-data` branch into ./data so runs can read and extend the
# ledger. Creates the branch on first use, and only then: if GitHub can't be
# reached, the run stops rather than starting from an empty ledger, which would
# hide this month's spend from the budget check.
set -euo pipefail
git config user.name "protocol-buyer-node"
git config user.email "protocol-buyer-node@users.noreply.github.com"
status=0
git ls-remote --exit-code --heads origin node-data >/dev/null || status=$?
case "$status" in
  0)
    git fetch --depth=50 origin node-data:node-data
    git worktree add data node-data
    ;;
  2)
    echo "No node-data branch yet; starting a new ledger."
    git worktree add --orphan -b node-data data
    ;;
  *)
    echo "Could not check for the node-data branch (git exit $status); not running." >&2
    exit 1
    ;;
esac
mkdir -p data/ledger
