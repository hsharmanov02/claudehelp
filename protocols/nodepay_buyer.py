from protocols.base_protocol import BaseProtocolBuyer
class NodepayBuyerAdapter(BaseProtocolBuyer):
    def __init__(self):
        super().__init__("Nodepay", "Unverified reward integration", "Not integrated", "https://www.nodepay.ai/")

