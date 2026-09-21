# Software Requirements Specification (SRS)
## Automated Clothing Product Publishing System for Telegram & Instagram (GPT-powered)

Version 1.0 — Draft for implementation by an AI coding agent

---

## 1. Introduction

### 1.1 Purpose
This document specifies the functional and non-functional requirements for a backend system that automatically publishes clothing-product posts (photo + AI-generated description + calculated price) to Telegram and Instagram, on a schedule, without daily human intervention.

### 1.2 Scope
The system:
- Pulls product data (clothing items — initially Zara and Mango, extensible to more) from a **third-party aggregator API** (not a self-built scraper).
- Deduplicates against previously published items.
- Uses an LLM (GPT via OpenAI API) to (a) select the best products according to an editable prompt, and (b) generate a short marketing description for each.
- Computes a final sale price (`store_price + markup`).
- Composes a post (photo + description + price [+ optional link]).
- Publishes to a Telegram channel/group via Bot API and to Instagram via the Graph API.
- Prevents re-publishing the same item.
- Logs all activity and errors, with optional admin alerting.
- Supports a dry-run mode that executes the full pipeline without publishing.

After launch, the only things the business owner should need to touch are:
1. The GPT prompt text file.
2. The markup value.
3. The publishing schedule.

Everything else runs autonomously.

### 1.3 Definitions
| Term | Meaning |
|---|---|
| Aggregator API | Third-party API that supplies product data from Zara/Mango/etc. |
| External ID | Unique product identifier issued by the aggregator |
| Post | The final publishable unit: photo + description + price |
| Dry-run | Full pipeline execution with publishing step mocked/skipped |
| Publisher | A module responsible for pushing a post to one channel (Telegram, Instagram, …) |
| Adapter | A module responsible for pulling normalized product data from one source |

### 1.4 Intended Use of This Document
This SRS is intended to be handed to an AI coding agent (or a human developer) as the authoritative statement of *what* must be built. It intentionally avoids prescribing implementation details — see the companion **SDD.md** for architecture and design.

---

## 2. Overall Description

### 2.1 Product Perspective
A standalone, self-hosted Python backend service. It integrates with four external systems:
1. Third-party product aggregator API (data source)
2. OpenAI API (selection + description generation)
3. Telegram Bot API (publishing)
4. Instagram Graph API (publishing)

Optionally, a fifth integration: an S3-compatible object store, needed only if the aggregator does not provide direct public HTTPS photo URLs (Instagram Graph API requires public HTTPS image URLs, not file uploads).

### 2.2 Product Functions (Summary)
1. Scheduled retrieval of product data.
2. Deduplication by `external_id`.
3. GPT-based product selection against a configurable prompt.
4. GPT-based description generation.
5. Price calculation with configurable markup and optional currency conversion.
6. Post composition (platform-specific formatting where required).
7. Publishing to Telegram and Instagram.
8. Persistent tracking of publication status.
9. Structured logging and error alerting.
10. Dry-run testing mode.
11. Fully external configuration (no code changes for routine operation).

### 2.3 User Characteristics
- **Operator/owner**: non-technical or semi-technical; interacts only via config files (prompt text, markup number, schedule) — no code editing expected.
- **Developer/maintainer**: occasionally extends the system (new source, new LLM provider, new social network).

### 2.4 Constraints
- Instagram Graph API requires a Business/Creator account linked to a Facebook Page, and Meta App Review for the required permissions — this is an external process with its own timeline, not controlled by the development team.
- Instagram Graph API only accepts publicly reachable HTTPS image URLs — never raw files. If the aggregator does not supply such URLs, an intermediate public hosting step (e.g., S3) is required.
- All secrets/config (API keys, tokens, markup, schedule) must live outside source code (`.env` / `config.yaml` / `config.json`).
- No web scraping or Playwright-based data collection is required or in scope — data comes exclusively via the aggregator API.

### 2.5 Assumptions & Dependencies
- The third-party aggregator API's exact contract (endpoint type, response schema, rate limits, auth method, photo delivery format, supported stores, extensibility) must be confirmed with the provider **before** development starts (see §10, Open Questions). The system must nonetheless be built against an abstracted adapter interface so this can be confirmed/changed later without a rewrite.
- Terms of Service of the aggregator API regarding commercial use of product photos/data is the customer's legal responsibility, not the system's concern — but the system should not make this harder to audit (e.g., should log source/provenance of each item).

---

## 3. Functional Requirements

Each requirement is uniquely numbered for traceability.

### 3.1 Product Ingestion (FR-1.x)
- **FR-1.1**: The system SHALL retrieve product listings from the configured aggregator API on a schedule (see FR-8).
- **FR-1.2**: For each product, the system SHALL extract: photo (URL or file), title, price, currency, product URL, unique external ID, and availability/in-stock status.
- **FR-1.3**: The data-source integration SHALL be implemented behind an adapter interface so the aggregator can be swapped or extended (additional stores) without modifying downstream pipeline logic.
- **FR-1.4**: Products already marked `published` in the database (matched by `external_id`) SHALL be discarded at ingestion time before further processing.

### 3.2 GPT-Based Selection (FR-2.x)
- **FR-2.1**: The system SHALL pass candidate products (photo, title, price) to an LLM for selection.
- **FR-2.2**: The selection prompt SHALL be stored outside the codebase in a plain text file (e.g., `prompt.txt`).
- **FR-2.3**: The prompt file SHALL be re-read from disk at the start of every publishing cycle — no service restart required to apply prompt edits.
- **FR-2.4**: The LLM response SHALL be in a strictly structured, machine-parseable format (JSON) containing the selected products and a short description for each.
- **FR-2.5**: The maximum number of products selected per run SHALL be a configurable parameter.
- **FR-2.6**: If the LLM response fails to parse as valid JSON, the system SHALL treat this as a recoverable error (log it, optionally retry, skip the cycle) rather than crash.

### 3.3 Pricing (FR-3.x)
- **FR-3.1**: Final sale price SHALL be computed as `price_final = price_original + markup`.
- **FR-3.2**: The markup value SHALL be stored in configuration and changeable without a code change.
- **FR-3.3**: If a product's original currency differs from the target sale currency, the system SHALL convert it using either a fixed configured rate or a dynamic rate source — the exact mode SHALL be configurable (open question, see §10.3).

### 3.4 Post Composition (FR-4.x)
- **FR-4.1**: A composed post SHALL include: product photo, GPT-generated description, final price, and optionally the product URL.
- **FR-4.2**: Content SHALL be logically identical across Telegram and Instagram, with platform-specific formatting differences (e.g., text length limits, link support) handled at the composition/publishing boundary, not by duplicating business logic.

### 3.5 Publishing (FR-5.x)
- **FR-5.1**: The system SHALL publish composed posts to a configured Telegram channel/group via the Telegram Bot API, including photo, description, and price.
- **FR-5.2**: The system SHALL publish composed posts to Instagram via the Graph API (Business/Creator account), including photo, description, and price.
- **FR-5.3**: Instagram publishing SHALL only use publicly accessible HTTPS image URLs. If the source photo is not already such a URL, the system SHALL upload/host it (e.g., via S3-compatible storage) before publishing.
- **FR-5.4**: Publishing SHALL be implemented behind a publisher interface so additional social networks can be added without modifying the core pipeline.

### 3.6 Anti-Duplicate Protection (FR-6.x)
- **FR-6.1**: Upon successful publication, the product's `external_id` SHALL be persisted with status `published`.
- **FR-6.2**: Before publishing, the system SHALL verify the product has not already been published (see FR-1.4 for the ingestion-time check; this is the pre-publish confirmation check).

### 3.7 Logging & Error Handling (FR-7.x)
- **FR-7.1**: All errors (aggregator API failures, LLM failures, publishing failures) SHALL be logged with timestamp, error source, error message, and affected product (if applicable).
- **FR-7.2**: Logs SHALL be persisted (file and/or database table).
- **FR-7.3**: Critical errors (e.g., publication failed after exhausting retries) SHOULD trigger an admin notification (e.g., a Telegram message to an admin chat).
- **FR-7.4**: All external API calls (aggregator, OpenAI, Telegram, Instagram) SHALL use retry with exponential backoff on transient failures.

### 3.8 Scheduling (FR-8.x)
- **FR-8.1**: Publication time(s) and number of posts per day SHALL be defined in configuration.
- **FR-8.2**: Schedule changes SHALL take effect without a code deployment/restart (the scheduler re-reads config).
- **FR-8.3**: The scheduling mechanism SHALL be cron-like (e.g., APScheduler) driven by config parameters.

### 3.9 Dry-Run Mode (FR-9.x)
- **FR-9.1**: A configuration flag SHALL allow running the full pipeline (ingestion → GPT selection/description → pricing → post composition) while skipping the actual publish step, for pre-production verification.
- **FR-9.2**: Dry-run output SHALL be inspectable (e.g., logged or written to a file/DB) so a human can review it before enabling live publishing.

---

## 4. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Configurability | All operational parameters (API keys, tokens, markup, schedule, prompt path, per-run product limit, currency logic, dry-run flag) MUST live in config files/env vars, never hardcoded. |
| NFR-2 | Extensibility | Adding a new product source, a new LLM provider, or a new publishing target MUST be possible via a well-defined interface, without rewriting the pipeline core. |
| NFR-3 | Reliability | Transient failures on any external API call must not crash the whole cycle; use retry with exponential backoff. |
| NFR-4 | Observability | Every pipeline run must be traceable end-to-end via logs (what was fetched, selected, priced, and published/failed, and why). |
| NFR-5 | Idempotency | Re-running the pipeline must never result in a duplicate publish of the same `external_id`. |
| NFR-6 | Security | Secrets (API keys/tokens) must not be committed to source control or logged in plaintext. |
| NFR-7 | Portability of storage | Database layer should default to SQLite for simplicity but be swappable to PostgreSQL for scaling without major rework. |
| NFR-8 | Maintainability | Business logic (selection/pricing/composition) must be decoupled from I/O concerns (network calls, DB, scheduler) to keep the system testable. |

---

## 5. External Interface Requirements

### 5.1 Aggregator API (data source) — TO BE CONFIRMED WITH PROVIDER BEFORE BUILD
Must document: endpoint type (REST/webhook/polling), response schema (photo, title, price, currency, product URL, ID, availability), catalog refresh frequency, rate limits/cost beyond limits, auth method, photo delivery format (public URL vs file), supported stores, and extensibility to new stores.

### 5.2 OpenAI API
Used for (a) product selection against the configurable prompt, (b) description generation. Model identifier must be configurable, not hardcoded. Must support returning strictly structured JSON.

### 5.3 Telegram Bot API
Publishes photo + text post to a configured channel/group ID using a Bot Token.

### 5.4 Instagram Graph API
Requires a Business/Creator account linked to a Facebook Page and completed Meta App Review for the necessary permissions. Accepts only public HTTPS image URLs.

### 5.5 (Optional) S3-Compatible Storage
Required only if the aggregator's photos are not already public HTTPS URLs; used to re-host images for Instagram compliance.

---

## 6. Data Requirements

### 6.1 `products` Table (minimum viable schema)

| Field | Type | Description |
|---|---|---|
| id | PK | Internal ID |
| external_id | string, unique | Aggregator's product ID |
| source | string | Zara / Mango / etc. |
| title | string | Product title |
| price_original | decimal | Store price |
| currency_original | string | Store currency |
| price_final | decimal | Sale price (with markup) |
| photo_url | string | Photo URL |
| description_gpt | text | GPT-generated description |
| product_url | string | Link to product |
| status | enum | new / selected / published / failed |
| telegram_post_id | string, nullable | Telegram post ID |
| instagram_post_id | string, nullable | Instagram post ID |
| published_at | timestamp, nullable | Publish timestamp |
| created_at | timestamp | Record creation timestamp |

### 6.2 Logs
Structured log entries (file and/or DB table) capturing timestamp, stage, severity, message, and related `external_id` where applicable.

---

## 7. Configuration Requirements

All of the following MUST be externally configurable (no code edits):
- Aggregator API key/credentials
- OpenAI API key + model name
- Telegram Bot Token + channel/group ID
- Instagram Graph API token + account ID
- Path to the prompt file (`prompt.txt`)
- Markup amount
- Publishing schedule (times, posts/day)
- Max products per run
- Currency conversion rate/logic (if applicable)
- Dry-run flag (on/off)

---

## 8. Acceptance Criteria

The system is considered complete when:
- [ ] Products are automatically retrieved from the aggregator API on schedule.
- [ ] The GPT prompt is editable in a separate file and takes effect without a service restart.
- [ ] Markup and schedule are changeable via config without code changes.
- [ ] A product is never published twice.
- [ ] A post is successfully published to both Telegram and Instagram automatically.
- [ ] Errors are captured in logs.
- [ ] Dry-run mode allows verifying a full cycle without a real publish.
- [ ] Adding a new source / social network / LLM provider does not require rewriting the whole system.

---

## 9. Out of Scope
- Web scraping / Playwright-based data collection.
- A human moderation UI (unless the answer to open question §10.2 requires one — currently undecided).
- Anything beyond clothing-product posts (e.g., other product categories) unless explicitly requested later.

---

## 10. Open Questions (must be resolved before/during development)
1. Exact name/documentation of the third-party aggregator API.
2. Is manual moderation of a post required before publishing (at least initially), or is publishing fully automatic from day one?
3. Currency conversion logic when store currency and sale currency differ (fixed config rate vs. dynamic rate).
4. Is there a daily publish cap beyond the schedule itself?
5. Where exactly should critical error alerts be sent (admin Telegram bot, email, etc.)?