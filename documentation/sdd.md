# Software Design Document (SDD)
## Automated Clothing Product Publishing System for Telegram & Instagram (GPT-powered)

Version 1.0 — Companion to SRS.md. This document describes *how* to build the system specified in the SRS.

---

## 1. Introduction

### 1.1 Purpose
This SDD translates the requirements in `SRS.md` into a concrete architecture, module breakdown, data design, and interface contracts, suitable for direct implementation by an AI coding agent (e.g., Antigravity / Gemini) or a human developer.

### 1.2 Design Goals
- **Modularity**: every external integration (data source, LLM, publisher) sits behind a small interface so it can be swapped or extended (NFR-2).
- **Config-driven**: nothing operational is hardcoded (NFR-1).
- **Fail-soft**: one failing product/API call must not halt the whole cycle (NFR-3).
- **Idempotent**: safe to re-run without duplicate publishes (NFR-5).
- **Testable in isolation**: dry-run mode plus decoupled business logic (NFR-8).

### 1.3 Recommended Technology Stack
- **Language**: Python 3.11+
- **Scheduler**: APScheduler (cron-style trigger, config-driven)
- **Database**: SQLite by default (single file, zero-ops); PostgreSQL as a drop-in alternative via the same ORM/repository layer (SQLAlchemy recommended)
- **LLM**: OpenAI API, model name from config
- **Telegram**: `python-telegram-bot` or direct Bot API HTTP calls
- **Instagram**: Instagram Graph API via `requests`/`httpx`
- **Optional image hosting**: any S3-compatible SDK (e.g., `boto3`) — only if aggregator photos aren't already public URLs
- **Config**: `.env` (via `python-dotenv`) + `config.yaml` for structured settings
- **Logging**: standard `logging` module → rotating file handler + optional DB sink

---

## 2. System Architecture

### 2.1 High-Level Pipeline

```
[Aggregator Adapter] ──▶ [Deduplication Service] ──▶ [LLM Selector]
                                                          │
                                                          ▼
                                              [LLM Description Generator]
                                                          │
                                                          ▼
                                                  [Pricing Engine]
                                                          │
                                                          ▼
                                                 [Post Composer]
                                                          │
                             ┌────────────────────────────┴───────────────────────────┐
                             ▼                                                        ▼
                  [Telegram Publisher]                                    [Instagram Publisher]
                             │                                                        │
                             └───────────────────────┬────────────────────────────────┘
                                                       ▼
                                          [Repository: write status/IDs]
                                                       │
                                                       ▼
                                             [Logger / Admin Notifier]
```

### 2.2 Orchestration
A single **PipelineRunner** (or `run_cycle()` function) is invoked by the **Scheduler** on each configured trigger. It executes the stages above in sequence, per product, catching and logging exceptions at the product level so one bad item doesn't abort the batch. The runner also supports a `dry_run: bool` flag threaded through to the publishing stage.

### 2.3 Layering
```
core/            → pure business logic (selection orchestration, pricing, composition) — no I/O
adapters/         → product source adapters (implements SourceAdapter interface)
llm/              → LLM provider(s) (implements LLMProvider interface)
publishers/       → Telegram / Instagram publishers (implements Publisher interface)
storage/          → DB models + repository (SQLAlchemy)
scheduler/        → APScheduler wiring, reads schedule from config
config/           → config loading (.env + yaml), validated via pydantic
logging/          → logger setup, admin notifier
main.py / cli.py  → composition root: wires everything together, entrypoint
```

This separation lets `core/` be unit-tested without touching any network or database code.

---

## 3. Module Design

### 3.1 Source Adapter (`adapters/`)

**Interface** (`SourceAdapter`):
```python
class SourceAdapter(Protocol):
    def fetch_products(self) -> list[RawProduct]:
        """Return normalized product records from this source."""
```

**RawProduct** (normalized DTO, regardless of source):
```python
@dataclass
class RawProduct:
    external_id: str
    source: str            # "zara", "mango", ...
    title: str
    price: Decimal
    currency: str
    photo_url: str          # may need re-hosting later if not public
    product_url: str
    in_stock: bool
```

**Implementation**: `AggregatorAPIAdapter(SourceAdapter)` — wraps the actual third-party aggregator's REST/polling client. Its internals (auth, pagination, rate-limit handling, retry) are fully encapsulated here; nothing outside this module should know the aggregator's wire format.

> Design note: since the exact aggregator contract is an open question (see SRS §10.1), build `AggregatorAPIAdapter` against a small internal client class (`AggregatorClient`) so that once the real API details are confirmed, only `AggregatorClient` needs updating — `AggregatorAPIAdapter` and everything downstream stays untouched.

### 3.2 Deduplication Service (`core/dedup.py`)
```python
def filter_unseen(products: list[RawProduct], repo: ProductRepository) -> list[RawProduct]:
    """Drop any product whose external_id already has status='published' in the DB."""
```
Pure function taking a repository interface (so it's testable with an in-memory fake).

### 3.3 LLM Provider (`llm/`)

**Interface** (`LLMProvider`):
```python
class LLMProvider(Protocol):
    def select_products(self, candidates: list[RawProduct], prompt: str, max_items: int) -> list[SelectionResult]:
        ...
```

**SelectionResult**:
```python
@dataclass
class SelectionResult:
    external_id: str
    description: str
```

**Implementation**: `OpenAIProvider(LLMProvider)`.
- Reads `prompt.txt` fresh on every call (FR-2.3) via an injected `PromptLoader`.
- Sends candidates (id, title, price) + prompt to the OpenAI API.
- Requests **strict JSON** output (use JSON-mode / response-format enforcement if the model supports it; otherwise instruct the prompt to output only JSON and validate with a schema, e.g. pydantic model + `json.loads` with a try/except fallback and one retry).
- On parse failure: log error, retry once, then skip the cycle gracefully (FR-2.6) rather than raising uncaught.

**Design note on extensibility**: keep `LLMProvider` generic enough that a future `AnthropicProvider` or local-model provider could implement it without changing `core/`.

### 3.4 Pricing Engine (`core/pricing.py`)
```python
def calculate_final_price(original_price: Decimal, currency: str, markup: Decimal,
                           target_currency: str, fx: FxConverter) -> Decimal:
    """price_final = convert(original_price, currency -> target_currency) + markup"""
```
`FxConverter` is itself an interface — `FixedRateConverter` (rate from config) as the default implementation, swappable for a `LiveRateConverter` later if the open question in SRS §10.3 resolves that way.

### 3.5 Post Composer (`core/composer.py`)
```python
@dataclass
class ComposedPost:
    photo_url: str
    text: str
    price: Decimal
    product_url: str | None

def compose_post(product: RawProduct, description: str, price: Decimal, include_link: bool) -> ComposedPost:
    ...
```
Platform-specific rendering (character limits, link formatting) happens at the **publisher** boundary, not here — `ComposedPost` stays platform-agnostic (FR-4.2).

### 3.6 Publishers (`publishers/`)

**Interface** (`Publisher`):
```python
class Publisher(Protocol):
    def publish(self, post: ComposedPost) -> PublishResult:
        ...
```

**PublishResult**:
```python
@dataclass
class PublishResult:
    success: bool
    platform_post_id: str | None
    error: str | None
```

**Implementations**:
- `TelegramPublisher(Publisher)`: formats text to fit Telegram limits, calls Bot API `sendPhoto` with caption.
- `InstagramPublisher(Publisher)`:
  1. Ensures `photo_url` is a public HTTPS URL — if not, calls an `ImageHostingService` (S3) to re-host it first.
  2. Uses Graph API's two-step container flow: create media container → publish container.
- Both implementations wrap their external HTTP calls with the shared **retry-with-backoff** utility (§3.9).

**Dry-run behavior**: a `DryRunPublisher(Publisher)` decorator/wrapper can wrap any real publisher — it logs what *would* be sent and returns a synthetic `PublishResult(success=True, platform_post_id="DRYRUN")` without making the network call. The pipeline runner selects real vs. dry-run publisher based on the config flag (FR-9.1), keeping the rest of the pipeline logic identical either way.

### 3.7 Repository / Storage (`storage/`)

**ORM Model** (`ProductRecord`) mirrors the `products` table from SRS §6.1 (SQLAlchemy model).

**Repository interface**:
```python
class ProductRepository(Protocol):
    def get_published_ids(self) -> set[str]: ...
    def upsert_new(self, product: RawProduct) -> None: ...
    def mark_selected(self, external_id: str, description: str, price_final: Decimal) -> None: ...
    def mark_published(self, external_id: str, telegram_id: str | None, instagram_id: str | None) -> None: ...
    def mark_failed(self, external_id: str, error: str) -> None: ...
```
Backed by SQLite by default; swapping to PostgreSQL is a connection-string change only (SQLAlchemy engine URL), no code change elsewhere (NFR-7).

### 3.8 Scheduler (`scheduler/`)
- Wraps APScheduler with a `BlockingScheduler` or `AsyncIOScheduler`.
- On startup, reads schedule config (times, posts/day) and (re-)registers cron jobs.
- Optionally supports a lightweight "reload" check (e.g., re-read config file at the top of each job, or watch the config file for changes) so schedule edits apply without a full restart (FR-8.2).
- Each triggered job calls `PipelineRunner.run_cycle()`.

### 3.9 Retry / Resilience Utility (`core/resilience.py`)
A shared decorator/helper providing exponential backoff retry, applied to all outbound calls to: aggregator API, OpenAI API, Telegram API, Instagram API, S3.
```python
@retry_with_backoff(max_attempts=3, base_delay=2, exceptions=(RequestException,))
def call_external_api(...): ...
```

### 3.10 Logging & Admin Notifications (`logging/`)
- Standard Python `logging` configured with a rotating file handler; optionally also writes structured entries to a `logs` DB table (per SRS §6.2).
- `AdminNotifier` interface, with `TelegramAdminNotifier` implementation sending a message to a configured admin chat ID when a critical error occurs (FR-7.3) — e.g., publish failure after retries exhausted.

### 3.11 Config Loader (`config/`)
- Loads `.env` (secrets: API keys/tokens) and `config.yaml` (operational params: markup, schedule, max items/run, prompt path, dry-run flag, currency logic).
- Validated at startup via a `pydantic.BaseModel` (`AppConfig`) so misconfiguration fails fast with a clear error instead of a cryptic runtime exception.
- `AppConfig` is re-loaded per pipeline cycle for the fields explicitly required to be hot-reloadable (prompt path/content, markup, schedule) — see FR-2.3, FR-3.2, FR-8.2. Secrets (tokens/keys) can remain loaded once per process.

### 3.12 Composition Root (`main.py`)
Wires concrete implementations into the abstract interfaces (dependency injection by hand — no framework needed at this scale):
```python
config = AppConfig.load()
repo = SqlAlchemyProductRepository(config.db_url)
source = AggregatorAPIAdapter(config.aggregator)
llm = OpenAIProvider(config.openai, prompt_loader=FilePromptLoader(config.prompt_path))
fx = FixedRateConverter(config.fx_rate)
telegram_pub = TelegramPublisher(config.telegram)
instagram_pub = InstagramPublisher(config.instagram, image_host=S3ImageHost(config.s3))
if config.dry_run:
    telegram_pub = DryRunPublisher(telegram_pub)
    instagram_pub = DryRunPublisher(instagram_pub)

runner = PipelineRunner(source, repo, llm, fx, [telegram_pub, instagram_pub], config)
scheduler = build_scheduler(config.schedule, runner)
scheduler.start()
```

---

## 4. Data Design

### 4.1 `products` Table
(as specified in SRS §6.1 — reproduced here as the authoritative schema for implementation)

```sql
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    price_original NUMERIC NOT NULL,
    currency_original TEXT NOT NULL,
    price_final NUMERIC,
    photo_url TEXT NOT NULL,
    description_gpt TEXT,
    product_url TEXT,
    status TEXT NOT NULL DEFAULT 'new',   -- new | selected | published | failed
    telegram_post_id TEXT,
    instagram_post_id TEXT,
    published_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_products_status ON products(status);
```

### 4.2 `logs` Table (optional DB sink, in addition to file logs)
```sql
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL,        -- INFO | WARNING | ERROR | CRITICAL
    stage TEXT,                 -- ingestion | selection | pricing | composition | publish_telegram | publish_instagram
    external_id TEXT,
    message TEXT NOT NULL
);
```

### 4.3 Config Schema (`config.yaml`, illustrative)
```yaml
markup: 15.00
target_currency: "USD"
fx:
  mode: "fixed"        # fixed | dynamic
  fixed_rate: 1.08
max_products_per_run: 5
prompt_path: "./prompt.txt"
dry_run: false
schedule:
  posts_per_day: 2
  times: ["10:00", "18:00"]
  timezone: "Europe/Istanbul"
db_url: "sqlite:///./data/app.db"
```

`.env` (secrets only):
```
AGGREGATOR_API_KEY=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL_ID=...
TELEGRAM_ADMIN_CHAT_ID=...
INSTAGRAM_ACCESS_TOKEN=...
INSTAGRAM_ACCOUNT_ID=...
S3_ENDPOINT=...
S3_BUCKET=...
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
```

---

## 5. Pipeline Sequence (per cycle)

1. `Scheduler` fires → `PipelineRunner.run_cycle()`.
2. `AppConfig` reloads hot-reloadable fields (prompt, markup, schedule, dry_run).
3. `SourceAdapter.fetch_products()` → `list[RawProduct]`.
4. `filter_unseen()` against `ProductRepository.get_published_ids()`.
5. New products persisted with `status='new'` (`upsert_new`).
6. `LLMProvider.select_products(candidates, prompt, max_items)` → `list[SelectionResult]`.
   - On JSON parse failure: log, retry once, else abort cycle cleanly.
7. For each selected item:
   a. `calculate_final_price(...)` via `PricingEngine` + `FxConverter`.
   b. `repo.mark_selected(external_id, description, price_final)`.
   c. `compose_post(...)` → `ComposedPost`.
   d. For each `Publisher` in `[telegram_pub, instagram_pub]`:
      - `publish(post)` wrapped in retry-with-backoff.
      - On success: collect `platform_post_id`.
      - On failure after retries: log error, notify admin (if critical), `repo.mark_failed(...)`.
   e. If all publishers succeeded (or dry-run): `repo.mark_published(external_id, telegram_id, instagram_id)`.
8. Cycle summary logged (counts: fetched, deduped, selected, published, failed).

---

## 6. Error Handling Strategy

| Failure point | Handling |
|---|---|
| Aggregator API unreachable/rate-limited | Retry w/ backoff (3 attempts); on final failure, log ERROR, skip cycle, continue to next scheduled run |
| LLM call fails or returns invalid JSON | Retry once; on final failure, log ERROR, skip cycle |
| Currency conversion misconfigured | Fail fast at config validation (pydantic) before any run starts |
| Telegram publish fails | Retry w/ backoff; on final failure, log ERROR + admin notify, mark product `failed` for that platform but continue with Instagram |
| Instagram publish fails | Same as above, independent of Telegram outcome |
| Both publishers fail for a product | `status='failed'`, product remains eligible for a future manual/automatic retry (do not mark `published`) |
| DB write failure | Log CRITICAL, notify admin — this affects idempotency guarantees, treat as high severity |

Design principle: **per-product isolation** — one product's failure must never abort processing of the remaining batch (supports NFR-3).

---

## 7. Extensibility Design

| To add... | Do this |
|---|---|
| A new product source (e.g., a third clothing brand via a different aggregator) | Implement `SourceAdapter`, register in composition root; no changes to `core/` |
| A new LLM provider | Implement `LLMProvider`; swap in composition root |
| A new social network | Implement `Publisher`; add to the publisher list in composition root |
| Dynamic FX rates | Implement `FxConverter` (e.g., `LiveRateConverter` calling a rates API); swap in composition root |
| Manual moderation step (pending SRS open question §10.2) | Insert an optional `ModerationGate` stage between "selected" and "publish" that holds posts in a `pending_review` status until approved (e.g., via an admin Telegram command) — designed as an optional pipeline stage so it doesn't affect the fully-automatic path if not needed |

---

## 8. Security Considerations
- All tokens/keys live in `.env`, excluded from version control (`.gitignore`).
- Logs must never print secrets, even at DEBUG level — mask tokens in any logged request/response payloads.
- Instagram/Telegram tokens should be checked for expiry where applicable; a failed-auth error from either API should trigger a distinct, clearly-labeled admin alert (different from a generic publish failure) so it's not confused with a transient issue.

---

## 9. Testing Strategy
- **Unit tests** for `core/` (pricing, composition, dedup) using fakes/mocks for repository, source, LLM, and publisher interfaces — no real network calls.
- **Contract tests** for each adapter/publisher against recorded/mocked API responses (e.g., `responses` or `vcrpy`).
- **Dry-run as integration test**: run the full cycle in dry-run mode against a small fixture set of products to validate end-to-end wiring before enabling live publishing (directly satisfies FR-9).
- **Idempotency test**: run the same cycle twice against the same fixture data; assert no duplicate publish occurs on the second run.

---

## 10. Suggested Project Structure

```
project/
├── main.py
├── config.yaml
├── prompt.txt
├── .env
├── core/
│   ├── pipeline.py          # PipelineRunner
│   ├── dedup.py
│   ├── pricing.py
│   ├── composer.py
│   └── resilience.py
├── adapters/
│   ├── base.py               # SourceAdapter Protocol, RawProduct
│   └── aggregator_adapter.py
├── llm/
│   ├── base.py                # LLMProvider Protocol, SelectionResult
│   └── openai_provider.py
├── publishers/
│   ├── base.py                # Publisher Protocol, PublishResult, ComposedPost
│   ├── telegram_publisher.py
│   ├── instagram_publisher.py
│   ├── dry_run_publisher.py
│   └── image_hosting.py       # S3ImageHost
├── storage/
│   ├── models.py               # SQLAlchemy models
│   └── repository.py           # ProductRepository + SqlAlchemyProductRepository
├── scheduler/
│   └── build_scheduler.py
├── config/
│   └── app_config.py           # pydantic AppConfig, .env + yaml loader
├── logging_setup/
│   ├── logger.py
│   └── admin_notifier.py
└── tests/
    ├── unit/
    └── integration/
```

---

## 11. Traceability Note
Every module above maps back to a specific SRS section:
- §3.1 Source Adapter → SRS FR-1
- §3.3 LLM Provider → SRS FR-2
- §3.4 Pricing Engine → SRS FR-3
- §3.5 Post Composer → SRS FR-4
- §3.6 Publishers → SRS FR-5
- §3.7 Repository → SRS FR-6
- §3.10 Logging/Notifier → SRS FR-7
- §3.8 Scheduler → SRS FR-8
- §3.6 DryRunPublisher → SRS FR-9

When implementing, keep this mapping intact so acceptance criteria (SRS §8) can be checked module-by-module.