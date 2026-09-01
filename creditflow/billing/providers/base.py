from abc import ABC, abstractmethod


class BillingProvider(ABC):
    @abstractmethod
    def create_checkout(self, payment, customer): ...

    @abstractmethod
    def get_payment_status(self, provider_payment_id): ...

    def cancel_subscription(self, external_subscription_id):
        raise NotImplementedError("This provider does not expose automatic recurring cancellation.")
