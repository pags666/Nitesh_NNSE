# 🚀 NNSE — Multi-AI Stock Signal Engine

[![NNSE Market Pipeline](https://github.com/pags666/Nitesh_NNSE/actions/workflows/update.yaml/badge.svg)](https://github.com/pags666/Nitesh_NNSE/actions/workflows/update.yaml)

> **Automated Indian stock market intelligence pipeline** — scrapes NSE, BSE, Economic Times & MoneyControl every 20 minutes via GitHub Actions, runs **6 AI models** for consensus-based BUY/SELL signals, and publishes a premium dark-themed dashboard to Google Sheets.

---

## 📊 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                 GitHub Actions (every 20 min)               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│   │  NNSE.py │  │  BSE.py  │  │  et.py   │  │ monc.py  │  │
│   │   (NSE)  │  │  (BSE)   │  │  (ET)    │  │  (MC)    │  │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│        │              │             │              │        │
│        └──────────────┴──────┬──────┴──────────────┘        │
│                              ▼                              │
│                    Google Sheets (Raw Data)                  │
│                     nse | bse | et | monc                   │
│                              │                              │
│               ┌──────────────┼──────────────┐               │
│               ▼              ▼              ▼               │
│        ┌────────────┐ ┌────────────┐ ┌────────────┐        │
│        │multi_ai.py │ │consol*.py  │ │nifty_move  │        │
│        │ (6 Models) │ │(1 Model)   │ │  (Bias)    │        │
│        └─────┬──────┘ └─────┬──────┘ └─────┬──────┘        │
│              │              │              │                │
│              └──────────────┴──────────────┘                │
│                             ▼                               │
│                  Google Sheets (Dashboard)                   │
│               multi_ai | consolidated | bias                │
└─────────────────────────────────────────────────────────────┘
```

---

## 🧠 The 6-Model Consensus Engine (`multi_ai.py`)

The core of the system — runs **every corporate announcement** through 6 independent AI models and generates a **weighted consensus** signal.

| #  | Model                    | Provider      | Weight (6M) | Weight (5M) | Role                        |
|----|--------------------------|---------------|-------------|-------------|-----------------------------|
| 1  | Llama 3.3 70B Versatile  | Groq          | 25%         | 30%         | **Primary** — best reasoning|
| 2  | Llama 3.1 8B Instant     | Groq          | 10%         | 15%         | Fast cross-check            |
| 3  | Llama 4 Scout 17B        | Groq          | 15%         | 20%         | Newest Llama architecture   |
| 4  | Qwen3 32B                | Groq          | 15%         | 20%         | Strong reasoning            |
| 5  | FinBERT                  | HuggingFace   | 15%         | 15%         | Financial NLP sentiment     |
| 6  | Gemini 2.0 Flash Lite    | Google        | 20%         | —           | Optional bonus model        |

### Signal Logic
- **STRONG BUY/SELL** → Consensus score ≥ 75 + minimum 2 models agree
- **BUY/SELL** → Consensus score ≥ 50 + minimum 2 models agree  
- **NO TRADE** → Score below threshold or conflict between models
- Auto-switches to **5-model mode** if Gemini hits daily quota

### Noise Filter
Automatically skips routine filings (AGMs, compliance certificates, newspaper ads, etc.) to focus only on **price-moving events**:
- Order wins / large contracts
- Strong earnings surprises
- Buybacks / bonus / stock splits
- Acquisitions / mergers
- SEBI actions / fraud / defaults
- Major capacity expansions

---

## 📁 Project Structure

```
nnse/
├── .github/
│   └── workflows/
│       └── update.yaml        # GitHub Actions — runs every 20 min
├── NNSE.py                    # NSE corporate announcements scraper
├── BSE.py                     # BSE corporate announcements scraper
├── et.py                      # Economic Times market news scraper
├── monc.py                    # MoneyControl stock news scraper
├── multi_ai.py                # ⭐ 6-Model Consensus Signal Engine
├── consolidated.py            # Single-model AI analysis (Groq + FinBERT)
├── nifty_move.py              # Nifty 50 direction bias (Bullish/Bearish)
├── ai.py                      # Legacy AI analysis script
├── words.py                   # Keyword-based stock screening
├── google_sheets.py           # Shared Google Sheets helper module
├── credentials.json           # 🔒 Google Service Account (gitignored)
├── requirements.txt           # Python dependencies
└── .gitignore                 # Excludes credentials & cache files
```

---

## ⚡ Data Sources

| Source | Script | What it scrapes | API |
|--------|--------|-----------------|-----|
| **NSE India** | `NNSE.py` | Corporate announcements (equities) | `nseindia.com/api/corporate-announcements` |
| **BSE India** | `BSE.py` | Corporate announcements | `api.bseindia.com/BseIndiaAPI` |
| **Economic Times** | `et.py` | Market stock news headlines | Web scraping (BeautifulSoup) |
| **MoneyControl** | `monc.py` | Stock news headlines (3 pages) | Web scraping (BeautifulSoup) |

---

## 🖥️ Google Sheets Dashboard

The `multi_ai.py` engine outputs a **premium dark-themed dashboard** with:

- 🟢 **Live Stats Panel** — BUY/SELL counts, sentiment indicator, top pick, average score
- 📜 **Signal History** — Cumulative log of all BUY/SELL signals across runs
- 🎨 **Color-coded rows** — Strong BUY (deep green), BUY (green), SELL (red), Strong SELL (deep red)
- 🧊 **Frozen headers** — Top 9 rows stay pinned while scrolling history
- ⏰ **IST timestamps** — Every signal logged with Indian Standard Time

---

## 🔧 Setup & Configuration

### Prerequisites
- Python 3.10+
- Google Cloud Service Account with Sheets & Drive API enabled
- API keys for Groq, Google Gemini, HuggingFace

### 1. Clone the repo
```bash
git clone https://github.com/pags666/Nitesh_NNSE.git
cd Nitesh_NNSE
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Add your credentials
Create a `credentials.json` file with your Google Service Account key (this file is gitignored).

### 4. Set environment variables
```bash
# Linux/Mac
export GROQ_API_KEY="your-groq-key"
export GEMINI_API_KEY="your-gemini-key"
export HF_TOKEN="your-huggingface-token"

# Windows PowerShell
$env:GROQ_API_KEY="your-groq-key"
$env:GEMINI_API_KEY="your-gemini-key"
$env:HF_TOKEN="your-huggingface-token"
```

### 5. Run locally
```bash
# Scrape data first
python NNSE.py
python BSE.py

# Run the signal engine
python multi_ai.py
```

---

## ☁️ GitHub Actions Automation

The pipeline runs automatically **every 20 minutes** via `.github/workflows/update.yaml`.

### Execution Order
```
1. NNSE.py          → Scrape NSE announcements
2. BSE.py           → Scrape BSE announcements
3. et.py            → Scrape Economic Times
4. monc.py          → Scrape MoneyControl
5. consolidated.py  → Single-model AI scan
6. words.py         → Keyword screening
7. ai.py            → Legacy AI analysis
8. multi_ai.py      → ⭐ 6-Model Consensus Engine
9. nifty_move.py    → Nifty direction bias
```

### Required GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret Name | Description |
|---|---|
| `GOOGLE_CREDENTIALS` | Full contents of your `credentials.json` |
| `GROQ_API_KEY` | Groq API key (for Llama models) |
| `GEMINI_API_KEY` | Google Gemini API key |
| `HF_TOKEN` | HuggingFace access token (for FinBERT) |

---

## 📈 Output Example

```
============================================================
  MULTI-AI STOCK SIGNAL ENGINE v3 -- 6 MODEL
  70B + 8B + Llama4Scout + Qwen3-32B + Gemini + FinBERT
============================================================

[1/45] TATAELXSI (NSE)
  70B=B85 | 8B=B78 | SCT=B82 | QWN=B80 | GEM=B88 | FB=B72 [6-MODEL]
  >>> ++ STRONG BUY | Score: 83

[2/45] YESBANK (NSE)
  70B=S70 | 8B=S65 | SCT=S72 | QWN=S68 | GEM=S75 | FB=S60 [6-MODEL]
  >>> -- STRONG SELL | Score: 71

============================================================
  FINAL SIGNALS
============================================================
  STOCK              ACTION         SCORE    70B    8B   SCT   QWN   GEM    FB
  ---------------------------------------------------------------------------
  TATAELXSI          STRONG BUY       83   B85   B78   B82   B80   B88   B72
  YESBANK            STRONG SELL      71   S70   S65   S72   S68   S75   S60
```

---

## 🛡️ Security

- `credentials.json` is in `.gitignore` — never pushed to GitHub
- All API keys stored as **GitHub Secrets** (encrypted)
- Service account JSON injected at runtime via secrets

---

## 📄 License

This project is for personal/educational use. Market signals are AI-generated and **should not be used as sole financial advice**. Always do your own research before trading.

---

## 👨‍💻 Author

**Nitesh Pags** — [@pags666](https://github.com/pags666)

---

<p align="center">
  <i>Built with 🧠 AI + ☕ caffeine + 📊 market obsession</i>
</p>
