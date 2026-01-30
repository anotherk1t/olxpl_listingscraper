<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Telegram-Bot_API-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram">
  <img src="https://img.shields.io/badge/Google-Gemini_AI-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Gemini AI">
  <img src="https://img.shields.io/badge/Docker-Containerized-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/License-GPL--3.0-brightgreen?style=for-the-badge" alt="License">
</p>

<h1 align="center">🛒 OLX Poland Listing Scraper</h1>

<p align="center">
  <b>An intelligent, AI-powered Telegram bot that monitors OLX.pl listings in real-time</b><br>
  <i>Never miss a deal again — get instant notifications with AI-driven price analysis</i>
</p>

---

## 🎯 Overview

**OLX Poland Listing Scraper** is a production-ready Telegram bot designed to automate the monitoring of OLX.pl marketplace listings. It combines robust web scraping techniques with cutting-edge AI to deliver real-time notifications enriched with intelligent price assessments.

Whether you're hunting for vintage cameras, electronics, or any other items, this bot does the heavy lifting — continuously scanning listings and alerting you the moment something new appears.

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🔍 **Real-Time Monitoring** | Automatically checks for new listings every 5 minutes |
| 🤖 **AI-Powered Analysis** | Leverages Google Gemini to evaluate if prices are HIGH, FAIR, or LOW |
| 📱 **Telegram Integration** | Seamless notifications with inline keyboard interactions |
| 🔄 **Dual Scraping Strategy** | Primary JSON-LD parsing with HTML fallback for resilience |
| 📊 **Market Intelligence** | Tracks listing lifecycle and builds historical price data |
| 🐳 **Docker-Ready** | One-command deployment with Docker Compose |
| 💾 **Persistent Storage** | JSON-based data persistence with volume mounting |
| 📝 **Admin Logging** | Real-time error notifications sent to Telegram |

---

## 🏗️ Architecture & Technologies

### Core Stack

```
┌─────────────────────────────────────────────────────────────┐
│                    OLX Listing Scraper                      │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Telegram   │  │   Web       │  │   Google Gemini     │  │
│  │  Bot API    │◄─┤  Scraping   │──►  AI Analysis        │  │
│  │  (v13.15)   │  │  Engine     │  │   (2.5 Flash Lite)  │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│         │               │                    │              │
│         ▼               ▼                    ▼              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              JSON Data Persistence Layer                ││
│  │  • user_searches.json  • seen_listings.json             ││
│  │  • market_data.json                                     ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### Technology Breakdown

| Technology | Purpose | Implementation Details |
|------------|---------|------------------------|
| **Python 3.9+** | Core runtime | Async-compatible, type-hinted codebase |
| **python-telegram-bot** | Bot framework | Conversation handlers, callbacks, job queues |
| **BeautifulSoup4 + lxml** | HTML parsing | High-performance DOM traversal |
| **Requests** | HTTP client | Session handling with custom headers |
| **Google Generative AI** | Price intelligence | Gemini 2.5 Flash Lite model integration |
| **Docker** | Containerization | Slim Python image, multi-stage ready |
| **Docker Compose** | Orchestration | Environment management, volume persistence |

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose (recommended)
- Python 3.9+ (for local development)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Google Gemini API Key (from [Google AI Studio](https://aistudio.google.com/app/apikey))

### 🐳 Docker Deployment (Recommended)

1. **Clone the repository**
   ```bash
   git clone https://github.com/anotherk1t/olxpl_listingscraper.git
   cd olxpl_listingscraper
   ```

2. **Create environment file**
   ```bash
   cat > .env << EOF
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   GEMINI_API_KEY=your_gemini_api_key
   ADMIN_CHAT_ID=your_telegram_chat_id
   EOF
   ```

3. **Launch with Docker Compose**
   ```bash
   docker-compose up -d --build
   ```

4. **Verify deployment**
   ```bash
   docker logs -f olx-notifier
   ```

### 🐍 Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export TELEGRAM_BOT_TOKEN="your_token"
export GEMINI_API_KEY="your_key"
export ADMIN_CHAT_ID="your_chat_id"

# Run the bot
python main.py
```

---

## 📖 Usage Guide

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Initialize the bot and see welcome message |
| `/add` | Add a new OLX search URL to monitor |
| `/list` | View all your active search queries |
| `/delete` | Remove a search from monitoring |
| `/help` | Display available commands |

### Workflow

```
1. Search for items on OLX.pl
2. Copy the search URL
3. Send /add to the bot
4. Provide a name for your search
5. Paste the OLX URL
6. Receive notifications for new listings! 🎉
```

### Sample Notification

```
✨ New Listing Found for 'vintage camera' ✨

Olympus OM-1 with 50mm f/1.8 lens

💰 Price: 850 zł
🔗 View Listing

🟢 AI Price Analysis (high confidence):
This is a great deal! Similar Olympus OM-1 cameras with kit 
lenses typically sell for 1000-1200 PLN. The included 
50mm f/1.8 adds significant value.
```

---

## 🔧 Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from BotFather |
| `GEMINI_API_KEY` | ❌ | Gemini API key (enables AI analysis) |
| `ADMIN_CHAT_ID` | ❌ | Chat ID for admin log notifications |

### Timing Configuration

Adjust intervals in `main.py`:

```python
CHECK_INTERVAL = 300    # Check for new listings (5 min)
MONITOR_INTERVAL = 1200 # Monitor existing listings (20 min)
```

---

## 🔬 Technical Deep Dive

### Scraping Strategy

The scraper employs a **dual-method approach** for maximum reliability:

1. **Primary: JSON-LD Extraction**
   - Parses structured data from `<script type="application/ld+json">`
   - Most reliable when available, provides clean structured data

2. **Fallback: HTML DOM Parsing**
   - Traverses `data-cy="l-card"` elements
   - Extracts title, price, and URL from HTML structure
   - Ensures functionality even when JSON-LD is unavailable

### AI Price Analysis

The Gemini integration provides:

- **Market Comparison** — Compares against historical data
- **Price Assessment** — HIGH / FAIR / LOW classification
- **Confidence Scoring** — Indicates analysis reliability
- **Contextual Insights** — Brand, condition, and anomaly detection

### Data Persistence

```
data/
├── user_searches.json    # User search configurations
├── seen_listings.json    # Listing deduplication cache
└── market_data.json      # Historical pricing data
```

---

## 📂 Project Structure

```
olxpl_listingscraper/
├── main.py              # Core application (684 lines)
├── Dockerfile           # Container definition
├── docker-compose.yml   # Orchestration config
├── requirements.txt     # Python dependencies
├── LICENSE              # GPL-3.0 license
└── README.md            # Documentation
```

---

## 🛡️ Security Considerations

- ✅ Token-based authentication via environment variables
- ✅ No hardcoded credentials
- ✅ User-scoped data isolation
- ✅ Rate limiting compliance (5-second delays)
- ✅ Respectful scraping with proper User-Agent headers

---

## 🗺️ Roadmap

- [ ] Multi-language support
- [ ] Price drop notifications
- [ ] Export to CSV/JSON
- [ ] Web dashboard interface
- [ ] Redis-based caching
- [ ] Prometheus metrics endpoint

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0** — see the [LICENSE](LICENSE) file for details.

---

## 👨‍💻 Author

**anotherk1t**

<p>
  <a href="https://github.com/anotherk1t">
    <img src="https://img.shields.io/badge/GitHub-anotherk1t-181717?style=flat-square&logo=github" alt="GitHub">
  </a>
</p>

---

<p align="center">
  <b>⭐ If this project helped you, consider giving it a star! ⭐</b>
</p>