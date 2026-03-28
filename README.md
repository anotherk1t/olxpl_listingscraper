# OLX Poland Listing Scraper

OLX Poland Listing Scraper is an intelligent, AI-powered Telegram bot that monitors OLX.pl listings in real-time. It provides instant notifications with AI-driven price analysis to help you never miss a deal again.

## Why?

Buying used goods is a great way to save money on transportation, devices, and everyday items. However, searching on platforms like OLX can be frustrating because you rarely search for one specific, exact product. Instead, you usually have a list of requirements, and multiple products from various brands might be suitable for you.

This project aims to solve this problem! It features an automatic link builder to help you avoid unwanted listings and easily find the best offers matching your broad requirements. By combining robust web scraping techniques with cutting-edge AI, it delivers real-time notifications enriched with intelligent price assessments.

Whether you're hunting for a vintage camera, a decent gaming laptop, or any other items, this bot does the heavy lifting — continuously scanning listings and alerting you the moment a relevant deal appears.

## Features
- Real-time monitoring automatically checking for new listings every 5 minutes.
- AI-powered analysis using Google Gemini to evaluate if prices are HIGH, FAIR, or LOW.
- Multi-language support automatically detecting English, Polish, Russian, and Ukrainian.
- Telegram integration for seamless notifications and inline keyboard interactions.
- Dual scraping strategy with JSON-LD parsing and HTML fallback for resilience.
- Market intelligence tracking listing lifecycle and building historical price data.
- Docker-ready one-command deployment.
- Persistent SQLite-based storage for user preferences and listings.

## Roadmap & Planned Features
- **Price drop notifications**: Alerting users when watched items drop in price.
- **Export capabilities**: Exporting data to CSV/JSON formats.
- **Web interface**: Building a web dashboard for easier management.
- **Redis caching**: Implementing Redis for improved performance.
- **Metrics endpoint**: Adding Prometheus metrics for monitoring.

## Integration & Architecture
This application relies on a modular architecture combining a Python Telegram bot framework with web scraping and AI capabilities:
- **Core Bot**: Built using python-telegram-bot for handling conversations, callbacks, and scheduled jobs.
- **Scraping Engine**: Utilizes BeautifulSoup4 and Requests for a dual-strategy scraping approach (JSON-LD and HTML).
- **Gemini Proxy**: Includes a Node.js proxy server alongside a Python MCP extension for seamless Gemini AI integration.
- **Storage**: Uses SQLite with a robust `db.py` module to persist users, searches, and listing data.

## Prerequisites
- Docker & Docker Compose (recommended)
- Python 3.9+ (for local development)
- Telegram Bot Token (from @BotFather)
- Google Gemini API Key (from Google AI Studio)

## Installation & Setup

### 1. Running with Docker (Recommended)
1. Clone the repository and create an environment file:
   ```bash
   cat > .env << EOF
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   LLM_PROXY_URL=http://host.docker.internal:3000/ask
   ADMIN_CHAT_ID=your_telegram_chat_id
   EOF
   ```
2. Launch using Docker Compose:
   ```bash
   docker-compose up -d --build
   ```
3. To monitor the deployment, you can check the logs:
   ```bash
   docker logs -f olx-notifier
   ```

### 2. Running locally (without Docker)
1. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set environment variables and run the bot:
   ```bash
   export TELEGRAM_BOT_TOKEN="your_token"
   export LLM_PROXY_URL="http://localhost:3000/ask"
   export ADMIN_CHAT_ID="your_chat_id"
   python main.py
   ```