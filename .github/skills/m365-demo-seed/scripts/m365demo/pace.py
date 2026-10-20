"""Waiting in real time; scaled down only against the loopback test server (M365_DEMO_TIME_SCALE)."""

import os
import time

from . import transport


def scale():
    if not transport.testing():
        return 1.0
    try:
        return max(0.0, float(os.environ.get("M365_DEMO_TIME_SCALE", "1")))
    except ValueError:
        return 1.0


def sleep(seconds):
    seconds = max(0.0, float(seconds)) * scale()
    if seconds:
        time.sleep(seconds)


def waits(schedule):
    """Yield (attempt, last) for each attempt, sleeping the scheduled seconds before each retry."""
    for attempt, delay in enumerate(schedule, start=1):
        if delay:
            sleep(delay)
        yield attempt, attempt == len(schedule)
