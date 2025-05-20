import os
import logging
import re
import sqlite3
import json

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── LOGGING ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── DATENBANK: INITIALISIERUNG ───────────────────────────────────────────────────
DB_PATH = "user_data.db"  # SQLite-Datei im selben Verzeichnis wie das Script

def init_db() -> None:
    """
    Erzeugt die Tabelle user_state, falls sie noch nicht existiert.
    Spalten:
      - chat_id  INTEGER PRIMARY KEY
      - selected TEXT       → JSON-Array der ausgewählten Spiele
      - ranking  TEXT       → JSON-Array der finalen Rangfolge
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_state (
            chat_id  INTEGER PRIMARY KEY,
            selected TEXT,
            ranking  TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    logger.info("Datenbank initialisiert: Tabelle user_state sicher existiert.")


def save_selected(chat_id: int, selected_games: list[str]) -> None:
    """
    Speichert oder aktualisiert die Spalte 'selected' für diesen chat_id.
    Falls der Datensatz noch nicht existiert, wird er angelegt (mit ranking=NULL).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    json_selected = json.dumps(selected_games, ensure_ascii=False)

    # Prüfen, ob Eintrag bereits existiert
    cursor.execute("SELECT 1 FROM user_state WHERE chat_id = ?", (chat_id,))
    exists = cursor.fetchone() is not None

    if exists:
        cursor.execute(
            "UPDATE user_state SET selected = ? WHERE chat_id = ?",
            (json_selected, chat_id),
        )
    else:
        cursor.execute(
            "INSERT INTO user_state (chat_id, selected, ranking) VALUES (?, ?, NULL)",
            (chat_id, json_selected),
        )

    conn.commit()
    conn.close()
    logger.info(f"Gespeichert (selected) für chat_id={chat_id}: {selected_games}")


def save_ranking(chat_id: int, ranking: list[str]) -> None:
    """
    Speichert oder aktualisiert die Spalte 'ranking' für diesen chat_id.
    Wenn es noch keinen Datensatz gibt, wird er angelegt (selected=NULL).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    json_ranking = json.dumps(ranking, ensure_ascii=False)

    cursor.execute("SELECT 1 FROM user_state WHERE chat_id = ?", (chat_id,))
    exists = cursor.fetchone() is not None

    if exists:
        cursor.execute(
            "UPDATE user_state SET ranking = ? WHERE chat_id = ?",
            (json_ranking, chat_id),
        )
    else:
        cursor.execute(
            "INSERT INTO user_state (chat_id, selected, ranking) VALUES (?, NULL, ?)",
            (chat_id, json_ranking),
        )

    conn.commit()
    conn.close()
    logger.info(f"Gespeichert (ranking) für chat_id={chat_id}: {ranking}")


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
    """
    /start: Daten im context.user_data zurücksetzen und leere DB-Zeile anlegen,
    dann Keyboard senden.
    """
    chat_id = update.effective_chat.id
    # context.user_data zurücksetzen
    context.user_data.clear()
    context.user_data["selected_games"] = []
    context.user_data["awaiting_ranking"] = False
    context.user_data["ranking"] = []

    # In der DB leeren Datensatz anlegen (falls noch nicht vorhanden)
    save_selected(chat_id, [])
    # Ranking bleibt in der DB NULL, bis der Nutzer eine finale Rangfolge sendet.

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Willkommen beim Multi-Game Picker Bot!\n\n"
            "Bitte wähle per Klick die Spiele aus, die du möchtest. "
            "Tippe erneut auf ein Spiel, um es zu de-selektieren. "
            "Wenn du fertig bist, drücke ‚Done‘."
        ),
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Wähle deine Spiele aus:",
        reply_markup=build_multi_select_keyboard(context.user_data["selected_games"]),
    )


async def handle_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Wird ausgelöst, wenn ein Inline-Button getappt wird:
    - data (Spielname) togglen und in context.user_data["selected_games"] aktualisieren
    - In der DB speichern (save_selected)
    - Wenn Done getappt wurde, zur Ranking-Eingabe auffordern
    """
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    data = query.data
    selected_games: list[str] = context.user_data.get("selected_games", [])

    if data == "__DONE__":
        if not selected_games:
            # Keine Auswahl getroffen → Fehlermeldung und Keyboard neu senden
            await query.edit_message_text(text="⚠️ Du musst zuerst mindestens ein Spiel auswählen!")
            await context.bot.send_message(
                chat_id=chat_id,
                text="Wähle deine Spiele aus:",
                reply_markup=build_multi_select_keyboard(selected_games),
            )
            return

        # Auswahl abgeschlossen → Ranking-Modus starten
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

    # Ein einzelnes Spiel togglen
    if data in GAMES:
        if data in selected_games:
            selected_games.remove(data)
        else:
            selected_games.append(data)
        context.user_data["selected_games"] = selected_games

        # Sofort in der DB aktualisieren
        save_selected(chat_id, selected_games)

        # Keyboard-Nachricht so anpassen, dass die Häkchen korrekt angezeigt werden
        await query.edit_message_text(
            text="Wähle deine Spiele aus:",
            reply_markup=build_multi_select_keyboard(selected_games),
        )
        return

    # Fallback, sollte nicht eintreten
    await query.edit_message_text(text="❌ Da ist etwas schiefgelaufen. Bitte /start erneut.")


async def handle_ranking_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Wenn context.user_data["awaiting_ranking"] True ist,
    interpretieren wir eingehende Text-Nachrichten als Ranking.
    Validieren, speichern (DB + context.user_data) und bestätigen.
    """
    if not context.user_data.get("awaiting_ranking", False):
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    selected_games: list[str] = context.user_data.get("selected_games", [])

    # Teile den Text per Komma auf
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Ich erkenne keine Komma-Liste. Bitte formatiere so:\n`1. Chess, 2. Hangman, 3. 2048`",
            parse_mode="Markdown",
        )
        return

    # Entferne führende Nummerierung (z.B. "1. Chess" → "Chess")
    normalized: list[str] = []
    for part in parts:
        name = re.sub(r"^\s*\d+\.\s*", "", part)  # entfernt "1.", "2.", etc.
        normalized.append(name)

    # Prüfen, ob normalized genau dieselben Spiele enthält wie selected_games
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

    # Ranking ist valide → in context.user_data + DB speichern
    context.user_data["ranking"] = normalized
    context.user_data["awaiting_ranking"] = False

    save_ranking(chat_id, normalized)

    # Bestätigung an den Nutzer
    ranked_list_text = "\n".join(f"{i+1}. {game}" for i, game in enumerate(normalized))
    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Deine finale Rangfolge:\n\n" + ranked_list_text + "\n\nTippe /change, um neu zu starten.",
    )


async def change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /change: Setzt die Auswahl und das Ranking zurück,
    löscht in context.user_data und DB nur selected, nicht aber gespeichertes Ranking.
    """
    chat_id = update.effective_chat.id
    # context zurücksetzen (aber in der DB belassen wir die alten Daten)
    context.user_data["selected_games"] = []
    context.user_data["ranking"] = []
    context.user_data["awaiting_ranking"] = False

    # Für eine „echte“ Löschung aus der DB könntest du hier noch save_selected(chat_id, []) und/oder save_ranking(chat_id, []) aufrufen.
    # Im Standardfall belassen wir das Ranking–Feld in der DB, bis der Nutzer neu rankt.

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
    """
    /current: Zeigt dem Nutzer den aktuellen Status (selected + ranking) aus context.user_data an.
    (Optional: man könnte hier auch direkt aus der DB lesen.)
    """
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


# ── HAUPTBAUSTEIN: APPLICATION ERZEUGEN, DB INIT & WEBHOOK STARTEN ───────────────────────
def main() -> None:
    # 1) Datenbank initialisieren
    init_db()

    # 2) Bot-Konfiguration einlesen
    TOKEN = os.getenv("BOT_TOKEN")
    APP_URL = os.getenv("APP_URL")  # z.B. "https://mein-bot.onrender.com"
    if not TOKEN or not APP_URL:
        logger.error("Fehlende Umgebungsvariable BOT_TOKEN oder APP_URL")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # 3) Handler registrieren
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_selection_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ranking_message))
    app.add_handler(CommandHandler("change", change))
    app.add_handler(CommandHandler("current", current))

    # 4) Webhook-Pfad / -URL
    WEBHOOK_PATH = f"/{TOKEN}"
    WEBHOOK_URL = f"{APP_URL}/{TOKEN}"

    # 5) Bot im Webhook-Modus starten
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "8443")),
        url_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
