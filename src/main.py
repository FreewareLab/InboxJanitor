from pathlib import Path
from email.utils import parseaddr
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"


def get_gmail_service():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE,
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def list_message_ids(service, query):
    results = []
    page_token = None

    while True:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=500,
                pageToken=page_token,
            )
            .execute()
        )

        results.extend(response.get("messages", []))

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return results


def count_query(service, query):
    return len(list_message_ids(service, query))


def get_header(headers, name):
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def get_message_details(service, message_id):
    retries = 0

    while True:
        try:
            return (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=[
                        "From",
                        "Subject",
                        "Date",
                        "List-Unsubscribe",
                        "List-Unsubscribe-Post",
                    ],
                )
                .execute()
            )

        except HttpError as exc:
            text = str(exc)
            status = getattr(exc.resp, "status", None)

            if status in (403, 429) and (
                "rateLimitExceeded" in text
                or "Quota exceeded" in text
            ):
                retries += 1
                wait_seconds = min(10 * retries, 60)

                print(
                    f"\nGmail asked us to slow down. "
                    f"Waiting {wait_seconds} seconds..."
                )

                time.sleep(wait_seconds)
                continue

            raise


def clean_sender(raw_sender):
    name, email = parseaddr(raw_sender)

    if name:
        return name

    if email:
        return email

    return raw_sender or "Unknown sender"


def main():
    print()
    print("=" * 58)
    print("                     INBOX JANITOR")
    print("              Freeware Lab Experiment #001")
    print("=" * 58)
    print()
    print("Mode: READ ONLY")
    print("Quick Audit engaged.")
    print("No full-mailbox crawl required.")
    print()

    service = get_gmail_service()

    profile = service.users().getProfile(userId="me").execute()

    print(f"Account:  {profile.get('emailAddress')}")
    print(f"Messages: {profile.get('messagesTotal', 0):,}")
    print()

    audits = [
        ("Larger than 10 MB", "larger:10M"),
        ("Larger than 5 MB", "larger:5M"),
        ("Has attachments", "has:attachment"),
        ("Older than 5 years", "older_than:5y"),
        ("Older than 3 years", "older_than:3y"),
        ("Promotions", "category:promotions"),
        ("Social", "category:social"),
        ("Spam", "in:spam"),
        ("Trash", "in:trash"),
        ("Unread", "is:unread"),
    ]

    print("QUICK AUDIT")
    print("-" * 58)

    audit_results = {}

    for label, query in audits:
        print(f"Checking {label}...", end=" ", flush=True)

        count = count_query(service, query)
        audit_results[label] = count

        print(f"{count:,}")

    print()
    print("=" * 58)
    print("BIG STUFF ANALYSIS")
    print("=" * 58)
    print()

    big_message_ids = list_message_ids(service, "larger:5M")

    print(
        f"Found {len(big_message_ids):,} messages larger than 5 MB."
    )
    print("Inspecting only those messages...")
    print()

    big_messages = []

    for number, item in enumerate(big_message_ids, start=1):
        message = get_message_details(service, item["id"])

        payload = message.get("payload", {})
        headers = payload.get("headers", [])

        sender_raw = get_header(headers, "From")
        subject = get_header(headers, "Subject")
        unsubscribe = get_header(headers, "List-Unsubscribe")
        unsubscribe_post = get_header(
            headers,
            "List-Unsubscribe-Post",
        )

        size_bytes = message.get("sizeEstimate", 0)

        big_messages.append({
            "sender": clean_sender(sender_raw),
            "subject": subject or "(No subject)",
            "size": size_bytes,
            "unsubscribe": bool(unsubscribe),
            "one_click": "one-click" in unsubscribe_post.lower(),
        })

        print(
            f"Inspecting {number:,} / "
            f"{len(big_message_ids):,}",
            end="\r",
            flush=True,
        )

    print()
    print()

    big_messages.sort(
        key=lambda item: item["size"],
        reverse=True,
    )

    total_big_bytes = sum(
        item["size"]
        for item in big_messages
    )

    unsubscribe_count = sum(
        1
        for item in big_messages
        if item["unsubscribe"]
    )

    one_click_count = sum(
        1
        for item in big_messages
        if item["one_click"]
    )

    print("=" * 58)
    print("JANITOR REPORT")
    print("=" * 58)
    print()

    for label, _ in audits:
        print(
            f"{label:<35}"
            f"{audit_results[label]:>10,}"
        )

    print()
    print(
        f"Estimated size of >5 MB mail: "
        f"{total_big_bytes / 1024 / 1024:.1f} MB"
    )

    print(
        f"Large mail with unsubscribe:  "
        f"{unsubscribe_count:,}"
    )

    print(
        f"Large mail with one-click:     "
        f"{one_click_count:,}"
    )

    print()
    print("TOP 15 BIGGEST MESSAGES")
    print("-" * 58)

    for item in big_messages[:15]:
        size_mb = item["size"] / 1024 / 1024

        sender = item["sender"][:22]
        subject = item["subject"][:38]

        print()
        print(f"{size_mb:7.1f} MB  {sender}")
        print(f"          {subject}")

    print()
    print("=" * 58)
    print("Messages harmed: 0")
    print("=" * 58)
    print()


if __name__ == "__main__":
    main()
