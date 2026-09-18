from pathlib import Path
from email.utils import parseaddr
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

SAMPLE_SIZE = 150
TOP_OFFENDERS = 12
BATCH_SIZE = 500


def get_service():
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


def header(headers, name):
    for item in headers:
        if item.get("name", "").lower() == name.lower():
            return item.get("value", "")
    return ""


def recent_promotions(service):
    response = service.users().messages().list(
        userId="me",
        q="category:promotions",
        maxResults=SAMPLE_SIZE,
    ).execute()

    return response.get("messages", [])


def inspect_message(service, message_id):
    while True:
        try:
            return service.users().messages().get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=[
                    "From",
                    "List-Unsubscribe",
                    "List-Unsubscribe-Post",
                ],
            ).execute()

        except HttpError as exc:
            status = getattr(exc.resp, "status", None)

            if status in (403, 429):
                print("\nThe Gmail Wardens demand patience...")
                time.sleep(20)
                continue

            raise


def find_candidates(service):
    from collections import Counter

    sample = recent_promotions(service)

    counts = Counter()
    names = {}
    one_click = {}

    print()
    print(f"Inspecting {len(sample)} recent Promotions...")
    print()

    for number, item in enumerate(sample, 1):
        msg = inspect_message(service, item["id"])
        headers = msg.get("payload", {}).get("headers", [])

        raw = header(headers, "From")
        name, address = parseaddr(raw)
        address = address.lower().strip()

        if address:
            counts[address] += 1
            names[address] = name.strip() or address

            post = header(headers, "List-Unsubscribe-Post")
            one_click[address] = (
                "one-click" in post.lower()
            )

        print(
            f"Examining evidence: {number}/{len(sample)}",
            end="\r",
            flush=True,
        )

        time.sleep(.15)

    print()
    print()

    offenders = []

    for address, recent in counts.most_common(TOP_OFFENDERS):
        ids = get_sender_ids(service, address)

        offenders.append({
            "name": names[address],
            "email": address,
            "recent": recent,
            "ids": ids,
            "total": len(ids),
            "one_click": one_click.get(address, False),
        })

    offenders.sort(
        key=lambda x: x["total"],
        reverse=True,
    )

    return offenders


def get_sender_ids(service, address):
    ids = []
    token = None

    while True:
        response = service.users().messages().list(
            userId="me",
            q=f"from:{address}",
            maxResults=500,
            pageToken=token,
        ).execute()

        ids.extend(
            item["id"]
            for item in response.get("messages", [])
        )

        token = response.get("nextPageToken")

        if not token:
            break

    return ids


def move_to_trash(service, ids):
    total = len(ids)

    for start in range(0, total, BATCH_SIZE):
        batch = ids[start:start + BATCH_SIZE]

        service.users().messages().batchModify(
            userId="me",
            body={
                "ids": batch,
                "addLabelIds": ["TRASH"],
            },
        ).execute()

        completed = min(start + len(batch), total)

        print(
            f"Transporting prisoners to the Trash Dungeon: "
            f"{completed:,}/{total:,}"
        )


def show_offenders(offenders):
    print()
    print("=" * 68)
    print("                    DUNGEON TRIBUNAL")
    print("=" * 68)
    print()

    active = [
        offender
        for offender in offenders
        if offender["total"] > 0
    ]

    if not active:
        print("The docket is empty.")
        print("These particular offenders have been dealt with.")
        print()
        print("[ R] Rescan for new offenders")
        print("[ 0] Adjourn")
        print()
        return active

    for i, offender in enumerate(active, 1):
        unsub = (
            "DETECTED"
            if offender["one_click"]
            else "NO"
        )

        print(
            f"[{i:>2}] "
            f"{offender['name'][:27]:<27} "
            f"{offender['total']:>6,} msgs   "
            f"One-click: {unsub}"
        )

    print()
    print("[ R] Rescan for new offenders")
    print("[ 0] Adjourn")
    print()

    return active


def judgment(service, offender):
    print()
    print("=" * 68)
    print("                     THE ACCUSED")
    print("=" * 68)
    print()
    print(f"Name:     {offender['name']}")
    print(f"Sender:   {offender['email']}")
    print(f"Messages: {offender['total']:,}")
    print()

    print("[1] PARDON")
    print("[2] BANISH       - Unsubscribe (DRY RUN)")
    print("[3] PURGE        - Move existing mail to Trash")
    print("[4] TOTAL EXILE  - Unsubscribe DRY RUN + Trash mail")
    print("[0] Return")
    print()

    choice = input("Choose sentence [0-4]: ").strip()

    if choice in ("0", "1"):
        print("\nNo sentence carried out.")
        return

    if choice == "2":
        print()
        print("BANISHMENT NOT YET AUTHORIZED.")
        print("One-click unsubscribe verification is still being built.")
        return

    if choice not in ("3", "4"):
        print("\nUnknown sentence.")
        return

    print()
    print("=" * 68)
    print("                    FINAL JUDGMENT")
    print("=" * 68)
    print()
    print(offender["name"])
    print(offender["email"])
    print()
    print(
        f"{offender['total']:,} existing messages "
        f"will be moved to Gmail Trash."
    )

    if choice == "4":
        print()
        print(
            "Unsubscribe is NOT being executed in this trial."
        )
        print(
            "Only the Gmail cleanup portion will run."
        )

    print()
    print("Messages are NOT permanently deleted.")
    print("They remain recoverable from Gmail Trash.")
    print()
    confirmation = input(
        "CONFIRMATION - Type EXILE to carry out the sentence: "
    ).strip().upper()

    if confirmation != "EXILE":
        print()
        print("Sentence stayed.")
        print("Nothing was changed.")
        return

    print()
    print("THE GATES OF THE TRASH DUNGEON OPEN...")
    print()

    move_to_trash(
        service,
        offender["ids"],
    )

    print()
    print("=" * 68)
    print("                    SENTENCE COMPLETE")
    print("=" * 68)
    print()
    print(
        f"{offender['total']:,} messages were moved "
        f"to Gmail Trash."
    )

    if choice == "4":
        print(
            "Unsubscribe was NOT executed."
        )

    print()
    print("Permanent deletions: 0")
    print()


def main():
    print()
    print("=" * 68)
    print("                      INBOX JANITOR")
    print("                       EXECUTIONER")
    print("=" * 68)
    print()
    print("Gmail modification authority enabled.")
    print("Permanent deletion authority: NONE")
    print("Unsubscribe execution: DISABLED")
    print()

    service = get_service()

    profile = service.users().getProfile(
        userId="me"
    ).execute()

    print(f"Account: {profile.get('emailAddress')}")

    offenders = find_candidates(service)

    while True:
        active = show_offenders(offenders)

        selection = input(
            "Summon an offender: "
        ).strip()

        if selection.lower() == "r":
            print()
            print("The bailiffs are gathering a fresh docket...")
            offenders = find_candidates(service)
            continue

        if selection == "0":
            print()
            print("The Tribunal is adjourned.")
            break

        try:
            index = int(selection) - 1

            if index < 0 or index >= len(active):
                raise ValueError

        except ValueError:
            print("\nInvalid offender.")
            continue

        offender = active[index]

        judgment(
            service,
            offender,
        )

        offender["ids"] = get_sender_ids(
            service,
            offender["email"],
        )

        offender["total"] = len(
            offender["ids"]
        )

        offenders.sort(
            key=lambda x: x["total"],
            reverse=True,
        )


if __name__ == "__main__":
    main()


