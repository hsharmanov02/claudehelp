"""Evidence-dependent proposals; no automatic spending or claims that ideas were deployed."""
class IdeaExpander:
    def evaluate_and_expand(self, active_protocols, system_metrics):
        monero = next((p for p in active_protocols if p["name"] == "Monero / P2Pool"), {})
        work, payments = monero.get("work"), monero.get("payments")
        if not work:
            first = {"title": "Establish a real work signal", "status": "SETUP_REQUIRED",
                     "action": "Connect official local mining software and observe measured hashrate.",
                     "reason": "Website pings do not prove paid work."}
        elif not payments or not payments["receipts"]:
            first = {"title": "Observe the first received reward", "status": "WAIT_FOR_EVIDENCE",
                     "action": "Wait for unlocked mined receipts within the chosen resource budget.",
                     "reason": "Accepted shares do not guarantee a payout during a bounded pilot."}
        else:
            first = {"title": "Measure net proceeds before expansion", "status": "RESEARCH_ONLY",
                     "action": "Measure incremental power, actual conversion proceeds, fees and resource headroom.",
                     "reason": "Wallet history alone cannot attribute profit to this worker."}
        return [first, {"title": "Evaluate Golem separately", "status": "DEMAND_DEPENDENT",
                        "action": "Check Linux/KVM support, customer demand, settlement and withdrawal fees.",
                        "reason": "An automated marketplace still needs paying customers."},
                {"title": "Keep expansion spending at zero", "status": "POLICY_ACTIVE",
                 "action": "No paid APIs, deposits, cloud rentals, token purchases, automatic installs or transfers.",
                 "reason": "New opportunities stay proposals until cost and payment evidence are established."}]

