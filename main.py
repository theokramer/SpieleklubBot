import os
import logging
import json
import psycopg2
import psycopg2.extras
import pandas as pd

from telegram import (
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── LOGGING ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── POSTGRES-DB: VERBINDUNG UND FUNKTIONEN ──────────────────────────────────────

def get_db_connection():
    """
    Baut eine psycopg2-Verbindung zur Render-Postgres-DB 
    anhand der Environment-Variable DATABASE_URL auf.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Environment-Variable DATABASE_URL ist nicht gesetzt")
    conn = psycopg2.connect(dsn=database_url, cursor_factory=psycopg2.extras.DictCursor)
    return conn

def init_db() -> None:
    """
    Legt in Postgres die Tabelle user_state an, falls sie noch nicht existiert.
    Fügt anschließend fehlende Spalten hinzu, falls die Struktur
    älteren Versionen entspricht.
    Spalten:
      - chat_id    BIGINT PRIMARY KEY
      - first_name TEXT
      - last_name  TEXT
      - username   TEXT
      - selected   TEXT    (JSON-Array von game_ids)
      - ranking    TEXT    (JSON-Array von game_ids)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Tabelle anlegen, falls sie nicht existiert, mit allen Spalten
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_state (
            chat_id    BIGINT PRIMARY KEY
          , first_name TEXT
          , last_name  TEXT
          , username   TEXT
          , selected   TEXT
          , ranking    TEXT
        );
        """
    )

    # 2. Spalten ergänzen, falls sie in älterer Struktur fehlen
    alter_statements = [
        "ALTER TABLE user_state ADD COLUMN IF NOT EXISTS first_name TEXT;",
        "ALTER TABLE user_state ADD COLUMN IF NOT EXISTS last_name TEXT;",
        "ALTER TABLE user_state ADD COLUMN IF NOT EXISTS username TEXT;",
        "ALTER TABLE user_state ADD COLUMN IF NOT EXISTS selected TEXT;",
        "ALTER TABLE user_state ADD COLUMN IF NOT EXISTS ranking TEXT;"
    ]
    for stmt in alter_statements:
        cursor.execute(stmt)

    conn.commit()
    conn.close()
    logger.info("Postgres-Tabelle user_state ist eingerichtet (inkl. aller Spalten).")

def save_profile(chat_id: int, first_name: str, last_name: str, username: str) -> None:
    """
    Speichert oder aktualisiert Profil-Daten in Postgres.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO user_state (chat_id, first_name, last_name, username, selected, ranking)
        VALUES (%s, %s, %s, %s, NULL, NULL)
        ON CONFLICT (chat_id) DO UPDATE
          SET first_name = EXCLUDED.first_name,
              last_name  = EXCLUDED.last_name,
              username   = EXCLUDED.username
        """,
        (chat_id, first_name, last_name, username),
    )
    conn.commit()
    conn.close()
    logger.info(f"[DB] Profil gespeichert: chat_id={chat_id}, {first_name} {last_name}, @{username}")

def save_selected_and_ranking(chat_id: int, ids: list[int]) -> None:
    """
    Speichert oder aktualisiert in Postgres die Spalten 'selected' und 'ranking' 
    für diesen chat_id. Beides ist identisch, da die vom Nutzer gesendete Reihenfolge 
    zugleich die Rangfolge ist.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    json_ids = json.dumps(ids, ensure_ascii=False)

    cursor.execute(
        """
        INSERT INTO user_state (chat_id, first_name, last_name, username, selected, ranking)
        VALUES (%s, NULL, NULL, NULL, %s, %s)
        ON CONFLICT (chat_id) DO UPDATE
          SET selected = EXCLUDED.selected,
              ranking  = EXCLUDED.ranking
        """,
        (chat_id, json_ids, json_ids),
    )
    conn.commit()
    conn.close()
    logger.info(f"[DB] Ausgewählte IDs und Ranking gespeichert für chat_id={chat_id}: {ids}")


# ── EXCEL EINLESEN UND BEREINIGEN ────────────────────────────────────────────────

def load_games_from_excel(path: str) -> pd.DataFrame:
    """
    Liest die Excel-Datei ein und gibt einen DataFrame mit Spalten:
    [game_id, game_name, price]. Spiel-IDs sind fortlaufend ab 1.
    """
    df_raw = pd.read_excel(path, header=None)
    df_clean = df_raw.dropna(subset=[1]).loc[:, [1, 2]].copy()
    df_clean.columns = ["game_name", "price"]
    df_clean.insert(0, "game_id", range(1, len(df_clean) + 1))
    df_clean["game_name"] = df_clean["game_name"].astype(str)
    df_clean["price"] = df_clean["price"].astype(float)
    return df_clean

# Excel-Datei beim Start laden (Pfad anpassen, falls nötig)
GAMES_DF = load_games_from_excel("SpieleMitPreisenIDs.xlsx")
NUM_PER_PAGE = 10  # Anzahl Spiele pro Seite, kann angepasst werden
MAX_PAGE = (len(GAMES_DF) - 1) // NUM_PER_PAGE + 1  # Gesamtzahl der Seiten

# ── HILFSFUNKTION: SPIELELISTE ALS TEXT ──────────────────────────────────────────

def format_games_page() -> str:
    """
    Gibt einen Textblock zurück mit den Spielen und Preisen
    für die angegebene Seite (1-basiert).
    """
    slice_df = GAMES_DF.iloc[0:MAX_PAGE*NUM_PER_PAGE]

    lines = ["Eine Liste aller Spiele inklusive Bildern gibt es auch hier: https://nextcloud.hpi.de/s/HRo3qcRexPCS3TS \n"]
    for _, row in slice_df.iterrows():
        gid = int(row["game_id"])
        name = row["game_name"]
        price = float(row["price"])
        lines.append(f"{gid}. {name} — {price:.2f}€")
    lines.append(
        "\n"
        "Sende eine Kommaseparierte Liste von IDs in der Reihenfolge, in der du die Spiele bevorzugst.\n"
        "Beispiel: `1,5,10` (ID 1 ist dein Top-Priorität, dann ID 5, dann ID 10)."
    )
    return "\n".join(lines)


# ── HANDLER ──────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start: Profil speichern, initiale Erinnerung senden.
    """
    user = update.effective_user
    chat_id = update.effective_chat.id

    first_name = user.first_name or ""
    last_name = user.last_name or ""
    username = user.username or ""

    # In-memory initialisieren
    context.user_data.clear()

    # Profil in DB speichern
    save_profile(chat_id, first_name, last_name, username)

    text = (
    f"👋 Willkommen, {first_name}!\n\n"
    "Ich habe dein Profil gespeichert.\n\n"
    "📋 Befehle:\n"
    "`/games` – Zeigt die Spieleliste\n"
    "`/current` – Zeigt deine aktuelle Auswahl\n"
    "`/delete` – Löscht deine aktuelle Auswahl\n\n"
    "📨 Sende eine kommaseparierte Liste von IDs, um deine Favoriten anzugeben.\n"
    "Beispiel: `1,5,10` – ID 1 ist dein Favorit, dann ID 5, dann ID 10."
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

async def delete_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /delete: Löscht die aktuelle Auswahl für den Benutzer.
    """
    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE user_state
        SET selected = NULL, ranking = NULL
        WHERE chat_id = %s
        """,
        (chat_id,),
    )
    conn.commit()
    conn.close()

    context.user_data.pop("selected_ids", None)
    context.user_data.pop("ranking_ids", None)

    await context.bot.send_message(chat_id=chat_id, text="🗑️ Deine Auswahl wurde gelöscht.")


async def list_games(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /games: Zeigt die Spiele mit Preisen.
    """
    chat_id = update.effective_chat.id

    text = format_games_page()
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Verarbeitet Textnachrichten:
    - Jede gültige Kommaseparierte Liste von IDs wird direkt als Ranking interpretiert.
    - Speichert selected_ids = ranking_ids und bestätigt die Auswahl.
    """
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # IDs extrahieren (Komma-getrennt, nur Ziffern)
    parts = [p.strip() for p in text.split(",") if p.strip().isdigit()]
    if not parts:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Ich konnte keine gültigen IDs erkennen. Bitte sende etwas wie `1,5,10`.",
        )
        return

    ids = [int(p) for p in parts]
    # IDs validieren (müssen innerhalb 1..len(GAMES_DF) liegen)
    for gid in ids:
        if gid < 1 or gid > len(GAMES_DF):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Ungültige ID {gid}. Bitte wähle IDs zwischen 1 und {len(GAMES_DF)}."
            )
            return

    # Speicherung: ausgewählte IDs und Ranking identisch
    save_selected_and_ranking(chat_id, ids)
    context.user_data["selected_ids"] = ids
    context.user_data["ranking_ids"] = ids

    # Bestätigung mit Spielnamen
    names = [
        GAMES_DF.loc[GAMES_DF["game_id"] == gid, "game_name"].values[0]
        for gid in ids
    ]
    text_resp = (
        "✅ Deine Auswahl (in Prioritätsreihenfolge):\n"
        + "\n".join(f"{i+1}. {names[i]}" for i in range(len(names)))
        + "\n\n"
        "Wenn du erneut eine andere Reihenfolge senden möchtest, schicke einfach erneut die IDs."
    )
    await context.bot.send_message(chat_id=chat_id, text=text_resp)


async def current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /current: Zeigt die aktuelle Reihenfolge (Ranking) an, sofern vorhanden.
    Zum Löschen nutze /delete, zum Ändern sende neue IDs.
    """
    chat_id = update.effective_chat.id
    rank_ids = context.user_data.get("ranking_ids", [])

    if not rank_ids:
        await context.bot.send_message(chat_id=chat_id, text="Du hast noch keine Spiele ausgewählt.")
        return

    rank_names = [
        GAMES_DF.loc[GAMES_DF["game_id"] == gid, "game_name"].values[0]
        for gid in rank_ids
    ]
    text = (
        "🎮 Deine aktuelle Prioritätenliste:\n"
        + "\n".join(f"{i+1}. {rank_names[i]}" for i in range(len(rank_names)))
        + "\n\n"
        "Um sie zu ändern, sende einfach erneut deine ID-Liste."
    )
    await context.bot.send_message(chat_id=chat_id, text=text)


# ── HAUPTBAUSTEIN: DB INITIALISIEREN & WEBHOOK STARTEN ────────────────────────────

def main() -> None:
    # 1) Postgres-Tabelle anlegen (falls fehlt) und Spalten absichern
    init_db()

    # 2) Token und APP_URL aus Umgebung auslesen
    TOKEN = os.getenv("BOT_TOKEN")
    APP_URL = os.getenv("APP_URL")  # z.B. "https://mein-bot.onrender.com"
    if not TOKEN or not APP_URL:
        logger.error("Fehlende Umgebungsvariable BOT_TOKEN oder APP_URL")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # 3) Handler registrieren
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("games", list_games))
    app.add_handler(CommandHandler("current", current))
    app.add_handler(CommandHandler("delete", delete_selection))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 4) Webhook-Pfad und -URL
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
