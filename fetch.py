#!/usr/bin/env python3
"""Pull every booking from cal.com or Calendly into one normalized JSON file.

    python3 fetch.py --provider cal
    python3 fetch.py --provider calendly

Credentials come from the environment or a gitignored .env:
    CAL_API_KEY=cal_live_...
    CALENDLY_TOKEN=...

Raw API responses are written verbatim to private/raw/ so the data can be
re-cut later without re-hitting the API.
"""
import argparse
import collections
import json
import os
import sys

from providers import PROVIDERS
from providers.base import ProviderError

DEFAULT_OUT = os.path.join("private", "raw")


def load_dotenv(path=".env"):
    """Minimal .env reader; real env vars always win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def infer_self(bookings):
    """Fallback identity: the address hosting the clear majority of bookings.

    Used only when the provider's /me endpoint is unavailable. Guarding on a
    majority matters -- treating *every* host address as "you" wrongly deletes
    real contacts from bookings where someone else owned the event type.
    """
    counts = collections.Counter(
        h["email"] for b in bookings for h in b.get("hosts") or [] if h.get("email")
    )
    if not counts:
        return ""
    email, n = counts.most_common(1)[0]
    return email if n > len(bookings) / 2 else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="cal")
    ap.add_argument("--self", dest="self_email", default=None,
                    help="your own address; overrides /me auto-detection")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"raw output dir (default {DEFAULT_OUT})")
    ap.add_argument("--offline", metavar="FILE",
                    help="rebuild from a previously saved raw cal.com dump instead of "
                         "calling the API")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    os.makedirs(args.out, exist_ok=True)
    verbose = not args.quiet

    if args.offline:
        from providers.calcom import CalComProvider
        prov = CalComProvider("", args.out, verbose)
        with open(args.offline, encoding="utf-8") as f:
            payload = json.load(f)
        raw = payload.get("bookings") if isinstance(payload, dict) else payload
        bookings = [prov._normalize(b) for b in raw]
        provider_name = "cal.com"
        if verbose:
            print(f"Offline rebuild from {args.offline}: {len(bookings)} bookings")
    else:
        cls = PROVIDERS[args.provider]
        token = os.environ.get(cls.env_var, "").strip()
        if not token:
            sys.exit(f"error: {cls.env_var} is not set. "
                     f"Copy .env.example to .env and fill it in.")
        prov = cls(token, args.out, verbose)
        provider_name = cls.name
        if verbose:
            print(f"Fetching from {provider_name}...")
        try:
            bookings = prov.fetch()
        except ProviderError as e:
            sys.exit(f"error: {e}")

    self_email = (args.self_email or "").strip().lower()
    source = "--self"
    if not self_email and not args.offline:
        self_email = prov.whoami()
        source = "/me endpoint"
    if not self_email:
        self_email = infer_self(bookings)
        source = "inferred from majority host"
    if not self_email:
        print("warning: could not determine your own address; pass --self. "
              "Without it you will appear in your own contact list.", file=sys.stderr)

    out_path = os.path.join(args.out, "normalized.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"provider": provider_name,
                   "self_email": self_email,
                   "count": len(bookings),
                   "bookings": bookings}, f, indent=2)

    if verbose:
        print(f"\n{len(bookings)} bookings -> {out_path}")
        print(f"identity: {self_email or '(unknown)'}  [{source}]")
        print("next: python3 build_csvs.py")


if __name__ == "__main__":
    main()
