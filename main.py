import logging
import re
import os
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

# ── CONFIGURE LOGGING ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── GLOBALS ─────────────────────────────────────────────────────────────────────
# List of all possible games
GAMES = [
    "Chess",
    "Tic-Tac-Toe",
    "Hangman",
    "2048",
    "Sudoku",
]

# We will keep per‐chat state in context.user_data:
# - user_data["selected_games"]: a list of games the user has toggled ON
# - user_data["awaiting_ranking"]: True/False, whether the bot is waiting for their ranking message
# - user_data["ranking"]: final ordered list once they've successfully ranked


# ── BUILD A KEYBOARD FOR MULTI‐SELECT ────────────────────────────────────────────
def build_multi_select_keyboard(selected_games: list[str]) -> InlineKeyboardMarkup:
    """
    Returns an InlineKeyboardMarkup where each row is one game-button.
    If a game is in selected_games, we prefix a checkmark. At the bottom, we add "Done".
    """
    buttons = []
    for game in GAMES:
        label = f"✅ {game}" if game in selected_games else game
        # We use callback_data=game so when tapped, callback_query.data=="Chess", etc.
        buttons.append([InlineKeyboardButton(label, callback_data=game)])
    # Finally, a "Done" button
    buttons.append([InlineKeyboardButton("✅ Done", callback_data="__DONE__")])
    return InlineKeyboardMarkup(buttons)


# ── /start HANDLER ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear any existing state, initialize an empty 'selected_games' list,
    and send a greeting + the multi-select keyboard.
    """
    chat_id = update.effective_chat.id

    # Reset any previous state
    context.user_data.clear()
    context.user_data["selected_games"] = []
    context.user_data["awaiting_ranking"] = False
    context.user_data["ranking"] = []

    # Greeting message
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Welcome to the Multi-Game Picker Bot!\n\n"
            "Please tap on each game you'd like to include in your list. "
            "Tap again to unselect. When you’re done choosing, press “Done”."
        ),
    )

    # Send the inline keyboard for game selection
    await context.bot.send_message(
        chat_id=chat_id,
        text="Choose all the games you are interested in:",
        reply_markup=build_multi_select_keyboard(context.user_data["selected_games"]),
    )


# ── CALLBACKQUERY HANDLER FOR SELECTION + DONE ─────────────────────────────────
async def handle_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    This handles any button tap in the multi-select keyboard:
    - If callback_data is one of the game names, we toggle it ON/OFF in selected_games.
    - If callback_data == "__DONE__", we move to the ranking prompt (if at least one game).
    """
    query = update.callback_query
    await query.answer()  # Acknowledge the callback

    chat_id = query.message.chat.id
    data = query.data  # either a game name, or "__DONE__"

    selected_games: list[str] = context.user_data.get("selected_games", [])

    if data == "__DONE__":
        # User tapped the Done button
        if not selected_games:
            # They haven't chosen anything yet
            await query.edit_message_text(
                text="⚠️ You must select at least one game before pressing Done."
            )
            # Re‐send the keyboard so they can pick
            await context.bot.send_message(
                chat_id=chat_id,
                text="Choose all the games you are interested in:",
                reply_markup=build_multi_select_keyboard(selected_games),
            )
            return

        # OK: we have ≥1 game. Ask them to rank.
        context.user_data["awaiting_ranking"] = True

        # Compose a short prompt listing what they selected
        selected_str = ", ".join(selected_games)
        await query.edit_message_text(
            text=(
                f"You selected: *{selected_str}*\n\n"
                "Now please rank them in order of preference.\n"
                "Send a single message like:\n"
                "`1. Chess, 2. Hangman, 3. 2048`\n\n"
                "Make sure to include *all* selected games exactly once."
            ),
            parse_mode="Markdown",
        )
        return

    # Otherwise, data is a game name; toggle it
    if data in GAMES:
        if data in selected_games:
            selected_games.remove(data)
        else:
            selected_games.append(data)
        # Save back
        context.user_data["selected_games"] = selected_games

        # Edit the keyboard message so the checkmarks update
        await query.edit_message_text(
            text="Choose all the games you are interested in:",
            reply_markup=build_multi_select_keyboard(selected_games),
        )
        return

    # In case some unexpected callback_data shows up:
    await query.edit_message_text(text="❌ Something went wrong. Please send /start to retry.")


# ── MESSAGE HANDLER FOR RANKING PARSE ────────────────────────────────────────────
async def handle_ranking_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    When user_data['awaiting_ranking'] is True, we treat any text message
    as their ranking. We parse it, validate, and save if valid.
    """
    if not context.user_data.get("awaiting_ranking", False):
        # If we're not expecting ranking, ignore here
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    selected_games: list[str] = context.user_data.get("selected_games", [])

    # Split by commas
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ I couldn't detect any comma‐separated items. "
                "Please send your ranking like:\n"
                "`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )
        return

    # For each part, strip any leading digits/dots/spaces. E.g., "1. Chess" → "Chess"
    normalized: list[str] = []
    for part in parts:
        # Remove something like “1.” or “2.” at the start
        name = re.sub(r"^\s*\d+\.\s*", "", part)
        normalized.append(name)

    # Check that normalized exactly matches all selected_games (as sets),
    # and that there are no duplicates, and same length
    normalized_set = set(normalized)
    selected_set = set(selected_games)

    if normalized_set != selected_set or len(normalized) != len(selected_games):
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ The list you sent does not exactly match the games you selected.\n"
                f"You selected: *{', '.join(selected_games)}*\n"
                f"You ranked: *{', '.join(normalized)}*\n\n"
                "Please make sure to:\n"
                "  • Include every selected game exactly once.\n"
                "  • Use commas between each entry.\n\n"
                "Try again, e.g.:\n"
                "`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )
        return

    # If we get here, the ranking is valid.
    context.user_data["ranking"] = normalized
    context.user_data["awaiting_ranking"] = False

    # Confirm back to the user
    ranked_list_text = "\n".join(f"{i+1}. {game}" for i, game in enumerate(normalized))
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ Got it! Your final ranked list:\n\n"
            f"{ranked_list_text}\n\n"
            "If you want to pick or rank again, type /change."
        ),
    )


# ── /change HANDLER ─────────────────────────────────────────────────────────────
async def change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Let the user start over: clear selected_games & ranking & awaiting_ranking,
    then send the multi-select keyboard again.
    """
    chat_id = update.effective_chat.id

    # Clear the prior data
    context.user_data["selected_games"] = []
    context.user_data["ranking"] = []
    context.user_data["awaiting_ranking"] = False

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔄 Let's start over.\n\n"
            "Please tap on each game you'd like to include. "
            "When you're done, press “Done.”"
        ),
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Choose all the games you are interested in:",
        reply_markup=build_multi_select_keyboard([]),
    )


# ── /current HANDLER (OPTIONAL) ─────────────────────────────────────────────────
async def current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the user what they have currently selected/ranked (if anything).
    """
    chat_id = update.effective_chat.id
    selected_games = context.user_data.get("selected_games", [])
    ranking = context.user_data.get("ranking", [])

    if not selected_games:
        await context.bot.send_message(
            chat_id=chat_id,
            text="You haven't selected any games yet. Type /start to begin."
        )
        return

    if ranking:
        ranked_list_text = "\n".join(f"{i+1}. {game}" for i, game in enumerate(ranking))
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎮 Your current ranked list is:\n\n"
                f"{ranked_list_text}\n\n"
                "To change your selection or ranking, use /change."
            ),
        )
    else:
        # They have selected but not yet ranked
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "You currently have these selected (not yet ranked):\n"
                f"*{', '.join(selected_games)}*\n\n"
                "Please rank them by sending a message like:\n"
                "`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )


# ── MAIN ENTRYPOINT ─────────────────────────────────────────────────────────────
def main() -> None:
    """
    Set up the application, register handlers, and start polling.
    """
    TOKEN = os.getenv("BOT_TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start))
    # Inline‐button presses
    app.add_handler(CallbackQueryHandler(handle_selection_callback))
    # Ranking message (only used when awaiting_ranking is True)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ranking_message)
    )
    # /change
    app.add_handler(CommandHandler("change", change))
    # /current
    app.add_handler(CommandHandler("current", current))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()

import logging
from telegram import (
    Update,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
)

# ── CONFIGURE LOGGING ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── GLOBALS ─────────────────────────────────────────────────────────────────────
# List of all possible games
GAMES = [
    "Chess",
    "Tic-Tac-Toe",
    "Hangman",
    "2048",
    "Sudoku",
]

# We will keep per‐chat state in context.user_data:
# - user_data["selected_games"]: a list of games the user has toggled ON
# - user_data["awaiting_ranking"]: True/False, whether the bot is waiting for their ranking message
# - user_data["ranking"]: final ordered list once they've successfully ranked


# ── BUILD A KEYBOARD FOR MULTI‐SELECT ────────────────────────────────────────────
def build_multi_select_keyboard(selected_games: list[str]) -> InlineKeyboardMarkup:
    """
    Returns an InlineKeyboardMarkup where each row is one game-button.
    If a game is in selected_games, we prefix a checkmark. At the bottom, we add "Done".
    """
    buttons = []
    for game in GAMES:
        label = f"✅ {game}" if game in selected_games else game
        # We use callback_data=game so when tapped, callback_query.data=="Chess", etc.
        buttons.append([InlineKeyboardButton(label, callback_data=game)])
    # Finally, a "Done" button
    buttons.append([InlineKeyboardButton("✅ Done", callback_data="__DONE__")])
    return InlineKeyboardMarkup(buttons)


# ── /start HANDLER ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear any existing state, initialize an empty 'selected_games' list,
    and send a greeting + the multi-select keyboard.
    """
    chat_id = update.effective_chat.id

    # Reset any previous state
    context.user_data.clear()
    context.user_data["selected_games"] = []
    context.user_data["awaiting_ranking"] = False
    context.user_data["ranking"] = []

    # Greeting message
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Welcome to the Multi-Game Picker Bot!\n\n"
            "Please tap on each game you'd like to include in your list. "
            "Tap again to unselect. When you’re done choosing, press “Done”."
        ),
    )

    # Send the inline keyboard for game selection
    await context.bot.send_message(
        chat_id=chat_id,
        text="Choose all the games you are interested in:",
        reply_markup=build_multi_select_keyboard(context.user_data["selected_games"]),
    )


# ── CALLBACKQUERY HANDLER FOR SELECTION + DONE ─────────────────────────────────
async def handle_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    This handles any button tap in the multi-select keyboard:
    - If callback_data is one of the game names, we toggle it ON/OFF in selected_games.
    - If callback_data == "__DONE__", we move to the ranking prompt (if at least one game).
    """
    query = update.callback_query
    await query.answer()  # Acknowledge the callback

    chat_id = query.message.chat.id
    data = query.data  # either a game name, or "__DONE__"

    selected_games: list[str] = context.user_data.get("selected_games", [])

    if data == "__DONE__":
        # User tapped the Done button
        if not selected_games:
            # They haven't chosen anything yet
            await query.edit_message_text(
                text="⚠️ You must select at least one game before pressing Done."
            )
            # Re‐send the keyboard so they can pick
            await context.bot.send_message(
                chat_id=chat_id,
                text="Choose all the games you are interested in:",
                reply_markup=build_multi_select_keyboard(selected_games),
            )
            return

        # OK: we have ≥1 game. Ask them to rank.
        context.user_data["awaiting_ranking"] = True

        # Compose a short prompt listing what they selected
        selected_str = ", ".join(selected_games)
        await query.edit_message_text(
            text=(
                f"You selected: *{selected_str}*\n\n"
                "Now please rank them in order of preference.\n"
                "Send a single message like:\n"
                "`1. Chess, 2. Hangman, 3. 2048`\n\n"
                "Make sure to include *all* selected games exactly once."
            ),
            parse_mode="Markdown",
        )
        return

    # Otherwise, data is a game name; toggle it
    if data in GAMES:
        if data in selected_games:
            selected_games.remove(data)
        else:
            selected_games.append(data)
        # Save back
        context.user_data["selected_games"] = selected_games

        # Edit the keyboard message so the checkmarks update
        await query.edit_message_text(
            text="Choose all the games you are interested in:",
            reply_markup=build_multi_select_keyboard(selected_games),
        )
        return

    # In case some unexpected callback_data shows up:
    await query.edit_message_text(text="❌ Something went wrong. Please send /start to retry.")


# ── MESSAGE HANDLER FOR RANKING PARSE ────────────────────────────────────────────
async def handle_ranking_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    When user_data['awaiting_ranking'] is True, we treat any text message
    as their ranking. We parse it, validate, and save if valid.
    """
    if not context.user_data.get("awaiting_ranking", False):
        # If we're not expecting ranking, ignore here
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    selected_games: list[str] = context.user_data.get("selected_games", [])

    # Split by commas
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ I couldn't detect any comma‐separated items. "
                "Please send your ranking like:\n"
                "`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )
        return

    # For each part, strip any leading digits/dots/spaces. E.g., "1. Chess" → "Chess"
    normalized: list[str] = []
    for part in parts:
        # Remove something like “1.” or “2.” at the start
        name = re.sub(r"^\s*\d+\.\s*", "", part)
        normalized.append(name)

    # Check that normalized exactly matches all selected_games (as sets),
    # and that there are no duplicates, and same length
    normalized_set = set(normalized)
    selected_set = set(selected_games)

    if normalized_set != selected_set or len(normalized) != len(selected_games):
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ The list you sent does not exactly match the games you selected.\n"
                f"You selected: *{', '.join(selected_games)}*\n"
                f"You ranked: *{', '.join(normalized)}*\n\n"
                "Please make sure to:\n"
                "  • Include every selected game exactly once.\n"
                "  • Use commas between each entry.\n\n"
                "Try again, e.g.:\n"
                "`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )
        return

    # If we get here, the ranking is valid.
    context.user_data["ranking"] = normalized
    context.user_data["awaiting_ranking"] = False

    # Confirm back to the user
    ranked_list_text = "\n".join(f"{i+1}. {game}" for i, game in enumerate(normalized))
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ Got it! Your final ranked list:\n\n"
            f"{ranked_list_text}\n\n"
            "If you want to pick or rank again, type /change."
        ),
    )


# ── /change HANDLER ─────────────────────────────────────────────────────────────
async def change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Let the user start over: clear selected_games & ranking & awaiting_ranking,
    then send the multi-select keyboard again.
    """
    chat_id = update.effective_chat.id

    # Clear the prior data
    context.user_data["selected_games"] = []
    context.user_data["ranking"] = []
    context.user_data["awaiting_ranking"] = False

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔄 Let's start over.\n\n"
            "Please tap on each game you'd like to include. "
            "When you're done, press “Done.”"
        ),
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Choose all the games you are interested in:",
        reply_markup=build_multi_select_keyboard([]),
    )


# ── /current HANDLER (OPTIONAL) ─────────────────────────────────────────────────
async def current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the user what they have currently selected/ranked (if anything).
    """
    chat_id = update.effective_chat.id
    selected_games = context.user_data.get("selected_games", [])
    ranking = context.user_data.get("ranking", [])

    if not selected_games:
        await context.bot.send_message(
            chat_id=chat_id,
            text="You haven't selected any games yet. Type /start to begin."
        )
        return

    if ranking:
        ranked_list_text = "\n".join(f"{i+1}. {game}" for i, game in enumerate(ranking))
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎮 Your current ranked list is:\n\n"
                f"{ranked_list_text}\n\n"
                "To change your selection or ranking, use /change."
            ),
        )
    else:
        # They have selected but not yet ranked
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "You currently have these selected (not yet ranked):\n"
                f"*{', '.join(selected_games)}*\n\n"
                "Please rank them by sending a message like:\n"
                "`1. Chess, 2. Hangman, 3. 2048`"
            ),
            parse_mode="Markdown",
        )


# ── MAIN ENTRYPOINT ─────────────────────────────────────────────────────────────
def main() -> None:
    """
    Set up the application, register handlers, and start polling.
    """
    TOKEN = "7822570197:AAFLnze08mxilQGqXagoSvhz5pvI2wokIRU"
    app = ApplicationBuilder().token(TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start))
    # Inline‐button presses
    app.add_handler(CallbackQueryHandler(handle_selection_callback))
    # Ranking message (only used when awaiting_ranking is True)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ranking_message)
    )
    # /change
    app.add_handler(CommandHandler("change", change))
    # /current
    app.add_handler(CommandHandler("current", current))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()

