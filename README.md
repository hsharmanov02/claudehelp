# Protocol Buyer Lab

A working local observer and a prepared, disabled CPU pilot for testing a zero-upfront-capital income hypothesis.

**Current outcome: no income demonstrated.** This is not a completed autonomous income business. No wallet is connected, no mining clients have been installed or started, and no paid service is configured.

## What changed

The previous prototype fetched public websites, incremented invented reward counters even after failed requests, and assigned invented dollar values. It did not authenticate to reward accounts, relay paid jobs, mine, or verify payments. Its archive is under `archive/unverified-prototype-*.zip`; none of those totals are financial evidence.

Those calculations and hardcoded IP/ISP/CPU/GPU claims have been removed. The dashboard now separates miner-reported work, wallet-reported unlocked mined receipts and unknown fiat profit.

## Best current hypothesis

**Monero proof of work with P2Pool is the closest mechanism match among the candidates reviewed.** Consensus issues XMR for valid blocks; a salesperson or customer order is not required to receive a mining reward. P2Pool distributes block rewards to eligible shares. A share can receive no payout; a short pilot can earn nothing. Exchanging XMR for cash still requires market liquidity and an acceptable conversion route.

This is zero token stake/purchase to enter using existing hardware, not zero electricity, bandwidth, storage, maintenance, or opportunity cost. Your observed CPU is an Intel i7-12700F with 20 logical processors. No hashrate, incremental power, payout or profitability has been measured here.

Golem is a second experiment if Linux/KVM and real requestor demand are available. Honeygain is a customer-funded bandwidth service. Neither eliminates demand uncertainty. Bittensor has a TAO registration cost and does not satisfy a strict zero-token-spend bootstrap. Nosana's entry costs and supported hardware remain unverified. Existing points-project stubs remain research only.

The sourced shortlist is visible in the dashboard and in [the catalog](research/protocol_catalog.json).

## Run the observer

Double-click **start_node.bat**. It uses an installed Python or the bundled Codex Python. Python 3.11+ is sufficient; there are **no package installs or paid APIs**.

Local dashboard: http://127.0.0.1:8899

From a Python environment:

```text
python node_engine.py
python node_engine.py --once
python node_engine.py --duration 60
python -m unittest discover -s tests -v
```

The default configuration makes no external network requests and launches no earning workloads. It reads the sourced local shortlist and periodically writes `node_state.json`. It does not install a startup task. Stop a foreground run with Ctrl+C. If launched in the background, use its recorded observer process ID; do not stop unrelated Python processes.

## Prepare a real pilot

The concrete launch gate requires:

1. A standard mainnet Monero receiving address that you control. Keep the seed and spend keys outside this project.
2. Official XMRig and P2Pool executables. Verify upstream release authenticity before setting each executable's SHA-256 in `config.json`. A local hash alone is not proof that a download is authentic. Review each installed version's flags before activation; the wrapper has not been exercised against real client binaries here.
3. An already-synced local mainnet Monero daemon at `127.0.0.1:18081`, with its ZMQ publisher at `127.0.0.1:18084`. Initial blockchain synchronization is a separate storage/bandwidth operation and is not performed by this program.
4. A local view-only Monero wallet RPC at `127.0.0.1:18083`, following that receiving address. Use RPC authentication; supply `MONERO_RPC_USER` and `MONERO_RPC_PASSWORD` through the local environment, never this chat. The observer only calls allowlisted read methods and stores no spend key.
5. An agreed electricity/resource budget. Set `monero.enabled` and `pilot.allow_cpu_work` to true only after these prerequisites are satisfied.

Then run `python node_engine.py --pilot`.

The wrapper checks binary hashes, mainnet sync, the receiving address and wallet match. It starts its own P2Pool mini and XMRig clients. The miner uses one idle-priority thread, light RandomX, no GPU backends, no MSR changes, pauses on battery and recent user activity, and has a maximum 30-minute pilot duration. Light mode intentionally sacrifices performance for lower memory use. XMRig's 1% development donation is retained. P2Pool also uses CPU/network resources; the one-thread limit is for mining, not total system load.

The clients stop at the pilot deadline, normal shutdown or client failure. Existing Monero daemon/wallet services are not stopped. Do not confuse a software time limit with a wattage limit. An OS crash or forced observer termination needs separate process supervision before unattended production use.

A 30-minute pilot validates setup and measured work, **not a promised payout**. P2Pool shares and block discovery are stochastic. No automatic purchases, withdrawals, swaps, deposits or paid scaling are implemented.

## Evidence and accounting

- Legacy protocol entries never request websites or award points.
- XMRig `/2/summary` provides reported hashrate and accepted shares; those never become a money balance.
- Wallet RPC `get_address` must match the configured recipient.
- `get_transfers` only counts entries labelled `block`, addressed to the configured recipient, not double-spent, unlocked and at least 60 confirmations deep.
- Amounts use integer atomic units. Complete snapshots deduplicate by transaction ID; repeat polling cannot compound the total. A removed/reorganized transfer disappears on the next successful snapshot.
- The value shown is **wallet-reported mined history**, which may include other workers or past periods. Set `history_from_height` to define the reporting range. It is not independently verified against the blockchain and is not a spendable wallet balance.
- Failed observations clear current evidence to unknown. Past snapshots are never reclassified as live income. The dashboard marks its connection stale when polling fails.
- Fiat profit remains unknown: no token-price estimate is presented as realized cash.

## Expansion path

The implemented rules produce evidence-dependent next actions. They do not claim to generate novel opportunities or deploy proposals.

The practical path is: establish work -> observe attributable receipts -> measure costs -> establish a usable conversion route -> compare additional permitted workloads on existing spare hardware. Only then consider reinvestment from realized proceeds with a separately defined budget. More process instances do not create more CPU capacity, bandwidth demand or protocol reward budget.

For planning, incremental electricity cost is `extra watts / 1000 × hours × tariff per kWh`. If gross realizable proceeds do not cover this and fees, unattended growth would scale a loss. Low income is acceptable for the first experiment; fabricated or inaccessible income is not.


## Sources checked on 27 September 2026

- [Monero mining mechanisms](https://www.getmonero.org/get-started/mining/)
- [P2Pool source and payout rules](https://github.com/SChernykh/p2pool)
- [Monero wallet RPC](https://docs.getmonero.org/rpc-library/wallet-rpc/)
- [XMRig configuration and options](https://xmrig.com/docs/miner/command-line-options)
- [Golem provider setup](https://docs.golem.network/docs/providers/provider-installation) and [demand/selection FAQ](https://docs.golem.network/docs/providers/provider-faq)
- [Honeygain traffic and demand](https://support.honeygain.com/hc/en-us/articles/360013231420-What-is-the-current-traffic-rate)
- [Bittensor registration costs](https://www.bittensor.com/docs/guides/mining/burn)
- [Nosana network description](https://www.nosana.com/terms-and-conditions/)

Tests cover accounting boundaries, invalid data, local RPC restrictions, disabled workload gates, hash pinning, normal time-limit cleanup and dashboard serving. They do not demonstrate mainnet earnings or test installed mining clients.

