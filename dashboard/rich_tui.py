"""Small dependency-free terminal rendering of evidence state."""
def render_terminal_dashboard(state):
    value = state.get("totals", {}).get("wallet_reported_mined_xmr")
    return "Mode: " + state.get("mode", "unknown") + " | Wallet-reported mined XMR: " + (str(value) if value is not None else "unknown")

