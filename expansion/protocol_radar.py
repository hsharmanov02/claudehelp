"""Sourced local shortlist. No claimed live discovery or automatic deployment."""
import json
from pathlib import Path
class ProtocolRadar:
    def candidates(self):
        path = Path(__file__).resolve().parents[1] / "research" / "protocol_catalog.json"
        return json.loads(path.read_text(encoding="utf-8"))["candidates"]
    async def scan_live_registries(self, session=None):
        return self.candidates()

