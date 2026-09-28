"""Research placeholders cannot earn rewards or turn website requests into money."""
class BaseProtocolBuyer:
    def __init__(self, name, category, buyer_name, endpoint):
        self.name, self.category, self.buyer_name, self.endpoint = name, category, buyer_name, endpoint
        self.status = "RESEARCH_ONLY"

    async def initialize(self, session=None, config=None):
        self.status = "RESEARCH_ONLY"

    async def execute_heartbeat(self):
        return self.get_telemetry()

    async def poll_earnings(self):
        return {"amount": None, "evidence": "No authenticated earnings integration"}

    def get_telemetry(self):
        return {"name": self.name, "category": self.category, "buyer_name": self.buyer_name,
                "endpoint": self.endpoint, "status": self.status, "points_earned": None,
                "estimated_usd": None, "bytes_relayed": None, "packets_transmitted": 0,
                "reason": "Official client and payment evidence required. Not integrated."}

