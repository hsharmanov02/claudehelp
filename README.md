# Protocol Buyer Node

A node that earns from **protocol buyers**: programs that publish, in advance, what
they pay for and how payouts are decided. There is no customer to convince; the
node only has to do the work well enough to score.

The first buyer is the **Metaculus FutureEval bot tournament**. Bots forecast
real-world questions, and each season's prize pool (about $58k for Fall 2026,
28 Sep 2026 to 6 Jan 2027, plus a $1k MiniBench every two weeks) is split by a
published formula: your share grows with the square of your summed Peer score.
Entry is free, and the rules forbid human intervention, so the bot has to run on
its own.

Earnings are not guaranteed. In Q2 2025, 19 of 96 bots won a prize. Prizes are
paid after a season's questions resolve, so Fall 2026 money would arrive around
Feb–Mar 2027.

## What it does

Every 30 minutes, GitHub Actions runs the node:

1. **Checks its guards.** It stops if the kill switch is on or this month's model
   spend has reached the cap in `node.toml`.
2. **Collects open questions** from the current seasonal tournament and MiniBench,
   skipping ones it has already forecast and question types it doesn't support.
3. **Researches** each question with AskNews (if configured) and a web-search
   model, in parallel.
4. **Forecasts with three model families** (Anthropic, OpenAI, Google by default),
   rotating so each question gets an even spread, then combines them. Binary
   forecasts use the geometric mean of odds; multiple choice averages each option;
   numeric and date questions take the median distribution.
5. **Publishes** the forecast and its reasoning to Metaculus, if publishing is on.
6. **Writes everything to the ledger**: each forecast, error and dollar spent.

The prompts are Metaculus' own Fall 2026 template prompts. The node's edge comes
from the multi-vendor ensemble, better aggregation, combined research, and
reading answers without an extra model call.

## Setup

1. **Create the repo** from this code (private is fine) and push it to GitHub.
2. **Create a Metaculus bot account and token** at
   <https://www.metaculus.com/futureeval/participate/>, and fill in the
   participation form there. The form is also where you apply for free model
   credits (an OpenRouter key) and AskNews access.
3. **Add repository secrets** (Settings → Secrets and variables → Actions → Secrets):
   - `METACULUS_TOKEN`
   - `OPENROUTER_API_KEY`
   - optional: `ASKNEWS_CLIENT_ID`, `ASKNEWS_SECRET`
4. **Run "Test bot on the practice tournament"** from the Actions tab. It posts to
   Metaculus' bot-testing-area, which doesn't count. Check the forecasts appear on
   your bot's profile.
5. **Go live**: add a repository *variable* `NODE_PUBLISH` = `true`
   (Settings → Secrets and variables → Actions → Variables). Until then the
   30-minute runs are dry runs that forecast but don't post.

Private repos get 2,000 free Actions minutes a month; a run takes a few minutes,
so if you hit the limit, lower the schedule in `.github/workflows/forecast.yaml`
or make the repo public.

## Switches and limits

| What | Where | Effect |
| --- | --- | --- |
| Go live | variable `NODE_PUBLISH=true` | Real forecasts are posted |
| Kill switch | variable `NODE_KILL=true`, or a file named `KILL` | Every run exits before doing anything |
| Monthly cap | `[guard].monthly_budget_usd` in `node.toml` | No runs once tracked model spend reaches it |
| Per-run cap | `[guard].max_run_cost_usd` | Model calls are refused once a run reaches it |

Model spend is tracked per call. If a model is too new for the price list, it is
charged at the fallback rate in `node.toml` so the caps still hold.

## The ledger

Every run appends JSON lines to `ledger/YYYY-MM.jsonl` on the `node-data`
branch. Nothing is ever rewritten. Useful commands:

```bash
python -m node status        # kill switch, publishing, budget, last run
python -m node scoreboard    # this month's summary as JSON
python -m node models        # which models the node would use right now
```

A dashboard can read the ledger straight from the `node-data` branch.

## Improving it

`python -m node.improve.benchmark --questions 60` (or the "Benchmark variants"
workflow) scores variants of the settings against the community forecast on
open questions. It costs model money, so it only runs by hand. Change
`node.toml` only when a variant wins on a large sample.

## Rules the node follows

- One bot per person. Don't run a second copy with another account.
- No human edits to individual forecasts. Changing code or settings is fine.
- Bots are banned from human tournaments such as the Metaculus Cup; the config
  refuses them.
- Prize winners must pass Metaculus' identity and tax checks. That part is yours.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
cp .env.template .env        # then fill in keys for local runs
python -m node -v run metaculus --mode test   # dry run on the practice tournament
```
