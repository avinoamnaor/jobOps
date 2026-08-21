"""One-time Gmail OAuth authorization for JobOps's read-only sync.

Run this ONCE from a terminal, from the backend/ directory:

    .venv\\Scripts\\python.exe scripts\\gmail_authorize.py

It opens your browser, asks you to sign in and approve READ-ONLY Gmail access,
then writes the resulting token to GMAIL_TOKEN_PATH (see .env). The running
FastAPI server never opens a browser or prompts for a login itself — this
script is the only place in the project that does.

One-time setup in Google Cloud Console is required first. Ask for the exact
step-by-step instructions if you have not done this yet — in short: create a
project, enable the Gmail API, create an OAuth client of type "Desktop app",
add yourself as a test user, and download the client JSON to the path
GMAIL_CREDENTIALS_PATH points at.

Re-running this script is safe: it simply produces a fresh token.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import settings
from app.core.gmail_auth import GMAIL_SCOPES


def main() -> None:
    credentials_path = settings.gmail_credentials_path_resolved
    token_path = settings.gmail_token_path_resolved

    if not credentials_path.is_file():
        print(f"Missing OAuth client file: {credentials_path}")
        print()
        print("Download it from Google Cloud Console (OAuth client, 'Desktop app'")
        print("type) and save it at that exact path — or point GMAIL_CREDENTIALS_PATH")
        print("in .env at wherever you saved it.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GMAIL_SCOPES)
    # Opens your browser and starts a temporary local server to receive the
    # OAuth redirect — Google's standard flow for a desktop/CLI application.
    credentials = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json())

    print(f"Gmail connected. Token saved to {token_path}")
    print("You can now call POST /integrations/gmail/sync.")


if __name__ == "__main__":
    main()
