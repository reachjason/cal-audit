"""cal.com v2 (with v1 fallback).

Attendees come back inline on each booking, so this is a single paginated
sweep with no per-event follow-up calls.
"""
import urllib.parse

from .base import (BaseProvider, ProviderError, duration_minutes, iso_utc,
                   person)

TAKE = 100
MAX_PAGES = 200


class CalComProvider(BaseProvider):
    name = "cal.com"
    env_var = "CAL_API_KEY"

    def _v2_headers(self):
        return {"Authorization": f"Bearer {self.token}",
                "cal-api-version": "2024-08-13"}

    def whoami(self):
        try:
            payload, _ = self.get("https://api.cal.com/v2/me", self._v2_headers())
        except ProviderError:
            return ""
        data = payload.get("data") or {}
        return (data.get("email") or "").strip().lower()

    def fetch(self):
        try:
            return self._fetch_v2()
        except ProviderError as e:
            self.log(f"  v2 unavailable -> {e}")
            self.log("  falling back to v1")
            return self._fetch_v1()

    def _fetch_v2(self):
        out, skip, page = [], 0, 0
        while True:
            url = f"https://api.cal.com/v2/bookings?take={TAKE}&skip={skip}"
            payload, body = self.get(url, self._v2_headers())
            self.save_raw(f"calcom_v2_page_{page:04d}.json", body)
            batch = payload.get("data") or []
            if isinstance(batch, dict):
                batch = batch.get("bookings") or []
            out.extend(self._normalize(b) for b in batch)
            self.log(f"  page {page}: {len(batch)} bookings (total {len(out)})")
            if len(batch) < TAKE or page >= MAX_PAGES:
                break
            skip += TAKE
            page += 1
        return out

    def _fetch_v1(self):
        out, page = [], 0
        while True:
            url = (f"https://api.cal.com/v1/bookings"
                   f"?apiKey={urllib.parse.quote(self.token)}&take={TAKE}&page={page}")
            payload, body = self.get(url)
            self.save_raw(f"calcom_v1_page_{page:04d}.json", body)
            batch = payload.get("bookings") or []
            out.extend(self._normalize(b) for b in batch)
            self.log(f"  page {page}: {len(batch)} bookings (total {len(out)})")
            if len(batch) < TAKE or page >= MAX_PAGES:
                break
            page += 1
        return out

    @staticmethod
    def _guest(g):
        """guests[] entries are sometimes plain strings, sometimes objects."""
        if isinstance(g, str):
            return person(email=g)
        if isinstance(g, dict):
            return person(g.get("name"), g.get("email"))
        return person()

    def _normalize(self, b):
        start, end = b.get("start") or "", b.get("end") or ""
        guests = [self._guest(g) for g in (b.get("guests") or [])]
        # The booking form can carry guest emails not mirrored in guests[].
        for g in ((b.get("bookingFieldsResponses") or {}).get("guests") or []):
            guests.append(self._guest(g))
        return {
            "provider": self.name,
            "uid": b.get("uid") or str(b.get("id") or ""),
            "title": b.get("title") or "",
            "description": (b.get("description") or "").replace("\n", " ").strip(),
            "status": (b.get("status") or "").lower(),
            "start": iso_utc(start),
            "end": iso_utc(end),
            "duration_min": b.get("duration") or duration_minutes(start, end),
            "event_type": (b.get("eventType") or {}).get("slug") or "",
            "location": b.get("location") or "",
            "meeting_url": b.get("meetingUrl") or "",
            "booked_at": iso_utc(b.get("createdAt")),
            "cancellation_reason": (b.get("cancellationReason") or "").replace("\n", " ").strip(),
            "cancelled_by": (b.get("cancelledByEmail") or "").lower(),
            "hosts": [person(h.get("name"), h.get("email"), h.get("timeZone"))
                      for h in (b.get("hosts") or []) if h.get("email")],
            "attendees": [person(a.get("name"), a.get("email"), a.get("timeZone"))
                          for a in (b.get("attendees") or []) if a.get("email")],
            "guests": [g for g in guests if g["email"]],
        }
