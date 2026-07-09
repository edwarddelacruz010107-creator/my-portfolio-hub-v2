"""
app/services/scheduler_lock.py — PostgreSQL Advisory Lock for Scheduler Safety

Prevents duplicate APScheduler instances when multiple Gunicorn workers or
Render instances start simultaneously.

Usage in _init_scheduler():
    from app.services.scheduler_lock import acquire_scheduler_lock
    if not acquire_scheduler_lock(app):
        return  # Another worker/process is running the scheduler
"""

import logging
import threading

logger = logging.getLogger(__name__)

# In-process lock: prevents two threads within the SAME process from both
# starting the scheduler. Combined with ENABLE_SCHEDULER env-var check.
_process_lock = threading.Lock()
_scheduler_claimed = False


def acquire_scheduler_lock(app) -> bool:
    """
    Attempt to acquire the scheduler singleton lock.
    
    Returns True if this process/worker should run the scheduler.
    Returns False if another worker already holds the lock.
    
    Strategy (defense in depth):
      1. In-process threading.Lock — prevents same-process race
      2. PostgreSQL advisory lock — prevents multi-worker/multi-instance race
         (only when CORE_DATABASE_URL is a PostgreSQL database)
    """
    global _scheduler_claimed

    # 1. In-process guard
    with _process_lock:
        if _scheduler_claimed:
            logger.info('Scheduler lock: already held by this process — skipping.')
            return False
        _scheduler_claimed = True

    # 2. PostgreSQL advisory lock (best-effort; skipped for SQLite)
    try:
        from app import db
        with app.app_context():
            dialect = db.engine.dialect.name
            if dialect == 'postgresql':
                SCHEDULER_LOCK_ID = 7919
                result = db.session.execute(
                    db.text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": SCHEDULER_LOCK_ID},
                ).scalar()
                db.session.remove()
                if result:
                    logger.info(
                        'Scheduler PG advisory lock acquired (lock_id=%d). '
                        'This worker will run APScheduler.',
                        SCHEDULER_LOCK_ID,
                    )
                    return True

                logger.info(
                    'Scheduler PG advisory lock NOT acquired (lock_id=%d). '
                    'Another worker holds the lock — this worker will skip APScheduler.',
                    SCHEDULER_LOCK_ID,
                )
                with _process_lock:
                    _scheduler_claimed = False
                return False
    except Exception as exc:
        logger.warning(
            'Scheduler PG advisory lock attempt failed (%s) — '
            'scheduler will remain disabled in this worker.',
            exc,
        )
        with _process_lock:
            _scheduler_claimed = False
        return False
    
    return True
