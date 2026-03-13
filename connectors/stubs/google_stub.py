"""Google Workspace stub connector — no real API calls."""

from connectors.base import ConnectorAdapter, ConnectorSpec


class GoogleStubConnector(ConnectorAdapter):
    def __init__(self):
        super().__init__(
            ConnectorSpec(
                name="google_workspace",
                scopes=["calendar.readonly", "drive.readonly", "gmail.readonly"],
                permissions=["read"],
                version="0.1.0",
                description="Google Workspace stub (Calendar, Drive, Gmail)",
                author="vectrax",
            )
        )

    def auth_handshake(self, credentials=None) -> bool:
        # Stub: always succeeds
        return True

    def list_resources(self):
        return [
            {"id": "cal-1", "name": "Primary Calendar", "type": "calendar"},
            {"id": "drive-root", "name": "My Drive", "type": "drive"},
            {"id": "inbox", "name": "Inbox", "type": "gmail"},
        ]

    def healthcheck(self) -> bool:
        return True

    def test(self):
        return {
            "passed": True,
            "details": "Google stub: auth_handshake OK, list_resources OK",
        }
