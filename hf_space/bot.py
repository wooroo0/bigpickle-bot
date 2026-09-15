import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import steam_analyzer as sa

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PROXY = os.getenv("PROXY", "").strip() or os.getenv("HTTPS_PROXY", "").strip() or os.getenv("HTTP_PROXY", "").strip()

HELP_TEXT = (
    "🎯 <b>SteamOsint — Steam OSINT бот</b>\n\n"
    "Кидай SteamID64 или ссылку на профиль — получу аналитическую сводку "
    "рисков по игроку (Rust / CS2 / Dota 2): часы, статус, баны, трекеры.\n\n"
    "Форматы ввода:\n"
    "• SteamID64: <code>76561198000000000</code>\n"
    "• Ссылка: <code>https://steamcommunity.com/profiles/7656119...</code>\n"
    "• Кастомный URL: <code>https://steamcommunity.com/id/nickname</code>\n"
    "• Или просто <code>/analyze &lt;данные&gt;</code>\n\n"
    "Команды:\n"
    "• /start — приветствие\n"
    "• /help — это сообщение\n"
    "• /analyze &lt;id/ссылка&gt; — сразу разведка"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет, я <b>SteamOsint</b> — элитный аналитик Steam-профилей.\n"
        "Отправь SteamID64 или ссылку на профиль — соберу телеметрию и оценку рисков.\n\n"
        + HELP_TEXT,
        parse_mode=ParseMode.HTML,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.startswith("/analyze"):
        raw = " ".join(context.args or [])
    else:
        raw = update.message.text or ""
    raw = raw.strip()
    if not raw:
        await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)
        return

    status = await update.message.reply_text("🔍 Запускаю разведку...")
    try:
        steamid = sa.resolve_steamid(raw)
        profile = sa.fetch_profile(steamid)
        card = sa.build_card(profile)
    except sa.InvalidInputError as exc:
        await status.edit_text(str(exc), parse_mode=ParseMode.HTML)
        return
    except sa.ProfileNotFoundError:
        await status.edit_text("❌ Профиль не найден. Проверь ID или ссылку.")
        return
    except sa.SteamUnavailableError:
        await status.edit_text(
            "⚠️ Steam временно недоступен или профиль скрыт от выдачи. Попробуй позже."
        )
        return

    try:
        await status.edit_text(
            card, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except Exception:
        await update.message.reply_text(
            card, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )


def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_TOKEN_HERE":
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN не задан.\n"
            "Открой .env, вставь токен бота (от @BotFather) и при желании STEAM_API_KEY "
            "(https://steamcommunity.com/dev/apikey)."
        )
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    builder = Application.builder().token(TOKEN)
    if PROXY:
        builder = builder.proxy(PROXY)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze))

    max_seconds = float(os.getenv("BOT_MAX_SECONDS", "0") or 0)
    if max_seconds <= 0:
        print("SteamOsint bot started. Ctrl+C to stop.")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
        return

    asyncio.run(_run_timed(app, max_seconds))


async def _run_timed(app: Application, max_seconds: float):
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        print(f"SteamOsint bot started. Running {int(max_seconds)}s, then clean exit.")
        await asyncio.sleep(max_seconds)
        logging.getLogger(__name__).info("Max runtime reached. Stopping cleanly.")
        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    main()