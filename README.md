# OLX Poland Listing Scraper

[![CI](https://github.com/anotherk1t/olxpl_listingscraper/actions/workflows/ci.yml/badge.svg)](https://github.com/anotherk1t/olxpl_listingscraper/actions/workflows/ci.yml)

AI-powered Telegram bot that monitors OLX.pl listings in real-time with intelligent filtering, price analysis, and automated deployment.

## Why?

Buying used goods on OLX is frustrating because you rarely search for one exact product — you have a list of requirements and multiple models from various brands might fit. This bot solves that by combining web scraping with LLM-powered filtering to continuously scan listings and alert you the moment a relevant deal appears.

## Features
- **Real-time monitoring** — checks for new listings every 5 minutes
- **AI-powered filtering** — Copilot CLI (gpt-5-mini) evaluates relevance, condition, and price
- **Cheap mode** — LLM generates product model lists from natural language queries
- **Browse mode** — monitors entire subcategories for generic-titled listings
- **Custom OLX filters** — engine size, year, mileage passed as native OLX parameters
- **Search advisor** — `/advisor` probes alternatives and suggests optimizations
- **Multi-language** — English, Polish, Russian, Ukrainian auto-detection
- **Dual scraping** — JSON-LD parsing with HTML card fallback
- **Market intelligence** — tracks listing lifecycle and price history
- **Docker + CI/CD** — GitHub Actions → GHCR → automated Hetzner deploy

## Architecture
```
Telegram ↔ Python Bot (PTB 22.6) ↔ Copilot CLI (subprocess)
                ↕                        ↕
           SQLite DB              MCP Extension (olx-db-ext)
                ↕
         OLX.pl Scraper
```

- **Bot**: `python-telegram-bot` with conversation handlers and scheduled jobs
- **LLM**: Copilot CLI called via `subprocess` with `--output-format text -s`
- **MCP**: `olx-db-ext/` Node.js extension gives the LLM read-only DB access for `/slopgest`
- **Storage**: SQLite (`data/olx.db`) — searches, listings, feedback, seen IDs

## Quick Start

### Docker (recommended)
```bash
cp .env.example .env
# Edit .env with your tokens
docker compose up -d
docker logs -f olx-notifier
```

### Local development
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export TELEGRAM_BOT_TOKEN="your_token"
export COPILOT_GITHUB_TOKEN="your_github_pat"
python main.py
```

### Running tests
```bash
TELEGRAM_BOT_TOKEN=test pytest -v
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `COPILOT_GITHUB_TOKEN` | ✅ | Fine-grained PAT with "Copilot Requests" permission |
| `ADMIN_CHAT_ID` | | Your Telegram chat ID for error notifications |
| `COPILOT_MODEL` | | LLM model override (default: `gpt-5-mini`) |

## CI/CD Pipeline

**GitHub Actions** runs on every push/PR to `main`:

1. **Lint** — `ruff check` + `ruff format --check`
2. **Test** — `pytest` with coverage report
3. **Build** — multi-stage Docker image
4. **Push** — to `ghcr.io/anotherk1t/olxpl_listingscraper` (on main only)
5. **Deploy** — SSH to Hetzner VPS, `docker compose pull && up -d`

### GitHub Secrets Required
| Secret | Description |
|---|---|
| `HETZNER_SSH_KEY` | ed25519 private key for VPS access |
| `HETZNER_HOST` | VPS IP address |
| `HETZNER_USER` | SSH username |
| `TELEGRAM_BOT_TOKEN` | For deploy notifications |
| `ADMIN_CHAT_ID` | For deploy notifications |

### VPS Setup (one-time)
```bash
# On the VPS:
mkdir -p /opt/olx-scraper/data
cd /opt/olx-scraper
# Create .env with TELEGRAM_BOT_TOKEN, COPILOT_GITHUB_TOKEN, ADMIN_CHAT_ID
# Create docker-compose.yml (or clone the repo)
# Add the GitHub Actions SSH public key to ~/.ssh/authorized_keys
```