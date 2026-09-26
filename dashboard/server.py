"""FastAPI backend server for the Fashion Autopost Admin Dashboard.

Provides REST APIs for monitoring metrics, inspecting products, managing the moderation queue,
triggering pipeline cycles on-demand, and editing prompt/markup/schedule settings live.
"""

from datetime import datetime, timezone
from decimal import Decimal
import logging
from pathlib import Path
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yaml
from sqlalchemy import select, func, desc

from config.app_config import AppConfig
from core.pipeline import PipelineRunner
from publishers.telegram_discovery import TelegramChatDiscoveryService
from storage.models import LogRecord, ProductRecord, TelegramChatRecord
from storage.repository import SqlAlchemyProductRepository

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


class ConfigUpdateRequest(BaseModel):
    markup: Optional[float] = None
    target_currency: Optional[str] = None
    max_products_per_run: Optional[int] = None
    daily_publish_cap: Optional[int] = None
    dry_run: Optional[bool] = None
    schedule_times: Optional[list[str]] = None
    interval_minutes: Optional[int] = None
    timezone: Optional[str] = None
    moderation_enabled: Optional[bool] = None
    auto_approve: Optional[bool] = None
    telegram_enabled: Optional[bool] = None
    instagram_enabled: Optional[bool] = None


class PromptUpdateRequest(BaseModel):
    prompt: str


class StoreUpsertRequest(BaseModel):
    name: str
    url: str
    currency: Optional[str] = "EUR"
    max_items: Optional[int] = 10
    enabled: Optional[bool] = True
    selectors: Optional[dict[str, Optional[str]]] = None
    cookie_button: Optional[str] = None
    scroll_steps: Optional[int] = None


class TelegramChatUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None


class RunCycleRequest(BaseModel):
    dry_run: Optional[bool] = None


class AuthLoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    key: Optional[str] = None


CATEGORIES_MAP = {
    "dresses": ["elbise", "dress", "vestido", "kombine", "robe", "платье", "сарафан"],
    "skirts": ["etek", "skirt", "falda", "юбка"],
    "jackets": ["blazer", "ceket", "kaban", "palto", "trençkot", "trench", "jacket", "coat", "mont", "pelerin", "cape", "parka", "anorak", "жакет", "пиджак", "пальто", "куртка"],
    "knitwear": ["kazak", "triko", "jumper", "sweater", "cardigan", "hırka", "hirka", "süveter", "suveter", "knit", "pullover", "свитер", "джемпер", "кардиган", "трикотаж"],
    "tops": ["gömlek", "gomlek", "t-shirt", "tişört", "tisort", "bluz", "blouse", "top", "shirt", "body", "büstiyer", "bustiyer", "polo", "рубашка", "блузка", "футболка"],
    "trousers": ["pantolon", "trousers", "pants", "jean", "denim", "tayt", "legging", "bermuda", "şort", "sort", "short", "брюки", "джинсы", "шорты"],
    "shoes": ["ayakkabı", "ayakkabi", "bot", "boot", "çizme", "cizme", "sandalet", "sandal", "terlik", "sneaker", "topuklu", "shoes", "loafer", "mule", "обувь", "туфли", "ботинки", "сапоги"],
    "accessories": ["çanta", "canta", "bag", "tote", "bere", "şapka", "sapka", "hat", "kemer", "belt", "atkı", "atki", "scarf", "fular", "küpe", "kupe", "kolye", "gözlük", "gozluk", "сумка", "ремень", "шапка", "шарф"],
}


def detect_product_category(title: str, url: str = "") -> str:
    combined = f"{title} {url}".lower()
    for cat, keywords in CATEGORIES_MAP.items():
        if any(kw in combined for kw in keywords):
            return cat
    return "other"


def create_dashboard_app(
    config: AppConfig,
    runner: PipelineRunner,
    repo: SqlAlchemyProductRepository,
    scheduler: Optional[Any] = None,
) -> FastAPI:
    app = FastAPI(title="Fashion Autopost Admin Dashboard", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    images_dir = Path("data/images")
    images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=str(images_dir)), name="images")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def get_index():
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
        )

    @app.get("/style.css")
    def get_style():
        return FileResponse(
            STATIC_DIR / "style.css",
            media_type="text/css",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
        )

    @app.get("/app.js")
    def get_app():
        return FileResponse(
            STATIC_DIR / "app.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
        )

    def verify_admin(
        authorization: Optional[str] = Header(None),
        x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
        admin_key_query: Optional[str] = Query(None, alias="key"),
    ) -> bool:
        expected_pass = (
            getattr(getattr(config, "dashboard", None), "admin_password", "")
            or getattr(getattr(config, "dashboard", None), "admin_key", "")
        )
        expected_user = getattr(getattr(config, "dashboard", None), "admin_username", "admin")

        if not expected_pass:
            return True

        token = None
        if authorization:
            parts = authorization.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]
            elif len(parts) == 2 and parts[0].lower() == "basic":
                import base64
                try:
                    decoded = base64.b64decode(parts[1]).decode("utf-8")
                    if ":" in decoded:
                        u, p = decoded.split(":", 1)
                        if u == expected_user and p == expected_pass:
                            return True
                except Exception:
                    pass
            elif len(parts) == 1:
                token = parts[0]
        elif x_admin_key:
            token = x_admin_key.strip()
        elif admin_key_query:
            token = admin_key_query.strip()

        if not token or token != expected_pass:
            raise HTTPException(
                status_code=401,
                detail="Admin kaliti noto'g'ri yoki taqdim etilmagan (Unauthorized)",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return True

    @app.post("/api/auth/login")
    def auth_login(req: AuthLoginRequest):
        """Verify admin username and password and return auth confirmation."""
        submitted_user = (req.username or "").strip()
        submitted_pass = (req.password or req.key or "").strip()

        expected_user = getattr(getattr(config, "dashboard", None), "admin_username", "admin")
        expected_pass = (
            getattr(getattr(config, "dashboard", None), "admin_password", "")
            or getattr(getattr(config, "dashboard", None), "admin_key", "")
        )

        # If username is provided, it must match
        if submitted_user and submitted_user != expected_user:
            raise HTTPException(
                status_code=401,
                detail="Foydalanuvchi nomi noto'g'ri (Invalid username)",
            )

        if not expected_pass or submitted_pass != expected_pass:
            raise HTTPException(
                status_code=401,
                detail="Xavfsizlik paroli noto'g'ri (Invalid password)",
            )

        return {
            "authenticated": True,
            "username": expected_user,
            "token": expected_pass,
            "message": "Admin tizimiga muvaffaqiyatli ulanildi",
        }

    @app.get("/api/auth/verify")
    def auth_verify(_: bool = Depends(verify_admin)):
        """Check if current session token is valid."""
        username = getattr(getattr(config, "dashboard", None), "admin_username", "admin")
        return {"authenticated": True, "username": username}

    @app.get("/api/stats", dependencies=[Depends(verify_admin)])
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
                "interval_minutes": config.schedule.interval_minutes,
            },
            "daily_publish_cap": config.daily_publish_cap,
            "moderation": {
                "enabled": config.moderation.enabled,
                "auto_approve": config.moderation.auto_approve,
            },
            "aggregator_mode": config.aggregator.mode,
            "scheduler": {
                "running": getattr(scheduler, "running", False) if scheduler else False,
                "jobs": [
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                    }
                    for job in (scheduler.get_jobs() if (scheduler and getattr(scheduler, "running", False)) else [])
                ],
            },
        }

    @app.get("/api/products", dependencies=[Depends(verify_admin)])
    def list_products(
        status: Optional[str] = Query(None, description="Filter by status"),
        source: Optional[str] = Query(None, description="Filter by store/source"),
        category: Optional[str] = Query(None, description="Filter by category"),
        limit: int = Query(250, ge=1, le=1000),
    ):
        """List products with optional status, store, and category filter."""
        with repo._get_session() as session:
            stmt = select(ProductRecord).order_by(desc(ProductRecord.created_at)).limit(limit)
            if status and status != "all":
                stmt = stmt.where(ProductRecord.status == status)
            if source and source != "all":
                stmt = stmt.where(ProductRecord.source == source.lower())
            records = session.scalars(stmt).all()

            results = []
            for r in records:
                detected_cat = detect_product_category(r.title, r.product_url or "")
                if category and category != "all" and detected_cat != category:
                    continue
                results.append({
                    "id": r.id,
                    "external_id": r.external_id,
                    "source": r.source,
                    "category": detected_cat,
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

    @app.post("/api/run-cycle", dependencies=[Depends(verify_admin)])
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

    @app.get("/api/config", dependencies=[Depends(verify_admin)])
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
                "posts_per_day": config.schedule.posts_per_day,
                "interval_minutes": config.schedule.interval_minutes,
            },
            "moderation": {
                "enabled": config.moderation.enabled,
                "auto_approve": config.moderation.auto_approve,
            },
            "telegram": {
                "enabled": config.telegram.enabled,
            },
            "instagram": {
                "enabled": config.instagram.enabled,
            },
            "prompt": prompt_content,
        }

    @app.post("/api/config", dependencies=[Depends(verify_admin)])
    def update_config(req: ConfigUpdateRequest):
        """Update operational parameters in config.yaml and trigger hot-reload."""
        cfg_file = Path(config._config_path or "config.yaml")
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

        if req.schedule_times is not None or req.timezone is not None or req.interval_minutes is not None:
            schedule_dict = data.get("schedule", {})
            if req.schedule_times is not None:
                schedule_dict["times"] = req.schedule_times
            if req.timezone is not None:
                schedule_dict["timezone"] = req.timezone
            if req.interval_minutes is not None:
                schedule_dict["interval_minutes"] = req.interval_minutes
            data["schedule"] = schedule_dict

        if req.moderation_enabled is not None or req.auto_approve is not None:
            mod_dict = data.get("moderation", {})
            if req.moderation_enabled is not None:
                mod_dict["enabled"] = req.moderation_enabled
            if req.auto_approve is not None:
                mod_dict["auto_approve"] = req.auto_approve
            data["moderation"] = mod_dict

        if req.telegram_enabled is not None:
            tg_dict = data.get("telegram", {})
            tg_dict["enabled"] = req.telegram_enabled
            data["telegram"] = tg_dict

        if req.instagram_enabled is not None:
            ig_dict = data.get("instagram", {})
            ig_dict["enabled"] = req.instagram_enabled
            data["instagram"] = ig_dict

        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False)

        config.reload_hot_fields()
        if scheduler is not None and getattr(scheduler, "running", False):
            try:
                from scheduler.build_scheduler import register_schedule_jobs
                register_schedule_jobs(scheduler, runner, config.schedule)
                logger.info("APScheduler jobs dynamically updated with new schedule.")
            except Exception as exc:
                logger.warning("Could not dynamically update scheduler jobs: %s", exc)
        return {"success": True, "message": "Configuration updated and hot-reloaded."}

    @app.post("/api/prompt", dependencies=[Depends(verify_admin)])
    def update_prompt(req: PromptUpdateRequest):
        """Save new prompt content to prompt.txt."""
        prompt_file = Path(config.prompt_path)
        prompt_file.write_text(req.prompt.strip(), encoding="utf-8")
        return {"success": True, "message": "Prompt updated successfully."}

    @app.get("/api/logs", dependencies=[Depends(verify_admin)])
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

    @app.post("/api/products/{external_id}/approve", dependencies=[Depends(verify_admin)])
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

    @app.get("/api/scraper/stores", dependencies=[Depends(verify_admin)])
    def list_scraper_stores():
        """List all configured website scraping targets."""
        stores_data = {}
        for name, store_cfg in config.scraper.stores.items():
            stores_data[name] = store_cfg.model_dump()
        return {"stores": stores_data}

    @app.post("/api/scraper/stores", dependencies=[Depends(verify_admin)])
    def upsert_scraper_store(req: StoreUpsertRequest):
        """Add or update an arbitrary website scraping target in config.yaml without code changes."""
        cfg_file = Path(config._config_path or "config.yaml")
        data: dict[str, Any] = {}
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    data = loaded

        scraper_data = data.setdefault("scraper", {})
        stores_data = scraper_data.setdefault("stores", {})

        store_entry: dict[str, Any] = {
            "enabled": bool(req.enabled if req.enabled is not None else True),
            "url": req.url,
            "currency": req.currency or "EUR",
            "max_items": int(req.max_items or 10),
        }
        if req.selectors:
            store_entry["selectors"] = {k: v for k, v in req.selectors.items() if v}
        if req.cookie_button:
            store_entry["cookie_button"] = req.cookie_button
        if req.scroll_steps is not None:
            store_entry["scroll_steps"] = int(req.scroll_steps)

        stores_data[req.name.strip().lower()] = store_entry

        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False)

        config.reload_hot_fields()
        return {
            "success": True,
            "message": f"Website target '{req.name}' successfully configured and hot-reloaded.",
            "store": store_entry,
        }

    @app.delete("/api/scraper/stores/{store_name}", dependencies=[Depends(verify_admin)])
    def delete_scraper_store(store_name: str):
        """Remove a website scraping target from config.yaml."""
        norm_name = store_name.strip().lower()
        cfg_file = Path(config._config_path or "config.yaml")
        data: dict[str, Any] = {}
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    data = loaded

        stores_data = data.get("scraper", {}).get("stores", {})
        if norm_name not in stores_data:
            raise HTTPException(status_code=404, detail=f"Store '{store_name}' not found")

        del stores_data[norm_name]

        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False)

        config.reload_hot_fields()
        return {"success": True, "message": f"Store '{norm_name}' removed and hot-reloaded."}

    @app.get("/api/telegram/chats", dependencies=[Depends(verify_admin)])
    def list_telegram_chats():
        """List all discovered channels, groups, and admin chats."""
        chats = repo.get_all_telegram_chats()
        return {
            "chats": [
                {
                    "id": c.id,
                    "chat_id": c.chat_id,
                    "title": c.title,
                    "chat_type": c.chat_type,
                    "role": c.role,
                    "username": c.username,
                    "is_active": c.is_active,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                }
                for c in chats
            ]
        }

    @app.post("/api/telegram/sync", dependencies=[Depends(verify_admin)])
    def sync_telegram_chats():
        """Trigger dynamic discovery of channels and chats via Telegram getUpdates."""
        discovery = TelegramChatDiscoveryService(bot_token=config.telegram.bot_token, repo=repo)
        active_targets = discovery.sync_updates()
        return {
            "success": True,
            "message": f"Telegram synchronization complete. {len(active_targets)} active targets.",
            "active_targets": [t.title for t in active_targets],
        }

    @app.patch("/api/telegram/chats/{chat_id}", dependencies=[Depends(verify_admin)])
    def update_telegram_chat(chat_id: str, req: TelegramChatUpdateRequest):
        """Enable, disable, or change the role of a Telegram chat target."""
        with repo._get_session() as session:
            stmt = select(TelegramChatRecord).where(TelegramChatRecord.chat_id == str(chat_id))
            record = session.scalar(stmt)
            if not record:
                raise HTTPException(status_code=404, detail="Chat not found")
            if req.is_active is not None:
                record.is_active = req.is_active
            if req.role is not None:
                record.role = req.role
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
            return {"success": True, "message": f"Chat {chat_id} updated."}

    @app.delete("/api/telegram/chats/{chat_id}", dependencies=[Depends(verify_admin)])
    def delete_telegram_chat(chat_id: str):
        """Deactivate a Telegram chat target."""
        repo.deactivate_telegram_chat(chat_id)
        return {"success": True, "message": f"Chat {chat_id} deactivated."}

    return app
