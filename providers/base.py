"""Shared HTTP plumbing and the normalized booking schema.

Every provider emits the same dict shape so downstream CSV building never has
to know which service the data came from:

    {
      "provider":     "cal.com" | "calendly",
      "uid":          str,          # stable id within the provider
      "title":        str,
      "description":  str,
      "status":       "accepted" | "cancelled" | "pending",
      "start":        ISO-8601 UTC,
      "end":          ISO-8601 UTC,
      "duration_min": int | "",
      "event_type":   str,          # slug
      "location":     str,
      "meeting_url":  str,
      "booked_at":    ISO-8601 UTC,
      "cancellation_reason": str,
      "cancelled_by": str,
      "hosts":        [{"name","email","timezone"}],
      "attendees":    [{"name","email","timezone"}],
      "guests":       [{"name","email","timezone"}],
    }
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

# Cloudflare sits in front of both APIs and 403s (error 1010) on Python's
# default user-agent. A browser UA is required, not cosmetic.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MAX_RETRIES = 5


class ProviderError(RuntimeError):
    pass


def person(name="", email="", timezone=""):
    """Normalized person record. Emails are lowercased for reliable dedupe."""
    return {
        "name": (name or "").strip(),
        "email": (email or "").strip().lower(),
        "timezone": timezone or "",
    }


def iso_utc(value):
    """Normalize assorted timestamp spellings to ISO-8601 with a Z suffix."""
    if not value:
        return ""
    return str(value).replace("+00:00", "Z")


def duration_minutes(start, end):
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return int((e - s).total_seconds() // 60)
    except (ValueError, TypeError):
        return ""


class BaseProvider:
    name = "base"
    env_var = ""

    def __init__(self, token, raw_dir, verbose=True):
        self.token = token
        self.raw_dir = raw_dir
        self.verbose = verbose
        os.makedirs(raw_dir, exist_ok=True)

    # ---------- to implement ----------
    def whoami(self):
        """Return the account owner's email, or '' if undetectable."""
        raise NotImplementedError

    def fetch(self):
        """Return a list of normalized bookings."""
        raise NotImplementedError

    # ---------- helpers ----------
    def log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    def save_raw(self, filename, body):
        with open(os.path.join(self.raw_dir, filename), "w", encoding="utf-8") as f:
            f.write(body)

    def get(self, url, headers=None):
        """GET with retry on 429/5xx, honouring Retry-After. Returns parsed JSON."""
        hdrs = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
        delay = 1.0
        for attempt in range(MAX_RETRIES):
            req = urllib.request.Request(url, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read().decode()
                return json.loads(body), body
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    wait = float(e.headers.get("Retry-After") or delay)
                    self.log(f"    HTTP {e.code}; retrying in {wait:.0f}s")
                    time.sleep(wait)
                    delay *= 2
                    continue
                hint = ""
                if e.code in (401, 403):
                    hint = (f"  (check {self.env_var}; a 403 with 'error code: 1010' "
                            f"means Cloudflare blocked the user-agent)")
                raise ProviderError(f"{self.name} HTTP {e.code}: {body[:300]}{hint}") from e
            except json.JSONDecodeError as e:
                raise ProviderError(f"{self.name}: non-JSON response: {body[:200]}") from e
            except urllib.error.URLError as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise ProviderError(f"{self.name}: network error: {e}") from e
        raise ProviderError(f"{self.name}: exhausted retries for {url}")
