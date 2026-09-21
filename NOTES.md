# NOTES.md — Architecture Decisions & Resolution of Open Questions (SRS §10)

This document explicitly details how the 5 open questions defined in [SRS.md §10](file:///d:/projects/fashion-autopost/documentation/srs.md#L239) are addressed in this codebase. Rather than hardcoding arbitrary assumptions, each question has been designed as a swappable, config-driven abstraction.

---

## 1. Exact Name & Documentation of Third-Party Aggregator API (SRS §10.1)

### Status & Resolution
- **Current State**: The real aggregator API endpoint and vendor contract remain unconfirmed.
- **Architectural Solution**:
  - The system isolates low-level wire transport inside `adapters.aggregator_client.AggregatorClient` (Protocol).
  - Downstream pipeline and deduplication logic interact exclusively with `adapters.base.SourceAdapter` and the normalized `RawProduct` DTO.
  - A production-ready `MockAggregatorClient` simulates realistic Zara and Mango fashion catalog data for local development and dry-run execution.
  - An `HttpAggregatorClient` skeleton is implemented with prominent `TODO` comments.
- **Config Key**: `aggregator.mode: "mock"` (default) or `"http"`, `aggregator.base_url`, `aggregator.stores`.
- **Items to Confirm With Provider Before Live Deployment**:
  1. *Endpoint Delivery Model*: REST polling (`GET /products`), Webhook push, or GraphQL.
  2. *Authentication Protocol*: API Key header (`X-API-Key` / `Authorization: Bearer <key>`) vs OAuth2 client credentials.
  3. *Response Schema Mappings*: Exact JSON keys for product ID (`id` vs `sku`), price format (decimal vs integer cents), availability flags, and catalog refresh frequency.
  4. *Photo Delivery Format*: Are images served as direct, permanent, public HTTPS URLs? (Instagram Graph API strictly requires public HTTPS image URLs; if raw files or private/authenticated URLs are returned, the integrated S3 re-hosting mechanism must be used).
  5. *Rate Limits & Bursts*: Maximum allowed queries per minute/day and overage costs.
  6. *Brand Query Parameters*: Parameter format for querying multiple stores (e.g. `?stores=zara,mango`).

---

## 2. Manual Moderation vs. Fully Automatic Publishing (SRS §10.2)

### Status & Resolution
- **Current State**: Business needs may require human approval initially before transitioning to full autonomy.
- **Architectural Solution**:
  - Implemented the `core.moderation.ModerationGate` protocol and `ConfigurableModerationGate`.
  - Positioned between the `LLM selection/pricing` stage and the `publishing` stage.
  - If enabled with `auto_approve: false`, candidate posts are stored in the database with status `pending_review`, preventing unauthorized automatic posting.
- **Config Keys**:
  ```yaml
  moderation:
    enabled: false      # Set to true to activate the moderation gate
    auto_approve: true  # If false, holds items in status 'pending_review'
  ```
- **Operational Assumption**: By default, `enabled: false, auto_approve: true` allows hands-off autonomy from day one, while enabling instant toggle to human review when requested.

---

## 3. Currency Conversion Logic (SRS §10.3)

### Status & Resolution
- **Current State**: Store currencies (e.g. EUR from Zara Europe, TRY from Zara Turkey) may differ from customer sale currency (e.g. USD).
- **Architectural Solution**:
  - Defined the `core.pricing.FxConverter` protocol.
  - Implemented two swappable converters:
    1. `FixedRateConverter`: converts using configured fixed multipliers or cross-rate table from `config.yaml`.
    2. `DynamicRateConverter`: supports live updates or cached currency rate feeds with automatic fallback to fixed rates.
  - The pricing engine executes:
    $$\text{price\_final} = \text{convert}(\text{price\_original}, \text{currency} \to \text{target\_currency}) + \text{markup}$$
    rounded to two decimal places (`ROUND_HALF_UP`).
- **Config Keys**:
  ```yaml
  target_currency: "USD"
  fx:
    mode: "fixed"        # "fixed" or "dynamic"
    fixed_rate: 1.08
    rates:
      EUR: 1.08
      USD: 1.00
      TRY: 0.029
      GBP: 1.29
  ```

---

## 4. Daily Publication Cap Beyond Schedule (SRS §10.4)

### Status & Resolution
- **Current State**: Unclear if a hard ceiling on daily published posts is required beyond `max_products_per_run` and the number of scheduled times.
- **Architectural Solution**:
  - Implemented `daily_publish_cap: int | None` in `AppConfig`.
  - At the beginning of each cycle, `ProductRepository.get_published_count_today(timezone)` queries how many items have already been published today in the configured timezone.
  - If `published_today >= daily_publish_cap`, the cycle gracefully logs and terminates early (`skipped_daily_cap = True`).
  - If items remain under the cap, the runner limits selection to `min(max_products_per_run, remaining_cap)`.
- **Config Key**: `daily_publish_cap: null` (no extra limit) or e.g. `daily_publish_cap: 10`.

---

## 5. Critical Error Alerting Destination (SRS §10.5)

### Status & Resolution
- **Current State**: Destination for critical operational alerts (e.g., publish failures after retries, database connection loss).
- **Architectural Solution**:
  - Defined `logging_setup.admin_notifier.AdminNotifier` protocol.
  - Implemented `TelegramAdminNotifier` (sends HTML-formatted error alerts to an admin chat ID via Telegram Bot API) and `ConsoleAdminNotifier`.
  - Implemented `CompositeAdminNotifier` to multiplex alerts to multiple destinations simultaneously.
- **Config Keys**:
  ```yaml
  alert:
    channel: "console"    # "telegram", "console", or "both"
    telegram_chat_id: null # Falls back to TELEGRAM_ADMIN_CHAT_ID in .env
  ```
