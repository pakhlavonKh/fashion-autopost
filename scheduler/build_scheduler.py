"""APScheduler integration for scheduled pipeline execution.

Per SDD §3.8 and SRS FR-8.
Registers cron triggers for specified daily publishing times and supports hot schedule updates.
"""

import logging
import zoneinfo
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger

from config.app_config import ScheduleSettings
from core.pipeline import PipelineRunner

logger = logging.getLogger(__name__)


def register_schedule_jobs(
    scheduler: BaseScheduler,
    runner: PipelineRunner,
    schedule_cfg: ScheduleSettings,
) -> None:
    """Clear existing cycle jobs and register cron triggers for configured publication times."""
    # Remove previous pipeline jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("pipeline_cycle_"):
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

    for idx, time_str in enumerate(schedule_cfg.times):
        hour_str, minute_str = time_str.split(":")
        hour, minute = int(hour_str), int(minute_str)

        job_id = f"pipeline_cycle_{idx}_{hour:02d}{minute:02d}"
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)

        scheduler.add_job(
            func=runner.run_cycle,
            trigger=trigger,
            id=job_id,
            name=f"Fashion Autopost Run at {time_str} {schedule_cfg.timezone}",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info(
            "Registered scheduled job '%s' at %02d:%02d (%s)",
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
