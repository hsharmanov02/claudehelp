from protocols.base_protocol import BaseProtocolBuyer
class GradientBuyerAdapter(BaseProtocolBuyer):
    def __init__(self):
        super().__init__("Gradient", "Unverified reward integration", "Not integrated", "https://gradient.network/")
class BlessBuyerAdapter(BaseProtocolBuyer):
    def __init__(self):
        super().__init__("Bless", "Unverified reward integration", "Not integrated", "https://bless.network/")

