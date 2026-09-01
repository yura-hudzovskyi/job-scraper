#!/usr/bin/env bash
# One-time setup for a fresh Oracle VM. Run this yourself over SSH — it's not part of
# the automated deploy (that just pulls images and restarts). See docs/deployment.md
# for the full walkthrough this fits into.
set -euo pipefail

REPO_URL="https://github.com/yura-hudzovskyi/job-scraper.git"
REPO_DIR="$HOME/job-scraper"

echo "==> Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "Added $USER to the docker group — log out and back in (or run 'newgrp docker') before continuing."
fi

echo "==> Cloning the repo"
if [ ! -d "$REPO_DIR" ]; then
    git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

if [ ! -f .env ]; then
    echo "==> No .env found — copying .env.example. Edit it with real values before continuing:"
    echo "    nano $REPO_DIR/.env"
    cp .env.example .env
    exit 0
fi

echo "==> Starting the stack"
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

echo "==> Running migrations"
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head

echo "==> Done. Check status with: docker compose -f docker-compose.prod.yml ps"
