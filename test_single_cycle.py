"""One actual observer cycle, no external workloads or invented rewards."""
from node_engine import AutoProtocolNodeEngine
if __name__ == "__main__":
    result = AutoProtocolNodeEngine().cycle()
    print("Observation complete.")
    print("Wallet-reported mined XMR:", result["totals"]["wallet_reported_mined_xmr"])
    print("Fiat profit:", result["totals"]["fiat_profit"])
    print("Unknown is not zero. No income is inferred from connectivity.")

