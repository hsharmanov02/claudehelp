from protocols.base_protocol import BaseProtocolBuyer
class DawnBuyerAdapter(BaseProtocolBuyer):
    def __init__(self):
        super().__init__("DAWN", "Unverified reward integration", "Not integrated", "https://www.dawninternet.com/")

