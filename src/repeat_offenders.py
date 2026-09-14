from pathlib import Path
from collections import Counter, defaultdict
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

SAMPLE_SIZE = 150
TOP_OFFENDERS = 12


def get_gmail_service():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            SCOPES,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE,
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(
            creds.to_json(),
            encoding="utf-8",
        )

    return build("gmail", "v1", credentials=creds)


def get_header(headers, name):
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def get_message(service, message_id):
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
                wait_seconds = min(15 * retries, 60)

                print()
                print(
                    f"Gmail has ordered a tactical retreat. "
                    f"Waiting {wait_seconds} seconds..."
                )

                time.sleep(wait_seconds)
                continue

            raise


def get_recent_promotions(service):
    response = (
        service.users()
        .messages()
        .list(
            userId="me",
            q="category:promotions",
            maxResults=SAMPLE_SIZE,
        )
        .execute()
    )

    return response.get("messages", [])


def count_sender(service, email_address):
    total = 0
    page_token = None

    query = f"from:{email_address}"

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

        total += len(response.get("messages", []))

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return total


def main():
    print()
    print("=" * 64)
    print("                    REPEAT OFFENDERS")
    print("               An Inbox Janitor Investigation")
    print("=" * 64)
    print()
    print("Mode: READ ONLY")
    print("No offenders will be harmed.")
    print("They will, however, be publicly shamed.")
    print()

    service = get_gmail_service()

    promotions = get_recent_promotions(service)

    print(
        f"Inspecting {len(promotions):,} recent promotional messages..."
    )
    print()

    sender_counts = Counter()
    sender_names = {}
    unsubscribe_found = defaultdict(bool)
    one_click_found = defaultdict(bool)

    for number, item in enumerate(promotions, start=1):
        message = get_message(service, item["id"])

        headers = (
            message
            .get("payload", {})
            .get("headers", [])
        )

        raw_sender = get_header(headers, "From")
        name, email_address = parseaddr(raw_sender)

        email_address = email_address.lower().strip()

        if not email_address:
            continue

        sender_counts[email_address] += 1
        sender_names[email_address] = (
            name.strip()
            if name.strip()
            else email_address
        )

        unsubscribe = get_header(
            headers,
            "List-Unsubscribe",
        )

        unsubscribe_post = get_header(
            headers,
            "List-Unsubscribe-Post",
        )

        if unsubscribe:
            unsubscribe_found[email_address] = True

        if "one-click" in unsubscribe_post.lower():
            one_click_found[email_address] = True

        print(
            f"Examining evidence: "
            f"{number:>3} / {len(promotions):<3}",
            end="\r",
            flush=True,
        )

        # Keeps our metadata inspection comfortably paced.
        time.sleep(0.15)

    print()
    print()

    top = sender_counts.most_common(TOP_OFFENDERS)

    print("Calculating lifetime offenses...")
    print()

    results = []

    for index, (email_address, recent_count) in enumerate(
        top,
        start=1,
    ):
        name = sender_names[email_address]

        print(
            f"[{index:>2}/{len(top)}] {name}...",
            end=" ",
            flush=True,
        )

        try:
            total_count = count_sender(
                service,
                email_address,
            )
        except HttpError:
            total_count = None

        results.append({
            "name": name,
            "email": email_address,
            "recent": recent_count,
            "total": total_count,
            "unsubscribe": unsubscribe_found[email_address],
            "one_click": one_click_found[email_address],
        })

        if total_count is None:
            print("count unavailable")
        else:
            print(f"{total_count:,} total")

    results.sort(
        key=lambda item: (
            item["total"]
            if item["total"] is not None
            else 0
        ),
        reverse=True,
    )

    print()
    print("=" * 64)
    print("                    MOST WANTED")
    print("=" * 64)

    for rank, item in enumerate(results, start=1):
        total_text = (
            f"{item['total']:,}"
            if item["total"] is not None
            else "?"
        )

        unsubscribe_text = (
            "ONE-CLICK"
            if item["one_click"]
            else "YES"
            if item["unsubscribe"]
            else "Not detected"
        )

        print()
        print(f"#{rank}  {item['name']}")
        print(f"    {item['email']}")
        print(
            f"    Recent sample:  "
            f"{item['recent']:,} / {len(promotions):,}"
        )
        print(
            f"    Total mailbox:  "
            f"{total_text}"
        )
        print(
            f"    Unsubscribe:     "
            f"{unsubscribe_text}"
        )

    print()
    print("=" * 64)
    print("No arrests made. Read-only jurisdiction.")
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
