#!/usr/bin/env bash
set -e

# ==============================================================================
# Fashion Autopost - Production VPS One-Click Deploy Script
# ==============================================================================

echo "======================================================================"
echo "    Fashion Autopost - VPS Production Deployment Setup"
echo "======================================================================"

# 1. Check Docker & Docker Compose
if ! command -v docker &> /dev/null; then
    echo "[!] Docker is not installed on this system."
    echo "    Please install Docker using the official script:"
    echo "    curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "[!] Docker Compose (v2) is not installed."
    echo "    Please install Docker Compose plugin: sudo apt-get update && sudo apt-get install -y docker-compose-plugin"
    exit 1
fi

# 2. Swap Memory Check (Playwright requires adequate RAM during page rendering)
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_RAM_MB=$((TOTAL_RAM_KB / 1024))
TOTAL_SWAP_KB=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
TOTAL_SWAP_MB=$((TOTAL_SWAP_KB / 1024))

echo "[*] System RAM: ${TOTAL_RAM_MB}MB | Swap: ${TOTAL_SWAP_MB}MB"
if [ "$TOTAL_RAM_MB" -lt 2000 ] && [ "$TOTAL_SWAP_MB" -lt 1000 ]; then
    echo "[!] VPS RAM is under 2GB and swap is insufficient."
    echo "    Recommended: allocate a 2GB swapfile to prevent browser OOM (Out Of Memory)."
    read -p "    Create 2GB swap now? (y/N): " CREATE_SWAP
    if [[ "$CREATE_SWAP" =~ ^[Yy]$ ]]; then
        echo "[*] Creating 2GB swapfile..."
        sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        if ! grep -q '/swapfile' /etc/fstab; then
            echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
        fi
        echo "[+] Swap enabled successfully."
    fi
fi

# 3. Directories & Permissions
echo "[*] Initializing local storage and directories..."
mkdir -p data logs certbot/conf certbot/www nginx/conf.d

# 4. Environment (.env) Check
if [ ! -f .env ]; then
    echo "[*] No .env file found. Creating from .env.example..."
    cp .env.example .env
    echo ""
    echo "======================================================================"
    echo " [!] ATTENTION: A new .env file has been created."
    echo " Please open .env and provide your API keys (OpenAI, Telegram, Instagram)."
    echo " Example: nano .env"
    echo "======================================================================"
    read -p "Press [Enter] once you have verified or updated your .env file..."
fi

# 5. Build and Launch Containers
echo "[*] Building and starting Docker containers..."
docker compose build --pull
docker compose up -d

# 6. Status verification
echo ""
echo "[*] Waiting for services to become healthy..."
sleep 5

docker compose ps

echo ""
echo "======================================================================"
echo " [✓] DEPLOYMENT COMPLETE!"
echo ""
echo " 🌐 Dashboard URL: http://<YOUR_VPS_IP>/"
echo " 📊 View Logs:     docker compose logs -f app"
echo " 🔒 Setup SSL:     ./init-ssl.sh <your_domain> <your_email>"
echo " 🛑 Stop System:   docker compose down"
echo " 🔄 Restart:       docker compose restart"
echo "======================================================================"
