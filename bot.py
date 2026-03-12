"""
Burmese-English Telegram Translator Bot
Automatically translates Burmese↔English in group chats.
Primary: OpenAI GPT-4o-mini. Fallback: Google Cloud Translation API.
"""

import os
import re
import logging
import html
from typing import Optional

import openai
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# Google credentials are loaded automatically from GOOGLE_APPLICATION_CREDENTIALS
# or from the GOOGLE_CREDENTIALS_JSON env var (see below).

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
MIN_MESSAGE_LENGTH = int(os.environ.get("MIN_MESSAGE_LENGTH", "2"))
COOLDOWN_SECONDS = float(os.environ.get("COOLDOWN_SECONDS", "1.0"))
ENABLE_ENGLISH_TO_BURMESE = os.environ.get("ENABLE_EN_TO_MY", "true").lower() == "true"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger("translator_bot")

# ---------------------------------------------------------------------------
# OpenAI client (primary translator)
# ---------------------------------------------------------------------------

_openai_client: Optional[openai.OpenAI] = None


def get_openai_client() -> openai.OpenAI:
    """Return a cached OpenAI client."""
    global _openai_client
    if _openai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY env var is not set")
        _openai_client = openai.OpenAI(api_key=api_key)
        logger.info("OpenAI client initialised.")
    return _openai_client


def translate_with_openai(text: str, target_lang: str) -> tuple[Optional[str], Optional[str]]:
    """Translate using OpenAI GPT-4o-mini. Returns (result, error_message)."""
    lang_name = "English" if target_lang == "en" else "Burmese"
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Translate the user's message to {lang_name}. "
                        "Return only the translated text, nothing else."
                    ),
                },
                {"role": "user", "content": text},
            ],
            max_tokens=1000,
            temperature=0,
        )
        return response.choices[0].message.content.strip(), None
    except Exception as e:
        logger.exception("OpenAI translation failed for target=%s", target_lang)
        return None, str(e)


# ---------------------------------------------------------------------------
# Google Cloud Translation client (fallback)
# ---------------------------------------------------------------------------

try:
    from google.cloud import translate_v2 as _gcloud_translate
    _GCLOUD_AVAILABLE = True
except ImportError:
    _GCLOUD_AVAILABLE = False

_translate_client = None


def get_translate_client():
    """Return a cached Google Cloud Translate client."""
    global _translate_client
    if _translate_client is None:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            creds_path = "/tmp/gcloud_creds.json"
            with open(creds_path, "w") as f:
                f.write(creds_json)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
            logger.info("Wrote Google credentials to %s", creds_path)
        _translate_client = _gcloud_translate.Client()
        logger.info("Google Cloud Translate client initialised.")
    return _translate_client


def translate_with_google(text: str, target_lang: str) -> Optional[str]:
    """Translate using Google Cloud Translation. Returns None on failure."""
    if not _GCLOUD_AVAILABLE:
        return None
    try:
        client = get_translate_client()
        result = client.translate(text, target_language=target_lang)
        return html.unescape(result["translatedText"])
    except Exception:
        logger.exception("Google Cloud translation failed for target=%s", target_lang)
        return None


# ---------------------------------------------------------------------------
# Zawgyi ↔ Unicode detection & conversion
# ---------------------------------------------------------------------------

try:
    from myanmar_tools import ZawgyiDetector, ZawgyiConverter
    _zg_detector = ZawgyiDetector()
    _zg_converter = ZawgyiConverter()
    MYANMAR_TOOLS_AVAILABLE = True
    logger.info("myanmar-tools loaded – Zawgyi detection enabled.")
except ImportError:
    MYANMAR_TOOLS_AVAILABLE = False
    logger.warning(
        "myanmar-tools not installed – Zawgyi detection disabled. "
        "Install with: pip install myanmar-tools"
    )


def normalise_myanmar(text: str) -> str:
    """Convert Zawgyi-encoded text to Unicode if needed."""
    if not MYANMAR_TOOLS_AVAILABLE:
        return text
    score = _zg_detector.get_zawgyi_probability(text)
    if score > 0.95:
        converted = _zg_converter.zawgyi_to_unicode(text)
        logger.debug("Zawgyi detected (%.2f) – converted to Unicode.", score)
        return converted
    return text


# ---------------------------------------------------------------------------
# Language helpers
# ---------------------------------------------------------------------------

# Myanmar Unicode block: U+1000 – U+109F
_MYANMAR_RE = re.compile(r"[\u1000-\u109F]")


def contains_myanmar_script(text: str) -> bool:
    """Fast check: does the text contain Myanmar script characters?"""
    return bool(_MYANMAR_RE.search(text))


def is_mostly_english(text: str) -> bool:
    """Heuristic: >50 % of alphabetic chars are Latin."""
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    total = sum(1 for c in text if c.isalpha())
    if total == 0:
        return False
    return (latin / total) > 0.5


# ---------------------------------------------------------------------------
# Per-group state (in-memory; resets on restart – fine for v1)
# ---------------------------------------------------------------------------

# Set of chat_ids where the bot is explicitly disabled.
_disabled_chats: set[int] = set()


# Simple per-chat cooldown tracking
_last_translation: dict[int, float] = {}


def _check_cooldown(chat_id: int) -> bool:
    """Return True if enough time has passed since last translation in chat."""
    import time
    now = time.time()
    last = _last_translation.get(chat_id, 0.0)
    if now - last < COOLDOWN_SECONDS:
        return False
    _last_translation[chat_id] = now
    return True


# ---------------------------------------------------------------------------
# Translation logic
# ---------------------------------------------------------------------------


def translate_text(text: str, target_lang: str) -> Optional[str]:
    """
    Translate *text* to *target_lang*.
    Tries OpenAI first; falls back to Google Cloud Translation on failure.
    Returns the translated string, or None if both fail.
    """
    result, err = translate_with_openai(text, target_lang)
    if result:
        logger.debug("Translation via OpenAI [target=%s]", target_lang)
        return result

    logger.warning("OpenAI failed (%s), falling back to Google Cloud Translate [target=%s]", err, target_lang)
    result = translate_with_google(text, target_lang)
    if result:
        logger.info("Translation via Google Cloud fallback [target=%s]", target_lang)
        return result

    logger.error("Both translation providers failed [target=%s]", target_lang)
    return None


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable the bot in this chat."""
    chat_id = update.effective_chat.id
    _disabled_chats.discard(chat_id)
    await update.message.reply_text(
        "✅ Translator enabled.\n"
        "I'll auto-translate Burmese → English"
        + (" and English → Burmese" if ENABLE_ENGLISH_TO_BURMESE else "")
        + " in this chat."
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable the bot in this chat."""
    chat_id = update.effective_chat.id
    _disabled_chats.add(chat_id)
    await update.message.reply_text("⏸️ Translator paused. Use /start to resume.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report current status."""
    chat_id = update.effective_chat.id
    active = chat_id not in _disabled_chats
    await update.message.reply_text(
        f"{'🟢 Active' if active else '🔴 Paused'}\n"
        f"MY→EN: on\n"
        f"EN→MY: {'on' if ENABLE_ENGLISH_TO_BURMESE else 'off'}\n"
        f"Min length: {MIN_MESSAGE_LENGTH}\n"
        f"Cooldown: {COOLDOWN_SECONDS}s\n\n"
        f"Use /test to verify the translation API is working."
    )


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Test both translation directions and report results."""
    await update.message.reply_text("🔄 Testing translation API…")

    results = []

    # Test MY → EN
    sample_my = "မင်္ဂလာပါ"
    openai_en, openai_err = translate_with_openai(sample_my, "en")
    if openai_en:
        results.append(f"✅ OpenAI MY→EN: '{sample_my}' → '{openai_en}'")
    else:
        results.append(f"❌ OpenAI MY→EN failed: {openai_err}")
        google_en = translate_with_google(sample_my, "en")
        if google_en:
            results.append(f"✅ Google fallback MY→EN: '{sample_my}' → '{google_en}'")
        else:
            results.append("❌ Google fallback MY→EN also failed — check GOOGLE_CREDENTIALS_JSON")

    # Test EN → MY
    if ENABLE_ENGLISH_TO_BURMESE:
        sample_en = "Hello"
        openai_my, openai_err2 = translate_with_openai(sample_en, "my")
        if openai_my:
            results.append(f"✅ OpenAI EN→MY: '{sample_en}' → '{openai_my}'")
        else:
            results.append(f"❌ OpenAI EN→MY failed: {openai_err2}")
            google_my = translate_with_google(sample_en, "my")
            if google_my:
                results.append(f"✅ Google fallback EN→MY: '{sample_en}' → '{google_my}'")
            else:
                results.append("❌ Google fallback EN→MY also failed — check GOOGLE_CREDENTIALS_JSON")
    else:
        results.append("⚠️ EN→MY: disabled (set ENABLE_EN_TO_MY=true in Railway to enable)")

    await update.message.reply_text("\n".join(results))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Core handler: detect language, translate, reply."""
    message = update.message
    if message is None or message.text is None:
        return

    chat_id = message.chat_id
    text = message.text.strip()

    # Skip if disabled, too short, or from a bot
    if chat_id in _disabled_chats:
        return
    if len(text) < MIN_MESSAGE_LENGTH:
        return
    if message.from_user and message.from_user.is_bot:
        return

    # Determine direction
    has_myanmar = contains_myanmar_script(text)
    mostly_en = is_mostly_english(text)

    target: Optional[str] = None
    flag = ""

    if has_myanmar and not mostly_en:
        # Burmese → English
        text = normalise_myanmar(text)
        target = "en"
        flag = "🇬🇧"
    elif mostly_en and not has_myanmar and ENABLE_ENGLISH_TO_BURMESE:
        # English → Burmese
        target = "my"
        flag = "🇲🇲"
    else:
        # Mixed / other – skip
        return

    # Cooldown check
    if not _check_cooldown(chat_id):
        logger.debug("Cooldown active for chat %s – skipping.", chat_id)
        return

    translated = translate_text(text, target)
    if translated is None:
        logger.error("Translation returned None for chat %s, target=%s", chat_id, target)
        await message.reply_text("⚠️ Translation failed — check Railway logs and Google credentials.")
        return

    if translated.strip().lower() != text.strip().lower():
        reply = f"{flag} {translated}"
        await message.reply_text(reply)
        logger.info(
            "Translated [%s→%s] in chat %s (%d chars)",
            "my" if target == "en" else "en",
            target,
            chat_id,
            len(text),
        )
    else:
        logger.debug("Translation identical to source for chat %s — skipping reply.", chat_id)


# ---------------------------------------------------------------------------
# Application entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    logger.info("Starting Burmese-English Translator Bot …")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("test", cmd_test))

    # Register message handler (text only, groups + private)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set bot commands (visible in Telegram UI)
    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Enable translation"),
                BotCommand("stop", "Pause translation"),
                BotCommand("status", "Show bot status"),
                BotCommand("test", "Test translation API"),
            ]
        )

    app.post_init = post_init

    # Use polling (simpler for Railway; webhook is also viable)
    logger.info("Bot is running (polling mode).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
