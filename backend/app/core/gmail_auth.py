"""Gmail OAuth credential loading — read-only scope only.

Isolated here so the token read/refresh mechanics stay in one small module, the
same reasoning as `app/core/os_reveal.py` for the OS-specific "open a folder"
call. The one-time interactive authorization flow itself lives in
`scripts/gmail_authorize.py`, run manually from a terminal — nothing in the
running API server ever opens a browser or prompts for a login.
"""

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import settings
from app.core.errors import GmailNotConnected

# Read-only, and nothing broader. This integration only ever needs to list and
# read messages — never to send, label, modify, or delete anything in Gmail.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_credentials() -> Credentials:
    """Load the token produced by `scripts/gmail_authorize.py`, refreshing if needed.

    Raises `GmailNotConnected` if authorization has never been completed, or if
    the stored token can no longer be refreshed (e.g. access was revoked) —
    both cases point the caller at the same fix: run the script again.
    """
    token_path = settings.gmail_token_path_resolved
    if not token_path.is_file():
        raise GmailNotConnected()

    credentials = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        # Persist the refreshed access token so the next sync does not need to
        # refresh again before its own expiry.
        token_path.write_text(credentials.to_json())

    if not credentials.valid:
        raise GmailNotConnected()

    return credentials


def describe_local_status() -> dict[str, bool]:
    """Locally-determinable Gmail readiness — never a network call.

    Used by the `/status` endpoint. Answers exactly three booleans and nothing
    else: never the token, never the OAuth client secret, never a filesystem
    path. `Credentials.valid` / `.expired` / `.refresh_token` are all computed
    from fields already in the parsed token file (an expiry timestamp, a
    refresh token string) — none of them make an HTTP request.

    "connected" is a best-effort local read, not a guarantee the next sync will
    succeed: Google could still reject a refresh (revoked access) or the token
    could turn out to cover the wrong scopes. It only means "a token file exists
    and looks usable from here" — the same thing `load_credentials` would try
    before making any real API call.
    """
    credentials_configured = settings.gmail_credentials_path_resolved.is_file()
    token_path = settings.gmail_token_path_resolved
    token_present = token_path.is_file()

    connected = False
    if token_present:
        try:
            credentials = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            connected = credentials.valid or bool(credentials.refresh_token)
        except (ValueError, OSError):
            # Malformed or unreadable token file — a status check should report
            # "not connected", not raise.
            connected = False

    return {
        "credentials_configured": credentials_configured,
        "token_present": token_present,
        "connected": connected,
    }
