"""User-facing workflow messages."""

PUBLISH_BLOCKED = (
    "I stopped because the approval is no longer valid. Nothing was posted, sent or changed. "
    "Reconnect the app if needed, then press Retry to approve a fresh version."
)
GMAIL_NOT_CONNECTED = (
    "Gmail isn't connected yet. Connect it on the Connectors page, then press Retry."
)
CALENDAR_NOT_CONNECTED = (
    "Google Calendar isn't connected yet. Connect it on the Connectors page, then press Retry."
)
SHEETS_NOT_CONNECTED = (
    "Google Sheets isn't connected yet. Connect it on the Connectors page, then press Retry."
)
NOT_CONNECTED = (
    "LinkedIn isn't connected yet. Connect it on the Connectors page, then press Retry."
)
NO_PROFILE_ID = (
    "I couldn't read your LinkedIn member id, which is needed as the post author. "
    "Reconnect LinkedIn on the Connectors page, then press Retry."
)
