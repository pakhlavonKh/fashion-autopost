# Production VPS Deployment Guide (Docker & SSL)

This guide covers deploying the **Fashion Autopost** backend service to a Linux VPS (Ubuntu 22.04 / 24.04 or Debian 12) using **Docker**, **Docker Compose**, **Nginx reverse proxy**, and **Certbot (Let's Encrypt SSL)**.

---

## 🏗️ Architecture on VPS

```
                       Internet (Client / Webhook)
                                   │
                                   ▼
                 ┌───────────────────────────────────┐
                 │          VPS Firewall             │
                 │      (Ports 80 & 443 open)        │
                 └─────────────────┬─────────────────┘
                                   │
                                   ▼
                 ┌───────────────────────────────────┐
                 │       Nginx Reverse Proxy         │
                 │   (HTTPS, HTTP/2, Let's Encrypt)  │
                 └─────────────────┬─────────────────┘
                                   │
                    Proxy Pass: http://app:8000
                                   │
                                   ▼
        ┌────────────────────────────────────────────────────────┐
        │            fashion-autopost-app (Docker)               │
        │                                                        │
        │  ┌───────────────────────┐  ┌───────────────────────┐  │
        │  │ FastAPI Web Dashboard │  │ APScheduler Daemon    │  │
        │  │ (Port 8000, Auth)     │  │ (Cron Publishing)     │  │
        │  └───────────┬───────────┘  └───────────┬───────────┘  │
        │              └─────────────┬────────────┘              │
        │                            ▼                           │
        │           ┌─────────────────────────────────┐          │
        │           │     Pipeline Runner Orchestrator │         │
        │           └────────────────┬────────────────┘          │
        │                            │                           │
        │         ┌──────────────────┼─────────────────┐         │
        │         ▼                  ▼                 ▼         │
        │  Playwright Engine   OpenAI GPT-4    Publishers:       │
        │  (Headless Chrome)   (Prompt Curation) Telegram Channel │
        │                                       Instagram Graph   │
        └────────────────────────────┬───────────────────────────┘
                                     │
                             Mounted Host Volumes:
                             ├── ./data   --> SQLite DB (app.db)
                             ├── ./logs   --> App Logs
                             ├── config.yaml
                             └── prompt.txt
```

---

## 🖥️ 1. Minimum VPS Requirements

| Component | Minimum | Recommended | Notes |
| :--- | :--- | :--- | :--- |
| **OS** | Ubuntu 22.04 / 24.04 LTS, Debian 12 | Ubuntu 24.04 LTS | Standard 64-bit Linux |
| **CPU** | 1 vCPU | 2 vCPU | For fast Playwright browser rendering |
| **RAM** | 1 GB (+ 2GB Swap) | 2 GB (+ 2GB Swap) | **Swap is required on 1GB VPS** |
| **Disk** | 15 GB SSD | 25 GB SSD | Docker images + Playwright Chrome |
| **Ports** | 22 (SSH), 80 (HTTP), 443 (HTTPS) | | Open via VPS firewall (UFW) |

> [!IMPORTANT]
> **Swap Memory**: Headless Chromium (Playwright) uses ~300-500MB during catalog scraping. If your VPS has 1GB or 2GB of physical RAM, you **must enable swap space** to prevent Linux OOM (Out Of Memory) kills. The included `./deploy.sh` script can create a 2GB swapfile automatically.

---

## 🚀 2. Quickstart: 5-Minute VPS Deployment

### Step 1: Connect to your VPS and install Docker
SSH into your server:
```bash
ssh root@YOUR_VPS_IP
```

If Docker and Docker Compose are not yet installed:
```bash
# Update repositories
sudo apt-get update && sudo apt-get install -y curl git ufw

# Install official Docker engine
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose plugin
sudo apt-get install -y docker-compose-plugin
```

Configure firewall (UFW):
```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

---

### Step 2: Clone the Project
```bash
git clone https://github.com/YOUR_USERNAME/fashion-autopost.git /opt/fashion-autopost
cd /opt/fashion-autopost
```

---

### Step 3: Configure Production Secrets (`.env`)
Create your production `.env` from `.env.example`:
```bash
cp .env.example .env
nano .env
```

Ensure the following critical values are set:
```ini
# Aggregator & Scraper
AGGREGATOR_MODE=playwright

# OpenAI (for AI clothing curation and copywriting)
OPENAI_API_KEY=sk-proj-your-real-key-here
OPENAI_MODEL=gpt-4.1

# Telegram Channel & Bot
TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIjKlMnOpQrStUvWxYz
TELEGRAM_CHANNEL_ID=@your_fashion_channel
TELEGRAM_ADMIN_CHAT_ID=123456789

# Instagram Graph API (Optional if only Telegram is active)
INSTAGRAM_ACCESS_TOKEN=EAAG...
INSTAGRAM_ACCOUNT_ID=178414...

# Live Publishing Mode (Set false for real posts, true for test run)
DRY_RUN=false

# Web Dashboard Admin Login Credentials (Configurable username & password)
DASHBOARD_ADMIN_USERNAME=admin
DASHBOARD_ADMIN_PASSWORD=your-secure-strong-admin-passphrase-2026
DASHBOARD_ADMIN_KEY=your-secure-strong-admin-passphrase-2026

```

---

### Step 4: Run the Automated Deploy Script
Make the scripts executable and run `deploy.sh`:
```bash
chmod +x deploy.sh init-ssl.sh
./deploy.sh
```

`deploy.sh` will:
1. Verify Docker & Compose.
2. Check RAM and automatically prompt to configure a **2GB swapfile** if RAM is low.
3. Create `data/`, `logs/`, and Nginx directories.
4. Build the production Docker image (with Python 3.11 and Playwright Chromium).
5. Start the containers (`fashion-autopost-app`, `fashion-autopost-nginx`, `fashion-autopost-certbot`).

At this point, your dashboard is accessible via:
👉 **`http://YOUR_VPS_IP/`**

---

## 🔒 3. Adding a Custom Domain & Free SSL (HTTPS)

To secure your dashboard with HTTPS (e.g. `fashion.yourdomain.com`):

1. **Add DNS Record**:
   In your domain registrar / Cloudflare DNS settings, create an **A Record**:
   - **Type**: `A`
   - **Name**: `fashion` (or `@` for root domain)
   - **Target**: `YOUR_VPS_IP`
   - **Proxy**: DNS Only (if using Cloudflare, turn proxy off during initial Certbot verification or leave DNS-only)

2. **Run the SSL initialization script**:
   ```bash
   ./init-ssl.sh fashion.yourdomain.com your-email@example.com
   ```

`init-ssl.sh` automatically:
- Obtains an official **Let's Encrypt SSL certificate**.
- Configures Nginx with modern TLS 1.3, HTTP/2, and security headers.
- Enforces automatic HTTP-to-HTTPS redirect.
- Starts Certbot daemon to automatically renew certificates every 12 hours.

Now access your dashboard securely at:
👉 **`https://fashion.yourdomain.com/`**

---

## 🛠️ 4. Day-to-Day Operations & Management

### Viewing Real-Time Logs
```bash
# View backend application & scheduler logs
docker compose logs -f app

# View Nginx access & error logs
docker compose logs -f nginx
```

### Triggering an Immediate Cycle via CLI (Without waiting for schedule)
```bash
# Run a single cycle in live mode
docker compose exec app python main.py --run-once --live

# Run a single cycle in safe dry-run mode (no live publishing)
docker compose exec app python main.py --run-once --dry-run
```

### Hot-Reloading Prompt or Configuration
The project is designed with hot-reload architecture:
- To modify AI copywriting tone: edit `./prompt.txt` on the VPS. It takes effect on the next cycle without restarting!
- To modify price markup or schedule times: edit `./config.yaml` or change it live from the Web Admin Dashboard.

### Restarting or Updating Services
```bash
# Pull latest Git changes and restart
git pull origin main
docker compose build --pull app
docker compose up -d

# Restart all containers
docker compose restart

# Stop all containers
docker compose down
```

---

## 💾 5. Database & Persistent Backups

All state is stored in SQLite at `./data/app.db`:
- Ingested catalog items
- Publication history & deduplication hashes
- Telegram dynamic chat member records

### Simple Backup Command:
```bash
# Create a timestamped backup of the database
cp data/app.db "data/backup_$(date +%Y%m%d_%H%M%S).db"
```

To automate daily backups to `/opt/backups`:
```bash
mkdir -p /opt/backups
(crontab -l 2>/dev/null; echo "0 3 * * * cp /opt/fashion-autopost/data/app.db /opt/backups/app_\$(date +\%Y\%m\%d).db && find /opt/backups -type f -mtime +14 -delete") | crontab -
```

---

## 🩺 6. Troubleshooting Common Issues

### 1. Playwright Browser Crashes / OOM (Out Of Memory)
**Symptom**: `Target page, context or browser has been closed` or exit code 137.
**Solution**: Verify that swap is enabled:
```bash
swapon --show
free -h
```
If swap is `0B`, run:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 2. Telegram "Bot is not a member of the channel"
**Solution**:
1. Open your Telegram Channel settings.
2. Go to **Administrators** -> **Add Administrator**.
3. Search for your bot username (e.g. `@YourFashionBot`) and grant **Post Messages** permission.
4. Send a test message or run `docker compose exec app python main.py --run-once --dry-run`.

### 3. Dashboard says "Unauthorized / 401"
**Solution**:
Enter the exact `DASHBOARD_ADMIN_KEY` configured in your `.env` file into the dashboard login prompt.
