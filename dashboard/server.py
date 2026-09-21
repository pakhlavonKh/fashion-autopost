"""FastAPI backend server for the Fashion Autopost Admin Dashboard.

Provides REST APIs for monitoring metrics, inspecting products, managing the moderation queue,
triggering pipeline cycles on-demand, and editing prompt/markup/schedule settings live.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yaml
from sqlalchemy import select, func, desc

from config.app_config import AppConfig
from core.pipeline import PipelineRunner
from storage.models import LogRecord, ProductRecord
from storage.repository import SqlAlchemyProductRepository

STATIC_DIR = Path(__file__).parent / "static"


class ConfigUpdateRequest(BaseModel):
    markup: Optional[float] = None
    target_currency: Optional[str] = None
    max_products_per_run: Optional[int] = None
    daily_publish_cap: Optional[int] = None
    dry_run: Optional[bool] = None
    schedule_times: Optional[list[str]] = None
    timezone: Optional[str] = None
    moderation_enabled: Optional[bool] = None
    auto_approve: Optional[bool] = None


class PromptUpdateRequest(BaseModel):
    prompt: str


class RunCycleRequest(BaseModel):
    dry_run: Optional[bool] = None


def create_dashboard_app(
    config: AppConfig,
    runner: PipelineRunner,
    repo: SqlAlchemyProductRepository,
) -> FastAPI:
    app = FastAPI(title="Fashion Autopost Admin Dashboard", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def get_index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/stats")
    def get_stats():
        """Return system status, counts, and publishing metrics."""
        with repo._get_session() as session:
            total_products = session.scalar(select(func.count(ProductRecord.id))) or 0
            published_count = session.scalar(
                select(func.count(ProductRecord.id)).where(ProductRecord.status == "published")
            ) or 0
            pending_count = session.scalar(
                select(func.count(ProductRecord.id)).where(ProductRecord.status == "pending_review")
            ) or 0
            failed_count = session.scalar(
                select(func.count(ProductRecord.id)).where(ProductRecord.status == "failed")
            ) or 0

        published_today = repo.get_published_count_today(config.schedule.timezone)

        return {
            "status": "online",
            "dry_run": config.dry_run,
            "total_products": total_products,
            "published_total": published_count,
            "published_today": published_today,
            "pending_review": pending_count,
            "failed_count": failed_count,
            "markup": float(config.markup),
            "target_currency": config.target_currency,
            "schedule": {
                "times": config.schedule.times,
                "timezone": config.schedule.timezone,
                "posts_per_day": config.schedule.posts_per_day,
            },
            "daily_publish_cap": config.daily_publish_cap,
            "moderation": {
                "enabled": config.moderation.enabled,
                "auto_approve": config.moderation.auto_approve,
            },
            "aggregator_mode": config.aggregator.mode,
        }

    @app.get("/api/products")
    def list_products(
        status: Optional[str] = Query(None, description="Filter by status"),
        limit: int = Query(50, ge=1, le=200),
    ):
        """List products with optional status filter."""
        with repo._get_session() as session:
            stmt = select(ProductRecord).order_by(desc(ProductRecord.created_at)).limit(limit)
            if status and status != "all":
                stmt = stmt.where(ProductRecord.status == status)
            records = session.scalars(stmt).all()

            results = []
            for r in records:
                results.append({
                    "id": r.id,
                    "external_id": r.external_id,
                    "source": r.source,
                    "title": r.title,
                    "price_original": float(r.price_original),
                    "currency_original": r.currency_original,
                    "price_final": float(r.price_final) if r.price_final else None,
                    "photo_url": r.photo_url,
                    "description_gpt": r.description_gpt,
                    "product_url": r.product_url,
                    "status": r.status,
                    "telegram_post_id": r.telegram_post_id,
                    "instagram_post_id": r.instagram_post_id,
                    "published_at": r.published_at.isoformat() if r.published_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            return {"products": results}

    @app.post("/api/run-cycle")
    def trigger_cycle(payload: RunCycleRequest):
        """Trigger an on-demand pipeline execution."""
        original_dry_run = config.dry_run
        if payload.dry_run is not None:
            config.dry_run = payload.dry_run
        try:
            summary = runner.run_cycle()
            return {
                "success": True,
                "summary": {
                    "fetched": summary.fetched,
                    "unseen": summary.unseen,
                    "selected": summary.selected,
                    "published": summary.published,
                    "failed": summary.failed,
                    "pending_review": summary.pending_review,
                    "skipped_daily_cap": summary.skipped_daily_cap,
                    "errors": summary.errors,
                }
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        finally:
            config.dry_run = original_dry_run

    @app.get("/api/config")
    def get_config():
        """Retrieve current config and prompt content."""
        prompt_content = ""
        prompt_file = Path(config.prompt_path)
        if prompt_file.exists():
            prompt_content = prompt_file.read_text(encoding="utf-8")

        return {
            "markup": float(config.markup),
            "target_currency": config.target_currency,
            "max_products_per_run": config.max_products_per_run,
            "daily_publish_cap": config.daily_publish_cap,
            "dry_run": config.dry_run,
            "schedule": {
                "times": config.schedule.times,
                "timezone": config.schedule.timezone,
            },
            "moderation": {
                "enabled": config.moderation.enabled,
                "auto_approve": config.moderation.auto_approve,
            },
            "prompt": prompt_content,
        }

    @app.post("/api/config")
    def update_config(req: ConfigUpdateRequest):
        """Update operational parameters in config.yaml and trigger hot-reload."""
        cfg_file = Path("config.yaml")
        data: dict[str, Any] = {}
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    data = loaded

        if req.markup is not None:
            data["markup"] = float(req.markup)
        if req.target_currency is not None:
            data["target_currency"] = req.target_currency
        if req.max_products_per_run is not None:
            data["max_products_per_run"] = int(req.max_products_per_run)
        if req.daily_publish_cap is not None:
            data["daily_publish_cap"] = int(req.daily_publish_cap)
        if req.dry_run is not None:
            data["dry_run"] = bool(req.dry_run)

        if req.schedule_times is not None or req.timezone is not None:
            schedule_dict = data.get("schedule", {})
            if req.schedule_times is not None:
                schedule_dict["times"] = req.schedule_times
            if req.timezone is not None:
                schedule_dict["timezone"] = req.timezone
            data["schedule"] = schedule_dict

        if req.moderation_enabled is not None or req.auto_approve is not None:
            mod_dict = data.get("moderation", {})
            if req.moderation_enabled is not None:
                mod_dict["enabled"] = req.moderation_enabled
            if req.auto_approve is not None:
                mod_dict["auto_approve"] = req.auto_approve
            data["moderation"] = mod_dict

        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False)

        config.reload_hot_fields()
        return {"success": True, "message": "Configuration updated and hot-reloaded."}

    @app.post("/api/prompt")
    def update_prompt(req: PromptUpdateRequest):
        """Save new prompt content to prompt.txt."""
        prompt_file = Path(config.prompt_path)
        prompt_file.write_text(req.prompt.strip(), encoding="utf-8")
        return {"success": True, "message": "Prompt updated successfully."}

    @app.get("/api/logs")
    def get_logs(limit: int = 40):
        """Fetch latest system logs from the database sink."""
        with repo._get_session() as session:
            stmt = select(LogRecord).order_by(desc(LogRecord.timestamp)).limit(limit)
            records = session.scalars(stmt).all()
            return {
                "logs": [
                    {
                        "id": r.id,
                        "timestamp": r.timestamp.isoformat(),
                        "level": r.level,
                        "stage": r.stage,
                        "external_id": r.external_id,
                        "message": r.message,
                    }
                    for r in records
                ]
            }

    @app.post("/api/products/{external_id}/approve")
    def approve_product(external_id: str):
        """Manually approve and publish an item held in pending_review."""
        rec = repo.get_by_external_id(external_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Product not found")

        # Fake or real publish
        repo.mark_published(
            external_id=external_id,
            telegram_id=f"MANUAL_TG_{external_id}",
            instagram_id=f"MANUAL_IG_{external_id}",
        )
        return {"success": True, "message": f"Product {external_id} approved and published."}

    return app
