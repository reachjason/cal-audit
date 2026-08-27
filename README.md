# booking-contacts

Export every meeting booked through **cal.com** or **Calendly** into a deduplicated
contact list, a per-meeting log, and a summary of how much of your life you spent
on calls.

```
================================================================
  BOOKING REPORT  (cal.com)
================================================================

  I've had 400 meetings booked on my calendar and spent a
  total of 24 working days on calls (11,265 minutes, 188 hours).
```

Every booking is, by definition, time with someone outside your own head — so
this doubles as a lightweight CRM export of everyone who has ever booked you.

## Setup

No dependencies. Python 3.9+, standard library only.

```bash
cp .env.example .env
# then edit .env and fill in ONE of the two credentials
```

| Provider | Credential | Where to get it |
|---|---|---|
| cal.com | `CAL_API_KEY` | Settings → Developer → API keys (`cal_live_…`) |
| Calendly | `CALENDLY_TOKEN` | Integrations → API & Webhooks → Personal Access Token |

`.env` is gitignored. Nothing reads a credential from a file tracked by git.

## Usage

```bash
python3 fetch.py --provider cal          # or: --provider calendly
python3 build_csvs.py
```

Output lands in `private/` (gitignored):

| File | Contents |
|---|---|
| `contacts.csv` | one row per person, deduped by email |
| `meetings.csv` | one row per booking, 22 columns |
| `report.txt` | the summary above |
| `raw/` | verbatim API responses, so you can re-cut without re-fetching |

Re-run `build_csvs.py` any time; it only reads `raw/normalized.json`. To rebuild
from a saved dump with no network and no credential:

```bash
python3 fetch.py --offline private/raw/all_bookings.json --self you@example.com
```

### Columns

`contacts.csv` — `email, name, domain, domain_type, meetings_total, actually_met,
upcoming, cancelled, pending, they_hosted_you, first_booking, last_booking,
timezone, event_types`

`domain_type` splits company addresses from personal ones (gmail, icloud, …).
`actually_met` counts only bookings that were accepted *and* have already
happened — the honest "people I have really spoken to" number.

## How it decides who *you* are

Everyone on a booking who isn't you becomes a contact. Getting "you" right is the
whole ballgame, so it is resolved in this order:

1. `--self you@example.com` if you pass it
2. the provider's `/me` endpoint
3. the address that hosts a **majority** of bookings

That majority guard is deliberate. Treating every address that ever appears as a
host as "your side" is wrong: on bookings where the *other* person owned the
event type, they are the host — and naive filtering silently deletes them from
your contact list. Hosts are therefore included as contacts, and
`meetings.csv.your_role` records which side booked.

## Report maths

- A working day is **8 hours** (`WORKING_DAY_HOURS` in `build_csvs.py`).
- Hours and days are **always rounded up**, never down.
- The headline counts *all* bookings, including cancelled ones — that is time
  that was committed on your calendar. `report.txt` also prints a **held only**
  line covering just accepted, already-happened meetings.

## Provider notes

**cal.com** — tested against a live account with 400 bookings. Tries API v2 and
falls back to v1. Attendees arrive inline, so a full export is one paginated
sweep.

**Calendly** — ⚠️ **written to the documented v2 API but not yet verified against
a live account.** Expect small fixes on first real run. Two structural
differences from cal.com:

- Invitee emails are *not* on the event. Each event needs a follow-up call to
  `/scheduled_events/{uuid}/invitees`, so a full export costs roughly one request
  per event plus pagination. Retries with backoff on 429 are built in.
- Event types are referenced by URI, so slugs are resolved once into a lookup map.
- Calendly reports `cancellation.canceled_by` as a display name, not an email.

Both APIs sit behind Cloudflare, which returns `403 error code: 1010` for
Python's default user-agent. A browser user-agent is sent for this reason — it is
load-bearing, not decoration.

## Privacy

`private/` holds real names and email addresses and is gitignored, along with
`*.csv`, `*.ics`, `.env` and `.calkey`. Keep it that way. If you ever paste an
API key into a terminal, a chat window, or a screenshot, rotate it — treat it as
burned.
