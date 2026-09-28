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

Once you switch it on, GitHub Actions runs the node every hour:

1. **Checks its guards.** It stops if the kill switch is on or this month's model
   spend has reached the cap in `node.toml`.
2. **Collects open questions** from the current seasonal tournament and MiniBench,
   skipping ones the ledger shows it already forecast and question types it
   doesn't support.
3. **Researches** each question with AskNews (if configured) and a web-search
   model, in parallel.
4. **Forecasts with three model families** (Anthropic, OpenAI, Google by default),
   rotating so each question gets an even spread, then combines them. Binary
   forecasts use the geometric mean of odds; multiple choice averages each option;
   numeric and date questions take the median distribution.
5. **Publishes** the forecast and its reasoning to Metaculus, if publishing is on.
6. **Writes everything to the ledger**: each forecast, error and dollar spent.

The prompts are Metaculus' own Fall 2026 template prompts. The node's edge comes
from the multi-vendor ensemble, better aggregation and combined research. When a
model ends its answer in exactly the requested format, the node reads it
directly; anything else (units like "million", extra text) goes to a model-based
parser, as in the official template.

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
   hourly job is skipped and costs nothing.

**Actions minutes.** Keep the repo private: the ledger holds the bot's forecasts
on open questions, and in a public repo anyone could copy them. Private repos on
GitHub's free plan get 2,000 Actions minutes a month. The hourly schedule is
about 720 runs; a run with nothing to forecast takes under a minute (the Python
environment is cached), and forecasting adds a few minutes per new question, so
expect roughly 1,000 to 1,500 minutes a month. MiniBench questions stay open for
about three hours, so hourly runs get each one. Check usage under Settings →
Billing; if you have more minutes, `"11,41 * * * *"` in
`.github/workflows/forecast.yaml` runs every 30 minutes.

## Switches and limits

| What | Where | Effect |
| --- | --- | --- |
| Go live | variable `NODE_PUBLISH=true` | The hourly job runs and posts real forecasts |
| Kill switch | variable `NODE_KILL=true`, or a committed file named `KILL` | Jobs are skipped, or exit before doing anything |
| Monthly cap | `[guard].monthly_budget_usd` in `node.toml` | No runs once this month's recorded spend reaches it |
| Per-run cap | `[guard].max_run_cost_usd` | Model calls are refused once a run reaches it |
| Run time limit | `[guard].max_run_minutes` | A run stops, keeps what it finished, and leaves the rest for the next run |
| New season | `[metaculus].seasonal_tournament` | Points "seasonal" at a tournament slug or ID |

Every model call's cost goes into the ledger the moment the call returns, so a
cancelled or timed-out run still counts toward the monthly cap. If a model is too
new for the price list, it is charged at the fallback rate in `node.toml` (set to
the most expensive model's price) so the caps still hold. Calls already in flight
when a cap is reached still finish, so a cap can be exceeded by a few calls.

## The ledger

Every run appends JSON lines to `ledger/YYYY-MM.jsonl` on the `node-data`
branch: one `spend` line per model call, one `forecast` or `forecast_error` line
per question, and `run_started` / `run_finished` lines. Nothing is ever
rewritten. If the branch can't be read, the run stops rather than start from an
empty ledger. Useful commands:

```bash
python -m node status        # kill switch, publishing, budget, last run
python -m node scoreboard    # this month's summary as JSON
python -m node models        # which models the node would use right now
```

A dashboard can read the ledger straight from the `node-data` branch.

## Improving it

`python -m node.improve.benchmark --questions 10 --budget-usd 20` (or the
"Benchmark variants" workflow) scores variants of the settings against the
community forecast on open questions. Each variant gets an equal share of the
budget and time, and all are scored on the questions every variant finished. It
costs model money and counts toward the monthly cap, so it only runs by hand.
Ten questions is a smoke test; change `node.toml` only when a variant wins on a
large sample (100+ questions).

## Rules the node follows

- One bot per person. Don't run a second copy with another account.
- No human edits to individual forecasts. Changing code or settings is fine.
- Bots are banned from human tournaments such as the Metaculus Cup; the config
  refuses them.
- Every forecast needs its reasoning posted as a comment. If a forecast went up
  but its comment didn't, the ledger has no record of it and the next run
  forecasts the question again, posting both.
- Prize winners must pass Metaculus' identity and tax checks. That part is yours.

## Development

Everything below runs from this `forecaster/` folder; the rest of the repository
is the separate PC node. The workflows in `.github/workflows/` run here too.

```bash
cd forecaster
pip install -e ".[dev]"
python -m pytest
cp .env.template .env        # then fill in keys for local runs
python -m node -v run metaculus --mode test   # dry run on the practice tournament
```

Tests replace only the network request inside the forecasting library, so the
spending cap, cost accounting and parsers run for real.

