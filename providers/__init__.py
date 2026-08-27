"""Booking providers. Each returns bookings in one normalized schema."""
from .calcom import CalComProvider
from .calendly import CalendlyProvider

PROVIDERS = {
    "cal": CalComProvider,
    "calendly": CalendlyProvider,
}

__all__ = ["PROVIDERS", "CalComProvider", "CalendlyProvider"]
