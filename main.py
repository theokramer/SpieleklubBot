# main.py
import os
import logging
import re

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

# ── LOGGING ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── KONSTANTEN ───────────────────────────────────────────────────────────────────
# Liste möglicher Spiele
GAMES = ["Chess", "Tic-Tac-Toe", "Hangman", "2048", "Sudoku"]

def build_multi_select_keyboard(selected_games: list[str]) -> InlineKeyboardMarkup:
    buttons = []
    for game in GAMES:
        label = f"✅ {game}" if game in selected_games else game
        buttons.append([InlineKeyboardButton(label, callback_data=game)])
    buttons.append([InlineKeyboardButton("✅ Done", callback_data="__DONE__")])
    return InlineKeyboardMarkup(buttons)

# ── HANDLER ──────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    context.user_data.clear()
    context.user_data["selected_games"] = []
    context.user_data["awaiting_ranking"] = False
    context.user_data["ranking"] = []

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Willkommen beim Multi-Game Picker Bot!\n\n"
            "Bitte wähle per Klick die Spiele aus, die du möchtest. "
            "Tippe erneut auf ein Spiel, um es zu de-selektieren. "
            "Wenn du fertig bist, drücke 'Done'."
        ),
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Wähle deine Spiele aus:",
        reply_markup=build_multi_select_keyboard(context.user_data["selected_games"]),
    )

async def handle_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    selected_games: list[str] = context.user_data.get("selected_games", [])

    if data == "__DONE__":
        if not selected_games:
            await query.edit_message_text(text="⚠️ Du musst zuerst mindestens ein Spiel auswählen!")
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text="Wähle deine Spiele aus:",
                reply_markup=build_multi_select_keyboard(selected_games),
            )
            return

        context.user_data["awaiting_ranking"] = True
        selected_str = ", ".join(selected_games)
        await query.edit_message_text(
            text=(
                f"Du hast ausgewählt: *{selected_str}*\n\n"
                "Bitte ordne sie jetzt nach Präferenz.\n"
                "Schreibe z.B.:\n"
                "`1. Chess, 2. Hangman, 3. 2048`\n\n"
                "Denke daran, *alle* ausgewählten Spiele einmalig aufzulisten."
            ),
            parse_mode="Markdown",
        )
        return

    # Toggle ein einzelnes Spiel
    if data in GAMES:
        if data in selected_games:
            selected_games.remove(data)
        else:
            selected_games.append(data)
        context.user_data["selected_games"] = selected_games

        await query.edit_message_text(
            text="Wähle deine Spiele aus:",
            reply_markup=build_multi_select_keyboard(selected_games),
        )
        return

    # Fallback
    await query.edit_message_text(text="❌ Da ist etwas schiefgelaufen. Bitte /start erneut.")

async def handle_ranking_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("awaiting_ranking", False):
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    selected_games: list[str] = context.user_data.get("selected_games", [])

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Ich erkenne keine Komma-Liste. Bitte formatiere so:\n`1. Chess, 2. Hangman, 3. 2048`",
            parse_mode="Markdown",
        )
        return

    normalized = []
    for part in parts:
        name = re.sub(r"^\s*\d+\.\s*", "", part)
        normalized.append(name)

    if set(normalized) != set(selected_games) or len(normalized) != len(selected_games):
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ Deine Liste stimmt nicht genau mit den ausgewählten Spielen überein.\n"
                f"Du hast ausgewählt: *{', '.join(selected_games)}*\n"
                f"Du hast gerankt: *{', '.join(normalized)}*\n\n"
                "Stelle sicher, dass:\n"
                "  • Du alle ausgewählten Spiele exakt einmal nennst.\n"
                "  • Kommas zwischen jedem Eintrag stehen.\n\n"
                "Versuche es erneut:\n`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )
        return

    context.user_data["ranking"] = normalized
    context.user_data["awaiting_ranking"] = False

    ranked_list_text = "\n".join(f"{i+1}. {game}" for i, game in enumerate(normalized))
    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Deine finale Rangfolge:\n\n" + ranked_list_text + "\n\nTippe /change, um neu zu starten.",
    )

async def change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    context.user_data["selected_games"] = []
    context.user_data["ranking"] = []
    context.user_data["awaiting_ranking"] = False

    await context.bot.send_message(
        chat_id=chat_id,
        text="🔄 Neu starten: Wähle deine Spiele per Klick aus.",
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Wähle deine Spiele aus:",
        reply_markup=build_multi_select_keyboard([]),
    )

async def current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    selected_games = context.user_data.get("selected_games", [])
    ranking = context.user_data.get("ranking", [])

    if not selected_games:
        await context.bot.send_message(chat_id=chat_id, text="Du hast noch nichts ausgewählt. /start")
        return
    if ranking:
        ranked_list_text = "\n".join(f"{i+1}. {g}" for i, g in enumerate(ranking))
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎮 Aktuelle Rangfolge:\n\n" + ranked_list_text + "\n\nTippe /change, um neu zu starten.",
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Du hast ausgewählt, aber noch nicht gerankt:\n"
                f"*{', '.join(selected_games)}*\n\n"
                "Bitte ranke mit:\n`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )

# ── HAUPTBAUSTEIN: APPLICATION ERZEUGEN UND WEBHOOK STARTEN ───────────────────────
def main() -> None:
    TOKEN = os.getenv("BOT_TOKEN")
    APP_URL = os.getenv("APP_URL")  # z.B. "https://mein-bot.onrender.com"
    if not TOKEN or not APP_URL:
        logger.error("Fehlende Umgebungsvariable BOT_TOKEN oder APP_URL")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # Handler registrieren
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_selection_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ranking_message))
    app.add_handler(CommandHandler("change", change))
    app.add_handler(CommandHandler("current", current))

    # Stelle den Webhook ein (Telegram weiß, wohin es Updates schicken muss)
    # "/webhook" ist der Pfad – Render leitet HTTP POST an diesen Pfad weiter
    WEBHOOK_PATH = f"/{TOKEN}"
    WEBHOOK_URL = f"{APP_URL}/{TOKEN}"

    # Setze den Webhook bei Bot-Start
    async def on_startup() -> None:
        await app.bot.set_webhook(WEBHOOK_URL)

    # Starte den Webserver mit run_webhook()
    # Wir lauschen auf alle Interfaces (0.0.0.0) und PORT von Render
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "8443")),
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
    )

if __name__ == "__main__":
    main()
