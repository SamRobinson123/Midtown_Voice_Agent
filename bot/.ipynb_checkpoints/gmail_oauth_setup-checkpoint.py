# gmail_oauth_setup.py  ▸ run once to create gmail_token.json
from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

flow  = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)     # browser opens → choose Gmail → Allow
Path("gmail_token.json").write_text(creds.to_json())
print("✅  Saved gmail_token.json – you can close this window.")
