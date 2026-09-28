from protocols.base_protocol import BaseProtocolBuyer
class HoneygainBuyerAdapter(BaseProtocolBuyer):
    def __init__(self):
        super().__init__("Honeygain", "Demand-dependent bandwidth service", "Honeygain customers", "https://www.honeygain.com/")

