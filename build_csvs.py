#!/usr/bin/env python3
"""Turn normalized bookings into contacts.csv, meetings.csv and a summary report.

    python3 build_csvs.py

Reads private/raw/normalized.json (written by fetch.py) and writes both CSVs
plus report.txt into private/. Provider-agnostic: works for cal.com or Calendly.
"""
import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

DEFAULT_IN = os.path.join("private", "raw", "normalized.json")
DEFAULT_OUT = "private"

# Hours in a working day, used for the "working days on calls" headline.
WORKING_DAY_HOURS = 8

FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
    "live.com", "msn.com", "yandex.com", "zoho.com", "mail.com", "gmx.com",
}


def is_past(start, now):
    try:
        return datetime.fromisoformat(str(start).replace("Z", "+00:00")) < now
    except (ValueError, TypeError):
        return False


def counterparties(booking, self_email):
    """Everyone on the booking who isn't you, deduped by email.

    Hosts are included: on bookings where someone else owned the event type,
    the host IS the contact. Only self_email is excluded.
    """
    seen, out = set(), []
    for group, hosted in (("hosts", True), ("attendees", False), ("guests", False)):
        for p in booking.get(group) or []:
            email = (p.get("email") or "").strip().lower()
            if not email or email == self_email or email in seen:
                continue
            seen.add(email)
            out.append({**p, "email": email, "hosted": hosted})
    return out


def build(bookings, self_email, out_dir):
    now = datetime.now(timezone.utc)
    os.makedirs(out_dir, exist_ok=True)

    # ---------- meetings.csv ----------
    rows = []
    for b in bookings:
        ppl = counterparties(b, self_email)
        rows.append({
            "start_utc": b.get("start", ""),
            "date": (b.get("start") or "")[:10],
            "end_utc": b.get("end", ""),
            "duration_min": b.get("duration_min", ""),
            "status": b.get("status", ""),
            "is_past": "yes" if is_past(b.get("start"), now) else "no",
            "title": b.get("title", ""),
            "event_type": b.get("event_type", ""),
            "your_role": "attendee" if any(p["hosted"] for p in ppl) else "host",
            "host_emails": "; ".join(sorted({h["email"] for h in b.get("hosts") or []
                                             if h.get("email")})),
            "attendee_names": "; ".join(p.get("name", "") for p in ppl if p.get("name")),
            "attendee_emails": "; ".join(p["email"] for p in ppl),
            "attendee_domains": "; ".join(sorted({p["email"].split("@")[-1] for p in ppl})),
            "num_attendees": len(ppl),
            "attendee_timezones": "; ".join(sorted({p.get("timezone", "") for p in ppl
                                                    if p.get("timezone")})),
            "location": b.get("location", ""),
            "meeting_url": b.get("meeting_url", ""),
            "booked_at": b.get("booked_at", ""),
            "cancellation_reason": b.get("cancellation_reason", ""),
            "cancelled_by": b.get("cancelled_by", ""),
            "description": b.get("description", ""),
            "provider": b.get("provider", ""),
            "uid": b.get("uid", ""),
        })
    rows.sort(key=lambda r: r["start_utc"])

    meetings_path = os.path.join(out_dir, "meetings.csv")
    with open(meetings_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---------- contacts.csv ----------
    agg = defaultdict(lambda: {
        "names": [], "tzs": set(), "dates": [], "event_types": set(),
        "total": 0, "cancelled": 0, "pending": 0, "met": 0, "upcoming": 0,
        "they_hosted": 0,
    })
    for b in bookings:
        status = (b.get("status") or "").lower()
        past = is_past(b.get("start"), now)
        for p in counterparties(b, self_email):
            c = agg[p["email"]]
            c["total"] += 1
            if p["hosted"]:
                c["they_hosted"] += 1
            if p.get("name"):
                c["names"].append(p["name"])
            if p.get("timezone"):
                c["tzs"].add(p["timezone"])
            if b.get("start"):
                c["dates"].append(b["start"][:10])
            if b.get("event_type"):
                c["event_types"].add(b["event_type"])
            if status == "accepted":
                c["met" if past else "upcoming"] += 1
            elif status == "cancelled":
                c["cancelled"] += 1
            elif status == "pending":
                c["pending"] += 1

    contacts = []
    for email, c in agg.items():
        domain = email.split("@")[-1]
        dates = sorted(c["dates"])
        contacts.append({
            "email": email,
            # most common spelling of the name wins
            "name": max(set(c["names"]), key=c["names"].count) if c["names"] else "",
            "domain": domain,
            "domain_type": "personal" if domain in FREEMAIL else "company",
            "meetings_total": c["total"],
            "actually_met": c["met"],
            "upcoming": c["upcoming"],
            "cancelled": c["cancelled"],
            "pending": c["pending"],
            "they_hosted_you": c["they_hosted"],
            "first_booking": dates[0] if dates else "",
            "last_booking": dates[-1] if dates else "",
            "timezone": "; ".join(sorted(c["tzs"])),
            "event_types": "; ".join(sorted(c["event_types"])),
        })
    contacts.sort(key=lambda c: (-c["actually_met"], -c["meetings_total"], c["email"]))

    contacts_path = os.path.join(out_dir, "contacts.csv")
    with open(contacts_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(contacts[0].keys()))
        w.writeheader()
        w.writerows(contacts)

    return rows, contacts, meetings_path, contacts_path


def report(rows, contacts, provider, self_email):
    """Headline summary. All derived figures round UP, never down."""
    minutes = sum(int(r["duration_min"]) for r in rows if str(r["duration_min"]).isdigit())
    hours = math.ceil(minutes / 60)
    days = math.ceil(hours / WORKING_DAY_HOURS)

    held = [r for r in rows if r["status"] == "accepted" and r["is_past"] == "yes"]
    held_min = sum(int(r["duration_min"]) for r in held if str(r["duration_min"]).isdigit())
    held_hours = math.ceil(held_min / 60)
    held_days = math.ceil(held_hours / WORKING_DAY_HOURS)

    dates = sorted(r["date"] for r in rows if r["date"])
    met = sum(1 for c in contacts if c["actually_met"] > 0)
    companies = len({c["domain"] for c in contacts if c["domain_type"] == "company"})

    lines = [
        "=" * 64,
        f"  BOOKING REPORT  ({provider})",
        "=" * 64,
        "",
        f"  I've had {len(rows):,} meetings booked on my calendar and spent a",
        f"  total of {days} working days on calls "
        f"({minutes:,} minutes, {hours} hours).",
        "",
        "-" * 64,
        f"  Range              {dates[0]} -> {dates[-1]}" if dates else "  Range              n/a",
        f"  Bookings           {len(rows):,}",
        f"  Unique people      {len(contacts):,}",
        f"  Actually met       {met:,}",
        f"  Company domains    {companies:,}",
        "",
        f"  All bookings       {minutes:,} min / {hours} hrs / {days} working days",
        f"  Held only          {held_min:,} min / {held_hours} hrs / {held_days} working days",
        f"                     ({len(held):,} accepted + in the past)",
        "",
        f"  Working day = {WORKING_DAY_HOURS}h. Hours and days always rounded up.",
        f"  Identity excluded: {self_email or '(unknown)'}",
        "=" * 64,
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=DEFAULT_IN)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--self", dest="self_email", default=None,
                    help="override the identity recorded by fetch.py")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        payload = json.load(f)
    bookings = payload.get("bookings") or []
    if not bookings:
        raise SystemExit(f"no bookings in {args.input}")
    self_email = (args.self_email or payload.get("self_email") or "").strip().lower()

    rows, contacts, mpath, cpath = build(bookings, self_email, args.out)
    text = report(rows, contacts, payload.get("provider", "unknown"), self_email)

    print(text)
    print(f"\n  {mpath}   {len(rows):,} rows")
    print(f"  {cpath}   {len(contacts):,} people")

    with open(os.path.join(args.out, "report.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
