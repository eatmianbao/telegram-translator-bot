# Burmese ↔ English Telegram Translator Bot

Autonomous translator bot for Telegram group chats. Detects Burmese and English messages and replies with the translation. Handles Zawgyi-encoded text automatically.

## Features

- **Burmese → English** translation (always on)
- **English → Burmese** translation (configurable)
- **Zawgyi detection** — auto-converts Zawgyi to Unicode before translating
- Per-group `/start` and `/stop` commands
- Cooldown to avoid spam in active groups
- Configurable minimum message length

## Prerequisites

1. **Telegram Bot Token** — create via [@BotFather](https://t.me/BotFather)
2. **Google Cloud project** with the Cloud Translation API enabled
3. **Service account JSON key** for that project

### BotFather setup (important)

After creating the bot, **disable Privacy Mode** so the bot can read all group messages:

1. Open @BotFather
2. `/mybots` → select your bot
3. **Bot Settings → Group Privacy → Turn off**

Without this, the bot will only see `/commands`, not regular messages.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from BotFather |
| `GOOGLE_CREDENTIALS_JSON` | ✅ | — | Full JSON string of your GCP service account key |
| `LOG_LEVEL` | — | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MIN_MESSAGE_LENGTH` | — | `2` | Ignore messages shorter than this |
| `COOLDOWN_SECONDS` | — | `1.0` | Min seconds between translations per chat |
| `ENABLE_EN_TO_MY` | — | `true` | Enable English → Burmese direction |

## Deploy to Railway

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:YOUR_USER/telegram-translator-bot.git
git push -u origin main
```

### 2. Create Railway service

1. Go to [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**
2. Select your repo.
3. Railway will detect the `Dockerfile` automatically.

### 3. Set environment variables

In the Railway service dashboard → **Variables** tab, add:

- `TELEGRAM_BOT_TOKEN` = your token
- `GOOGLE_CREDENTIALS_JSON` = paste the **entire contents** of your service account JSON file

### 4. Deploy

Railway deploys automatically on push. The bot runs in **polling mode** — no public URL or PORT needed.

Check **Logs** in Railway to confirm startup.

## Local Development

```bash
# Clone and enter directory
cp .env.example .env
# Edit .env with your real credentials

pip install -r requirements.txt
python bot.py
```

## Google Cloud Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. **APIs & Services → Enable APIs → Cloud Translation API**
4. **IAM & Admin → Service Accounts → Create**
5. Grant role: **Cloud Translation API User**
6. Create a JSON key → download it
7. Paste the full JSON contents into the `GOOGLE_CREDENTIALS_JSON` env var

## Commands

| Command | Description |
|---|---|
| `/start` | Enable translation in this chat |
| `/stop` | Pause translation in this chat |
| `/status` | Show current bot configuration |

## Cost Estimate

Google Cloud Translation API pricing (as of 2025):
- **First 500,000 characters/month** — free
- After that: **$20 per 1 million characters**

A typical group chat message is ~100 characters, so the free tier covers ~5,000 messages/month.

## Architecture

```
Telegram Group Message
        │
        ▼
   Bot receives text
        │
        ▼
   Contains Myanmar script? ──yes──▶ Normalise Zawgyi → Unicode
        │                                      │
        no                                     ▼
        │                              Translate MY → EN
        ▼                              Reply with 🇬🇧
   Mostly English? ──yes──▶ Translate EN → MY
        │                   Reply with 🇲🇲
        no
        │
        ▼
      (ignore)
```
