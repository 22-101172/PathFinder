"""
SAE background scheduler.

Runs a nightly job at 02:00 that refreshes the advisor overview cache.
This keeps GET /sae/advisor/overview fast at all hours — it reads from
the pre-computed JSON file rather than re-running the full feature pipeline.

Usage (start alongside a FastAPI app)
--------------------------------------
    from sae.scheduler import start_scheduler, stop_scheduler

    @app.on_event("startup")
    def startup():
        start_scheduler()

    @app.on_event("shutdown")
    def shutdown():
        stop_scheduler()

Or run as a standalone process (blocks until interrupted):
    python -m sae.scheduler
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# ── Job ───────────────────────────────────────────────────────────────────────

def _nightly_overview_refresh() -> None:
    """
    Recompute the advisor overview and write it to the 24-hour cache.
    Runs at 02:00 every night.
    """
    from sae.engine import refresh_overview_cache

    logger.info("SAE scheduler: starting nightly overview refresh…")
    try:
        n = refresh_overview_cache()
        logger.info("SAE scheduler: cached %d student records at %s", n, datetime.now())
    except Exception:
        logger.exception("SAE scheduler: overview refresh failed")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_scheduler(hour: int = 2, minute: int = 0) -> BackgroundScheduler:
    """
    Start the background scheduler and return it.

    Parameters
    ----------
    hour, minute
        Local time for the nightly cache refresh (default: 02:00).
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning("SAE scheduler is already running — ignoring start request.")
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _nightly_overview_refresh,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="sae_overview_refresh",
        replace_existing=True,
        misfire_grace_time=3600,   # allow up to 1-hour late start
    )
    _scheduler.start()
    logger.info(
        "SAE scheduler started — nightly overview refresh at %02d:%02d UTC.", hour, minute
    )
    return _scheduler


def stop_scheduler() -> None:
    """Gracefully shut down the background scheduler."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("SAE scheduler stopped.")
    _scheduler = None


def trigger_now() -> None:
    """Manually fire the refresh job immediately (useful for testing)."""
    logger.info("SAE scheduler: manual trigger requested.")
    _nightly_overview_refresh()


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Running SAE scheduler in standalone mode.")
    logger.info("Triggering an immediate cache refresh for verification…")
    trigger_now()

    scheduler = start_scheduler()
    logger.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_scheduler()
        logger.info("Exiting.")
