#!/usr/bin/env bash
# Checks out the `node-data` branch into ./data so runs can read and extend the
# ledger. Creates the branch on first use.
set -euo pipefail
git config user.name "protocol-buyer-node"
git config user.email "protocol-buyer-node@users.noreply.github.com"
if git ls-remote --exit-code --heads origin node-data >/dev/null 2>&1; then
  git fetch --depth=50 origin node-data:node-data
  git worktree add data node-data
else
  git worktree add --orphan -b node-data data
fi
mkdir -p data/ledger
