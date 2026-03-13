"""iCloud stub connector — no real API calls."""

from connectors.base import ConnectorAdapter, ConnectorSpec


class ICloudStubConnector(ConnectorAdapter):
    def __init__(self):
        super().__init__(
            ConnectorSpec(
                name="icloud",
                scopes=["reminders.readonly", "notes.readonly", "contacts.readonly"],
                permissions=["read"],
                version="0.1.0",
                description="iCloud stub (Reminders, Notes, Contacts)",
                author="vectrax",
            )
        )

    def auth_handshake(self, credentials=None) -> bool:
        return True

    def list_resources(self):
        return [
            {"id": "reminders-default", "name": "Reminders", "type": "reminders"},
            {"id": "notes-default", "name": "Notes", "type": "notes"},
            {"id": "contacts-all", "name": "All Contacts", "type": "contacts"},
        ]

    def healthcheck(self) -> bool:
        return True

    def test(self):
        return {
            "passed": True,
            "details": "iCloud stub: auth_handshake OK, list_resources OK",
        }
