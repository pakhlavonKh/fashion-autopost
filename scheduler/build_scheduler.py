"""APScheduler integration for scheduled pipeline execution.

Per SDD §3.8 and SRS FR-8.
Registers cron triggers for specified daily publishing times and supports hot schedule updates.
Features independent schedules for Telegram and Instagram, plus configurable Instagram publication jitter.
"""

import logging
import random
import time
import zoneinfo
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.app_config import ScheduleSettings
from core.pipeline import PipelineRunner

logger = logging.getLogger(__name__)


def generate_schedule_times(start_time: str, end_time: str, interval_minutes: int) -> list[str]:
    """Generate daily time strings formatted as 'HH:MM' from start_time to end_time stepping by interval_minutes."""
    try:
        sh, sm = [int(p) for p in start_time.split(":", 1)]
        eh, em = [int(p) for p in end_time.split(":", 1)]
    except Exception:
        sh, sm = 6, 0
        eh, em = 23, 0

    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em
    step = interval_minutes if interval_minutes > 0 else 60

    if end_mins < start_mins:
        end_mins += 24 * 60

    times: list[str] = []
    current = start_mins
    while current <= end_mins:
        actual_min = current % (24 * 60)
        times.append(f"{actual_min // 60:02d}:{actual_min % 60:02d}")
        current += step

    return times


def register_schedule_jobs(
    scheduler: BaseScheduler,
    runner: PipelineRunner,
    schedule_cfg: ScheduleSettings,
) -> None:
    """Clear existing cycle jobs and register interval and cron triggers for publication and scrape checks."""
    # Remove previous pipeline and platform jobs
    for job in scheduler.get_jobs():
        if job.id.startswith(("pipeline_cycle_", "tg_sched_", "ig_sched_")):
            job.remove()

    try:
        tz = zoneinfo.ZoneInfo(schedule_cfg.timezone)
    except Exception as exc:
        logger.warning(
            "Invalid timezone '%s', defaulting to UTC: %s",
            schedule_cfg.timezone,
            exc,
        )
        tz = zoneinfo.ZoneInfo("UTC")

    repo = getattr(runner, "repo", None)

    def _setting(key: str, default: str) -> str:
        if not repo or not hasattr(repo, "get_system_setting"):
            return default
        try:
            value = repo.get_system_setting(key, default)
        except Exception as exc:
            logger.warning("Could not read schedule setting %s, using %s: %s", key, default, exc)
            return default
        text = str(value).strip() if value is not None else ""
        return text or default

    # 1. Independent Telegram Schedule (Feature 5)
    # Default: Hourly 06:00-23:00
    tg_enabled = _setting("telegram_schedule_enabled", "true").lower() == "true"
    tg_start = _setting("telegram_schedule_start_time", "06:00")
    tg_end = _setting("telegram_schedule_end_time", "23:00")
    try:
        tg_interval = int(_setting("telegram_schedule_interval_minutes", "60"))
    except ValueError:
        tg_interval = 60

    if tg_enabled and getattr(runner.config.telegram, "enabled", True):
        tg_times = generate_schedule_times(tg_start, tg_end, tg_interval)
        for idx, time_str in enumerate(tg_times):
            hour_str, minute_str = time_str.split(":")
            h, m = int(hour_str), int(minute_str)
            job_id = f"tg_sched_{h:02d}{m:02d}"
            trigger = CronTrigger(hour=h, minute=m, timezone=tz)
            scheduler.add_job(
                func=runner.run_telegram_cycle,
                trigger=trigger,
                id=job_id,
                name=f"Telegram Scheduled Publication at {time_str} ({schedule_cfg.timezone})",
                replace_existing=True,
                misfire_grace_time=300,
            )
        logger.info(
            "Configured independent Telegram schedule: %s-%s every %dm (%d jobs registered)",
            tg_start,
            tg_end,
            tg_interval,
            len(tg_times),
        )

    # 2. Independent Instagram Schedule & Randomization Window (Feature 5 & 6)
    # Default: 06:00-21:00 every 3 hours (180 mins), with 0-10 min randomization
    ig_enabled = _setting("instagram_schedule_enabled", "true").lower() == "true"
    ig_start = _setting("instagram_schedule_start_time", "06:00")
    ig_end = _setting("instagram_schedule_end_time", "21:00")
    try:
        ig_interval = int(_setting("instagram_schedule_interval_minutes", "180"))
    except ValueError:
        ig_interval = 180
    try:
        ig_window = int(_setting("instagram_schedule_random_window_minutes", "10"))
    except ValueError:
        ig_window = 10

    def _make_instagram_job(p_runner: PipelineRunner, max_jitter_mins: int):
        def _job():
            if max_jitter_mins > 0:
                jitter_sec = random.randint(0, max_jitter_mins * 60)
                logger.info(
                    "Instagram scheduled run applying %d sec randomization jitter (window: 0-%d min)...",
                    jitter_sec,
                    max_jitter_mins,
                )
                time.sleep(jitter_sec)
            p_runner.run_instagram_cycle()
        return _job

    if ig_enabled and getattr(runner.config.instagram, "enabled", True):
        ig_times = generate_schedule_times(ig_start, ig_end, ig_interval)
        for idx, time_str in enumerate(ig_times):
            hour_str, minute_str = time_str.split(":")
            h, m = int(hour_str), int(minute_str)
            job_id = f"ig_sched_{h:02d}{m:02d}"
            trigger = CronTrigger(hour=h, minute=m, timezone=tz)
            scheduler.add_job(
                func=_make_instagram_job(runner, ig_window),
                trigger=trigger,
                id=job_id,
                name=f"Instagram Scheduled Publication at {time_str} [+{ig_window}m jitter] ({schedule_cfg.timezone})",
                replace_existing=True,
                misfire_grace_time=600,
            )
        logger.info(
            "Configured independent Instagram schedule: %s-%s every %dm with %dm jitter (%d jobs registered)",
            ig_start,
            ig_end,
            ig_interval,
            ig_window,
            len(ig_times),
        )

    # 3. Optional interval job for autonomous recurrent background scraping
    if schedule_cfg.interval_minutes and schedule_cfg.interval_minutes > 0:
        job_id = f"pipeline_cycle_interval_{schedule_cfg.interval_minutes}m"
        trigger = IntervalTrigger(minutes=schedule_cfg.interval_minutes, timezone=tz)
        scheduler.add_job(
            func=runner.run_cycle,
            trigger=trigger,
            id=job_id,
            name=f"Fashion Autopost Run every {schedule_cfg.interval_minutes}m ({schedule_cfg.timezone})",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info(
            "Registered interval scheduled job '%s' every %d minutes (%s)",
            job_id,
            schedule_cfg.interval_minutes,
            schedule_cfg.timezone,
        )

    # YAML clock times are a Telegram-only fallback. They must not also publish
    # Instagram: that channel has its own window (default 06:00-21:00 every 3 hours).
    if schedule_cfg.times and not tg_enabled:
        for idx, time_str in enumerate(schedule_cfg.times):
            hour_str, minute_str = time_str.split(":")
            hour, minute = int(hour_str), int(minute_str)

            job_id = f"pipeline_cycle_cron_{idx}_{hour:02d}{minute:02d}"
            trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)

            scheduler.add_job(
                func=runner.run_telegram_cycle,
                trigger=trigger,
                id=job_id,
                name=f"Telegram fallback publication at {time_str} {schedule_cfg.timezone}",
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info(
                "Registered Telegram fallback job '%s' at %02d:%02d (%s)",
                job_id,
                hour,
                minute,
                schedule_cfg.timezone,
            )


def build_scheduler(
    schedule_cfg: ScheduleSettings,
    runner: PipelineRunner,
    blocking: bool = True,
) -> BaseScheduler:
    """Instantiate and configure APScheduler with configured publication times."""
    if blocking:
        scheduler: BaseScheduler = BlockingScheduler()
    else:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()

    register_schedule_jobs(scheduler, runner, schedule_cfg)
    return scheduler
