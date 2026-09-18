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
            TOKEN_FILE, SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
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
                    f"The Gmail Wardens demand patience. "
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


def find_offenders(service):
    promotions = get_recent_promotions(service)

    print()
    print(
        f"Gathering evidence from "
        f"{len(promotions):,} recent Promotions..."
    )
    print()

    counts = Counter()
    names = {}
    unsubscribe = defaultdict(bool)
    one_click = defaultdict(bool)

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

        counts[email_address] += 1
        names[email_address] = (
            name.strip() if name.strip() else email_address
        )

        if get_header(headers, "List-Unsubscribe"):
            unsubscribe[email_address] = True

        unsubscribe_post = get_header(
            headers,
            "List-Unsubscribe-Post",
        )

        if "one-click" in unsubscribe_post.lower():
            one_click[email_address] = True

        print(
            f"Examining evidence: "
            f"{number:>3}/{len(promotions)}",
            end="\r",
            flush=True,
        )

        time.sleep(0.15)

    print()
    print()
    print("Calculating lifetime offenses...")
    print()

    offenders = []

    for email_address, recent in counts.most_common(
        TOP_OFFENDERS
    ):
        name = names[email_address]

        print(f"{name}...", end=" ", flush=True)

        total = count_sender(service, email_address)

        print(f"{total:,} offenses")

        offenders.append({
            "name": name,
            "email": email_address,
            "recent": recent,
            "total": total,
            "unsubscribe": unsubscribe[email_address],
            "one_click": one_click[email_address],
        })

    offenders.sort(
        key=lambda x: x["total"],
        reverse=True,
    )

    return offenders


def show_wanted_list(offenders):
    print()
    print("=" * 66)
    print("                 THE DUNGEON TRIBUNAL")
    print("=" * 66)
    print()

    for number, offender in enumerate(offenders, start=1):
        method = (
            "ONE-CLICK"
            if offender["one_click"]
            else "AVAILABLE"
            if offender["unsubscribe"]
            else "NOT DETECTED"
        )

        print(
            f"[{number:>2}] "
            f"{offender['name'][:28]:<28} "
            f"{offender['total']:>7,} msgs   "
            f"Unsubscribe: {method}"
        )

    print()
    print("[ 0] Flee the courtroom")
    print()


def sentencing_menu(offender):
    print()
    print("=" * 66)
    print("                     THE ACCUSED")
    print("=" * 66)
    print()
    print(f"Name:          {offender['name']}")
    print(f"Sender:        {offender['email']}")
    print(f"Mail offenses: {offender['total']:,}")
    print()

    if offender["one_click"]:
        unsub = "YES - One-Click supported"
    elif offender["unsubscribe"]:
        unsub = "YES - Unsubscribe information detected"
    else:
        unsub = "Not detected"

    print(f"Unsubscribe:   {unsub}")
    print()
    print("-" * 66)
    print("CHOOSE A SENTENCE")
    print("-" * 66)
    print()
    print("[1] PARDON")
    print("    Leave the offender and their messages alone.")
    print()
    print("[2] BANISH")
    print("    Unsubscribe from future messages.")
    print()
    print("[3] PURGE THE ARCHIVES")
    print(
        f"    Remove the existing "
        f"{offender['total']:,} messages."
    )
    print()
    print("[4] TOTAL EXILE")
    print("    Unsubscribe AND remove existing messages.")
    print()
    print("[0] Return to the offender list")
    print()

    choice = input("Sentence: ").strip()

    actions = {
        "1": (
            "PARDON",
            "No action would be taken."
        ),
        "2": (
            "BANISH",
            "Would attempt to unsubscribe this sender."
        ),
        "3": (
            "PURGE THE ARCHIVES",
            f"Would target {offender['total']:,} "
            f"existing messages for cleanup."
        ),
        "4": (
            "TOTAL EXILE",
            f"Would unsubscribe this sender and target "
            f"{offender['total']:,} existing messages."
        ),
    }

    if choice == "0":
        return

    if choice not in actions:
        print()
        print("The Tribunal does not recognize that sentence.")
        input("\nPress Enter to continue...")
        return

    title, description = actions[choice]

    print()
    print("=" * 66)
    print(f"                    SENTENCE: {title}")
    print("=" * 66)
    print()
    print(description)
    print()
    print("DRY RUN ONLY")
    print("No Gmail data was modified.")
    print("No unsubscribe request was sent.")
    print("No messages were moved or deleted.")
    print()
    print("The executioner remains unemployed.")
    print()

    input("Press Enter to return to the Tribunal...")


def main():
    print()
    print("=" * 66)
    print("                     INBOX JANITOR")
    print("                  THE DUNGEON TRIBUNAL")
    print("=" * 66)
    print()
    print("Jurisdiction: READ ONLY")
    print("The court may pass judgment.")
    print("It cannot yet carry out a sentence.")

    service = get_gmail_service()
    offenders = find_offenders(service)

    while True:
        show_wanted_list(offenders)

        selection = input(
            "Select an offender to face judgment: "
        ).strip()

        if selection == "0":
            print()
            print("The Tribunal is adjourned.")
            print("Messages harmed: 0")
            print()
            break

        try:
            index = int(selection) - 1

            if index < 0 or index >= len(offenders):
                raise ValueError

        except ValueError:
            print()
            print("That prisoner does not exist.")
            continue

        sentencing_menu(offenders[index])


if __name__ == "__main__":
    main()
