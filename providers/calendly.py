"""Calendly v2.

UNTESTED against a live account -- written to the documented v2 API. See the
README. The shape differs from cal.com in two ways that matter:

1. Invitee emails are NOT on the event. Each event needs a follow-up call to
   /scheduled_events/{uuid}/invitees, so a full sweep is O(events) requests.
2. Event types are referenced by URI, not slug, so slugs are resolved once
   up front into a uri -> slug map.
"""
import json
import urllib.parse

from .base import (BaseProvider, ProviderError, duration_minutes, iso_utc,
                   person)

API = "https://api.calendly.com"
COUNT = 100
MAX_PAGES = 500

STATUS_MAP = {"active": "accepted", "canceled": "cancelled"}


class CalendlyProvider(BaseProvider):
    name = "calendly"
    env_var = "CALENDLY_TOKEN"

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    # ---------- identity ----------
    def _me(self):
        payload, _ = self.get(f"{API}/users/me", self._headers())
        return payload.get("resource") or {}

    def whoami(self):
        try:
            return (self._me().get("email") or "").strip().lower()
        except ProviderError:
            return ""

    # ---------- pagination ----------
    def _paginate(self, url, tag):
        """Walk Calendly's cursor pagination, yielding each collection item."""
        page = 0
        while url and page < MAX_PAGES:
            payload, body = self.get(url, self._headers())
            self.save_raw(f"calendly_{tag}_page_{page:04d}.json", body)
            items = payload.get("collection") or []
            self.log(f"  {tag} page {page}: {len(items)}")
            yield from items
            url = (payload.get("pagination") or {}).get("next_page")
            page += 1

    # ---------- event type slugs ----------
    def _event_type_slugs(self, user_uri):
        url = f"{API}/event_types?{urllib.parse.urlencode({'user': user_uri, 'count': COUNT})}"
        slugs = {}
        try:
            for et in self._paginate(url, "event_types"):
                if et.get("uri"):
                    slugs[et["uri"]] = et.get("slug") or et.get("name") or ""
        except ProviderError as e:
            # Non-fatal: we just lose the slug column.
            self.log(f"  could not resolve event types ({e}); continuing")
        return slugs

    # ---------- invitees ----------
    def _invitees(self, event_uri):
        """All invitees for one event. event_uri is already an absolute URL."""
        out, url = [], f"{event_uri}/invitees?count={COUNT}"
        while url:
            payload, _ = self.get(url, self._headers())
            out.extend(payload.get("collection") or [])
            url = (payload.get("pagination") or {}).get("next_page")
        return out

    # ---------- main ----------
    def fetch(self):
        me = self._me()
        user_uri = me.get("uri")
        if not user_uri:
            raise ProviderError("could not resolve Calendly user URI from /users/me")

        slugs = self._event_type_slugs(user_uri)

        params = {"user": user_uri, "count": COUNT, "sort": "start_time:asc"}
        url = f"{API}/scheduled_events?{urllib.parse.urlencode(params)}"
        events = list(self._paginate(url, "events"))
        self.log(f"  {len(events)} events; fetching invitees "
                 f"({len(events)} extra requests)")

        out, all_invitees = [], {}
        for i, ev in enumerate(events, 1):
            try:
                invitees = self._invitees(ev["uri"])
            except ProviderError as e:
                self.log(f"    ! invitees failed for {ev.get('uri')}: {e}")
                invitees = []
            all_invitees[ev.get("uri", "")] = invitees
            out.append(self._normalize(ev, invitees, slugs))
            if i % 25 == 0:
                self.log(f"    invitees {i}/{len(events)}")

        # One combined raw file rather than one per event.
        self.save_raw("calendly_invitees.json", json.dumps(all_invitees, indent=2))
        return out

    def _normalize(self, ev, invitees, slugs):
        start, end = ev.get("start_time") or "", ev.get("end_time") or ""
        loc = ev.get("location") or {}
        if isinstance(loc, dict):
            location = loc.get("location") or loc.get("type") or ""
            join_url = loc.get("join_url") or ""
        else:
            location, join_url = str(loc), ""
        cancellation = ev.get("cancellation") or {}

        attendees = [
            person(iv.get("name"), iv.get("email"), iv.get("timezone"))
            for iv in invitees if iv.get("email")
        ]
        guests = [person(email=g.get("email"))
                  for g in (ev.get("event_guests") or []) if g.get("email")]
        # Some payloads nest additional guests under the invitee.
        for iv in invitees:
            for g in (iv.get("guests") or []):
                email = g.get("email") if isinstance(g, dict) else g
                if email:
                    guests.append(person(email=email))

        return {
            "provider": self.name,
            "uid": (ev.get("uri") or "").rstrip("/").split("/")[-1],
            "title": ev.get("name") or "",
            "description": (ev.get("meeting_notes_plain") or "").replace("\n", " ").strip(),
            "status": STATUS_MAP.get((ev.get("status") or "").lower(),
                                     (ev.get("status") or "").lower()),
            "start": iso_utc(start),
            "end": iso_utc(end),
            "duration_min": duration_minutes(start, end),
            "event_type": slugs.get(ev.get("event_type"), ""),
            "location": location,
            "meeting_url": join_url,
            "booked_at": iso_utc(ev.get("created_at")),
            "cancellation_reason": (cancellation.get("reason") or "").replace("\n", " ").strip(),
            # Calendly reports this as a display name, not an email address.
            "cancelled_by": (cancellation.get("canceled_by") or "").strip(),
            "hosts": [person(m.get("user_name"), m.get("user_email"))
                      for m in (ev.get("event_memberships") or []) if m.get("user_email")],
            "attendees": attendees,
            "guests": [g for g in guests if g["email"]],
        }
