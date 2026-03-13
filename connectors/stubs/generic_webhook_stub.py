"""Generic Webhook stub connector — no real API calls."""

from connectors.base import ConnectorAdapter, ConnectorSpec


class GenericWebhookStubConnector(ConnectorAdapter):
    def __init__(self):
        super().__init__(
            ConnectorSpec(
                name="generic_webhook",
                scopes=["webhook.send", "webhook.receive"],
                permissions=["read", "write"],
                version="0.1.0",
                description="Generic Webhook stub (inbound/outbound webhooks)",
                author="vectrax",
            )
        )

    def auth_handshake(self, credentials=None) -> bool:
        return True

    def list_resources(self):
        return [
            {"id": "wh-inbound", "name": "Inbound Webhook", "type": "webhook"},
            {"id": "wh-outbound", "name": "Outbound Webhook", "type": "webhook"},
        ]

    def healthcheck(self) -> bool:
        return True

    def test(self):
        return {
            "passed": True,
            "details": "Webhook stub: auth_handshake OK, list_resources OK",
        }
