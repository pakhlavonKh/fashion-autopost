"""Unit tests for APScheduler interval and cron registration.

Per SDD §3.8 and SRS FR-8.
"""

from unittest.mock import MagicMock
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from config.app_config import ScheduleSettings
from scheduler.build_scheduler import build_scheduler, register_schedule_jobs


def test_register_schedule_interval_jobs() -> None:
    """Verify that interval_minutes registers an IntervalTrigger job every N minutes."""
    scheduler = BackgroundScheduler()
    runner = MagicMock()
    schedule_cfg = ScheduleSettings(interval_minutes=15, times=[])

    register_schedule_jobs(scheduler, runner, schedule_cfg)

    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "pipeline_cycle_interval_15m"
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval.total_seconds() == 15 * 60


def test_register_schedule_cron_and_interval_coexistence() -> None:
    """Verify that both interval and daily cron times can coexist and be hot-updated."""
    scheduler = BackgroundScheduler()
    runner = MagicMock()
    schedule_cfg = ScheduleSettings(interval_minutes=15, times=["10:00", "18:00"])

    register_schedule_jobs(scheduler, runner, schedule_cfg)

    jobs = scheduler.get_jobs()
    assert len(jobs) == 3
    job_ids = {j.id for j in jobs}
    assert "pipeline_cycle_interval_15m" in job_ids
    assert "pipeline_cycle_cron_0_1000" in job_ids
    assert "pipeline_cycle_cron_1_1800" in job_ids

    # Hot update to interval only: previous jobs should be cleared
    schedule_cfg_updated = ScheduleSettings(interval_minutes=30, times=[])
    register_schedule_jobs(scheduler, runner, schedule_cfg_updated)

    new_jobs = scheduler.get_jobs()
    assert len(new_jobs) == 1
    assert new_jobs[0].id == "pipeline_cycle_interval_30m"
    assert new_jobs[0].trigger.interval.total_seconds() == 30 * 60


def test_build_scheduler_non_blocking() -> None:
    """Verify build_scheduler creates a scheduler configured with interval jobs."""
    runner = MagicMock()
    schedule_cfg = ScheduleSettings(interval_minutes=15, times=[])
    scheduler = build_scheduler(schedule_cfg, runner, blocking=False)
    assert isinstance(scheduler, BackgroundScheduler)
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "pipeline_cycle_interval_15m"
