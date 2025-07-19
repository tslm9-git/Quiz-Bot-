import json
import asyncio
import logging
import os
import sys
from datetime import datetime
import threading
import random
import time
from keep_alive_enhanced import keep_alive

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.error import Conflict
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        ContextTypes,
        filters
    )
except ImportError as e:
    logger.error(f"Failed to import telegram modules: {e}")
    logger.error("Please ensure python-telegram-bot is installed correctly")
    sys.exit(1)

# Get token from environment variables with fallback
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
    sys.exit(1)

# Database service
from database_service import DatabaseService, initialize_database
from anime_quiz_data import get_random_anime_questions, get_quiz_by_difficulty

# Initialize database on startup
initialize_database()

# Constants
BOT_OWNER_ID = 6640947043  # Your Telegram ID

# In-memory session storage
sessions = {}
waiting_rooms = {}
active_quizzes = {}
countdown_jobs = {}  # Store countdown jobs
user_stats = {}  # User statistics
quiz_categories = {}  # Quiz categories
streak_data = {}  # User streak data
auto_quiz_scheduler = {}  # Auto quiz scheduler

# Generate unique quiz ID
def generate_quiz_id():
    return datetime.now().strftime("%f%S%M")[:6]

# Quiz creation handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].startswith('startquiz-'):
        quiz_id = context.args[0].split('-')[1]
        await start_quiz_in_group(update, context, quiz_id)
    else:
        welcome_text = (
            "🎯 *Welcome to QuizzerrBot!* 🎯\n\n"
            "🔥 *Commands:*\n"
            "/createquiz - Create a new quiz\n"
            "/mystats - View your personal stats\n"
            "/topplayers - View top players\n"
            "/dailychallenge - Daily challenge quiz\n"
            "/quickquiz - Random quick quiz\n"
            "/categories - Browse quiz categories\n"
            "/streak - View your streak\n"
            "/stats - View bot statistics\n"
            "/help - Show this help message\n\n"
            "🎮 *How to play:*\n"
            "1. Create a quiz using /createquiz\n"
            "2. Share the quiz link in group chats\n"
            "3. Players click 'Ready' to join\n"
            "4. Quiz starts when 2+ players are ready\n\n"
            "💡 *Cool Features:*\n"
            "• 🏆 Personal statistics & rankings\n"
            "• 🔥 Streak system & achievements\n"
            "• 📚 Quiz categories & difficulty levels\n"
            "• ⚡ Quick random quizzes\n"
            "• 🎯 Daily challenges\n"
            "• 💬 Hints system\n"
            "• 🎨 Multiple question types\n"
            "• 🏅 Leaderboards & badges\n\n"
            "_Let's quiz! 🚀_"
        )
        await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def createquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        await update.message.reply_text("❌ Quiz creation only available in private chats!")
        return

    user_id = update.message.from_user.id
    
    # Initialize quiz creation session with enhanced features
    sessions[user_id] = {
        "questions": [],
        "stage": "category",
        "category": None,
        "difficulty": None,
        "title": None,
        "description": None
    }

    # Show category selection
    keyboard = [
        [InlineKeyboardButton("📚 General Knowledge", callback_data="cat_general")],
        [InlineKeyboardButton("🧪 Science", callback_data="cat_science")],
        [InlineKeyboardButton("🏛️ History", callback_data="cat_history")],
        [InlineKeyboardButton("🎮 Entertainment", callback_data="cat_entertainment")],
        [InlineKeyboardButton("⚽ Sports", callback_data="cat_sports")],
        [InlineKeyboardButton("💻 Technology", callback_data="cat_technology")],
        [InlineKeyboardButton("🌍 Geography", callback_data="cat_geography")],
        [InlineKeyboardButton("🎨 Art & Culture", callback_data="cat_culture")],
        [InlineKeyboardButton("📖 Literature", callback_data="cat_literature")],
        [InlineKeyboardButton("🔤 Custom", callback_data="cat_custom")]
    ]
    
    await update.message.reply_text(
        "🎯 *Create Your Quiz!* 🎯\n\n"
        "First, choose a category for your quiz:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in sessions:
        del sessions[user_id]
        await update.message.reply_text("❌ Quiz creation cancelled.")
    else:
        await update.message.reply_text("❌ No active quiz to cancel.")

async def handle_quiz_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in sessions:
        return

    session = sessions[user_id]
    text = update.message.text
    
    # Handle different stages of quiz creation
    if session["stage"] == "title":
        session["title"] = text
        session["stage"] = "description"
        await update.message.reply_text(
            f"✅ Title: {text}\n\n"
            "📝 Send a short description for your quiz (optional, or type 'skip'):"
        )
        return
    
    elif session["stage"] == "description":
        if text.lower() != "skip":
            session["description"] = text
        session["stage"] = "questions"
        
        await update.message.reply_text(
            f"✅ Quiz Setup Complete!\n\n"
            f"🏷️ Title: {session['title']}\n"
            f"📚 Category: {session['category']}\n"
            f"🎯 Difficulty: {session['difficulty']}\n"
            f"📝 Description: {session.get('description', 'No description')}\n\n"
            "Now send questions in this format:\n"
            "Question,Option1,Option2,Option3,Option4,CorrectOptionNumber\n\n"
            "Example:\n"
            "What is 2+2?,3,4,5,6,2\n\n"
            "Send /done when finished or /cancel to quit."
        )
        return
    
    elif session["stage"] == "questions":
        # Handle question input
        parts = text.split(',')

        if len(parts) != 6:
            await update.message.reply_text("❌ Invalid format. Please use: Question,Opt1,Opt2,Opt3,Opt4,CorrectNum")
            return

        try:
            correct_idx = int(parts[5].strip()) - 1
            if not (0 <= correct_idx <= 3):
                raise ValueError
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Correct option must be a number between 1-4")
            return

        question = {
            "text": parts[0].strip(),
            "options": [p.strip() for p in parts[1:5]],
            "answer": correct_idx
        }

        session["questions"].append(question)
        await update.message.reply_text(f"✅ Question {len(session['questions'])} added!")

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in sessions or not sessions[user_id]["questions"]:
        await update.message.reply_text("❌ No active quiz to save")
        return

    session = sessions[user_id]
    quiz_id = generate_quiz_id()
    quiz_data = load_data()
    
    # Update user stats
    if user_id not in user_stats:
        user_stats[user_id] = {
            "games_played": 0,
            "questions_answered": 0,
            "correct_answers": 0,
            "quizzes_created": 0,
            "best_streak": 0,
            "current_streak": 0,
            "achievements": [],
            "total_score": 0,
            "rank": "🥉 Bronze"
        }
    
    # Update user stats in database
    DatabaseService.update_user_stats(
        user_id,
        quizzes_created_increment=1,
        total_score_increment=10
    )
    
    quiz_data = {
        "id": quiz_id,
        "title": session.get("title", "Untitled Quiz"),
        "description": session.get("description", ""),
        "category": session.get("category", "📚 General Knowledge"),
        "difficulty": session.get("difficulty", "🟡 Medium"),
        "questions": session["questions"],
        "creator": user_id,
        "created_at": datetime.now().isoformat(),
        "plays": 0,
        "avg_score": 0,
        "ratings": []
    }
    
    # Save quiz to database
    success = DatabaseService.create_quiz(quiz_data)
    if not success:
        await update.message.reply_text("❌ Error creating quiz. Please try again.")
        return
    
    del sessions[user_id]
    
    share_link = f"https://t.me/{TOKEN.split(':')[0].replace('bot', '')}?start=startquiz-{quiz_id}"
    
    await update.message.reply_text(
        f"🎉 *Quiz Created Successfully!* 🎉\n\n"
        f"🏷️ **{quiz_data['title']}**\n"
        f"📚 {quiz_data['category']}\n"
        f"🎯 {quiz_data['difficulty']}\n"
        f"❓ {len(quiz_data['questions'])} questions\n\n"
        f"🔗 **Share this link in group chats:**\n`{share_link}`\n\n"
        f"📊 Quiz ID: `{quiz_id}`\n"
        f"⭐ Creator bonus: +10 points!",
        parse_mode="Markdown"
    )

# Group quiz handlers
async def start_quiz_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz_id: str):
    chat_id = update.effective_chat.id
    quiz_data = DatabaseService.get_quiz(quiz_id)

    if not quiz_data:
        await context.bot.send_message(chat_id, "❌ Quiz not found!")
        return

    # Check if quiz is already running in this group
    if chat_id in active_quizzes or chat_id in waiting_rooms:
        await context.bot.send_message(chat_id, "❌ A quiz is already running in this group!")
        return

    # Create waiting room and send initial message
    keyboard = [[InlineKeyboardButton("✅ I'm Ready", callback_data=f"ready_{quiz_id}")]]
    msg = await context.bot.send_message(
        chat_id,
        f"🧠 Quiz incoming! ({len(quiz_data['questions'])} questions)\n"
        f"Click the button when you're ready.\n"
        f"At least 2 players needed to start.\n\n"
        f"👤 Players ready: 0",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Store waiting room with message ID
    waiting_rooms[chat_id] = {
        "quiz_id": quiz_id,
        "ready_users": set(),
        "scores": {},
        "answered": {},
        "questions": quiz_data["questions"],
        "creator": quiz_data["creator"],
        "message_id": msg.message_id  # Store message ID for updates
    }

async def ready_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    quiz_id = query.data.split('_')[1]

    if chat_id not in waiting_rooms or waiting_rooms[chat_id]["quiz_id"] != quiz_id:
        await query.answer("❌ Quiz not active anymore!")
        return

    room = waiting_rooms[chat_id]

    # Skip if user already ready
    if user_id in room["ready_users"]:
        await query.answer("You're already ready!")
        return

    # Add user to ready list
    room["ready_users"].add(user_id)
    room["scores"][user_id] = room["scores"].get(user_id, 0)
    count = len(room['ready_users'])

    # Update message with new player count
    try:
        await context.bot.edit_message_text(
            f"🧠 Quiz incoming! ({len(room['questions'])} questions)\n"
            f"Click the button when you're ready.\n"
            f"At least 2 players needed to start.\n\n"
            f"👤 Players ready: {count}",
            chat_id=chat_id,
            message_id=room["message_id"],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ I'm Ready", callback_data=f"ready_{quiz_id}")]
            ])
        )
    except Exception as e:
        logger.error(f"Error updating ready message: {e}")

    await query.answer(f"You're ready! ({count} players)")

    # Start countdown IMMEDIATELY if enough players
    if count >= 2 and chat_id not in countdown_jobs:
        countdown_jobs[chat_id] = True
        # Start countdown immediately using asyncio task
        asyncio.create_task(start_countdown(context, chat_id))

async def start_countdown(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        if chat_id not in waiting_rooms:
            return

        room = waiting_rooms[chat_id]
        count = len(room['ready_users'])

        # Update message to show countdown starting
        try:
            await context.bot.edit_message_text(
                f"🎉 {count} players ready! Starting quiz in 5...",
                chat_id=chat_id,
                message_id=room["message_id"]
            )
        except:
            pass

        # Countdown sequence (5 seconds)
        for i in range(5, 0, -1):
            try:
                await context.bot.edit_message_text(
                    f"🎉 {count} players ready! Starting quiz in {i}...",
                    chat_id=chat_id,
                    message_id=room["message_id"]
                )
            except:
                pass
            await asyncio.sleep(1)

        # Start the quiz
        await start_quiz(context, chat_id)
    finally:
        # Clean up countdown job
        if chat_id in countdown_jobs:
            del countdown_jobs[chat_id]

async def start_quiz(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    if chat_id not in waiting_rooms:
        return

    room = waiting_rooms[chat_id]
    active_quizzes[chat_id] = {
        **room,
        "current_q": 0,
        "started": True,
        "message_ids": []  # Store question message IDs
    }
    del waiting_rooms[chat_id]

    # Delete the waiting room message
    try:
        await context.bot.delete_message(chat_id, room["message_id"])
    except:
        pass

    # Send engaging start message
    emojis = ["🚀", "🌟", "🔥", "⚡", "🎯", "🏆"]
    emoji = emojis[min(len(room['ready_users']) - 2, len(emojis) - 1)]
    await context.bot.send_message(
        chat_id,
        f"{emoji} The battle of wits begins! {emoji}\n"
        f"Fastest fingers will win! First question coming up..."
    )

    # Start the quiz questions
    await send_question(context, chat_id)

async def send_question(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    if chat_id not in active_quizzes:
        return

    quiz = active_quizzes[chat_id]
    q_idx = quiz["current_q"]
    question = quiz["questions"][q_idx]

    keyboard = []
    for i, option in enumerate(question["options"]):
        keyboard.append([InlineKeyboardButton(option, callback_data=f"ans_{q_idx}_{i}")])

    # Fixed message format
    total_questions = len(quiz['questions'])
    question_text = question['text']
    message = f"❓ *Question {q_idx+1}/{total_questions}*\n{question_text}"

    msg = await context.bot.send_message(
        chat_id,
        message,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Store message ID
    quiz["message_ids"].append(msg.message_id)

    # Schedule answer reveal after 20 seconds using asyncio
    asyncio.create_task(
        reveal_answer_after_delay(chat_id, q_idx, msg.message_id, context)
    )

async def reveal_answer_after_delay(chat_id: int, q_idx: int, msg_id: int, context: ContextTypes.DEFAULT_TYPE):
    # Wait for 20 seconds before revealing answer
    await asyncio.sleep(20)
    await reveal_answer(chat_id, q_idx, msg_id, context)

async def reveal_answer(chat_id: int, q_idx: int, msg_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        if chat_id not in active_quizzes:
            logger.warning(f"reveal_answer: Quiz not active for chat {chat_id}")
            return

        quiz = active_quizzes[chat_id]
        if quiz["current_q"] != q_idx:
            logger.warning(f"reveal_answer: Question index mismatch. Current: {quiz['current_q']}, Expected: {q_idx} in chat {chat_id}")
            return

        question = quiz["questions"][q_idx]
        correct_idx = question["answer"]

        # Edit message to show correct answer
        try:
            await context.bot.edit_message_text(
                f"✅ *Correct Answer:* {question['options'][correct_idx]}",
                chat_id=chat_id,
                message_id=msg_id,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error editing message: {e}")

        # Move to next question or end
        if q_idx + 1 < len(quiz["questions"]):
            # Clear answered state for next question
            quiz["answered"] = {}

            # Show temporary "next question" message
            try:
                next_msg = await context.bot.send_message(
                    chat_id,
                    "⏳ Next question in 3 seconds...",
                    reply_to_message_id=msg_id
                )
            except Exception as e:
                logger.error(f"Error sending next question message: {e}")
                next_msg = None

            # Wait for 3 seconds
            await asyncio.sleep(3)

            # Delete the temporary message if sent
            if next_msg:
                try:
                    await context.bot.delete_message(chat_id, next_msg.message_id)
                except Exception as e:
                    logger.error(f"Error deleting temporary message: {e}")

            # Move to next question
            quiz["current_q"] = q_idx + 1
            await send_question(context, chat_id)
        else:
            await show_leaderboard(context, chat_id)
    except Exception as e:
        logger.error(f"Unexpected error in reveal_answer: {e}", exc_info=True)

async def answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    data_parts = query.data.split('_')

    if len(data_parts) != 3:
        await query.answer("Invalid option!")
        return

    _, q_idx, selected_idx = data_parts
    q_idx = int(q_idx)
    selected_idx = int(selected_idx)

    if chat_id not in active_quizzes:
        await query.answer("❌ Quiz not active!")
        return

    quiz = active_quizzes[chat_id]

    # Check if already answered
    if user_id in quiz["answered"].get(q_idx, set()):
        await query.answer("⚠️ Already answered!", show_alert=False)
        return

    # Record answer
    if q_idx not in quiz["answered"]:
        quiz["answered"][q_idx] = set()
    quiz["answered"][q_idx].add(user_id)

    # Update scores and user stats
    question = quiz["questions"][q_idx]
    is_correct = selected_idx == question["answer"]
    
    # Update user stats
    if user_id not in user_stats:
        user_stats[user_id] = {
            "games_played": 0,
            "questions_answered": 0,
            "correct_answers": 0,
            "quizzes_created": 0,
            "best_streak": 0,
            "current_streak": 0,
            "achievements": [],
            "total_score": 0,
            "rank": "🥉 Bronze"
        }
    
    user_stats[user_id]["questions_answered"] += 1
    
    if is_correct:
        quiz["scores"][user_id] = quiz["scores"].get(user_id, 0) + 1
        user_stats[user_id]["correct_answers"] += 1
        user_stats[user_id]["total_score"] += 1
        
        # Update streak
        if user_id not in streak_data:
            streak_data[user_id] = {"current": 0, "best": 0, "last_date": None}
        
        streak_data[user_id]["current"] += 1
        if streak_data[user_id]["current"] > streak_data[user_id]["best"]:
            streak_data[user_id]["best"] = streak_data[user_id]["current"]
            user_stats[user_id]["best_streak"] = streak_data[user_id]["best"]
        
        # Check for achievements
        streak = streak_data[user_id]["current"]
        if streak == 5 and "🔥 5 Streak" not in user_stats[user_id]["achievements"]:
            user_stats[user_id]["achievements"].append("🔥 5 Streak")
        elif streak == 10 and "🌟 10 Streak Master" not in user_stats[user_id]["achievements"]:
            user_stats[user_id]["achievements"].append("🌟 10 Streak Master")
        elif streak == 25 and "💎 25 Streak Legend" not in user_stats[user_id]["achievements"]:
            user_stats[user_id]["achievements"].append("💎 25 Streak Legend")
    else:
        # Reset streak on wrong answer
        if user_id in streak_data:
            streak_data[user_id]["current"] = 0

    # Send private notification to user only
    try:
        feedback_msg = "✅ Correct!" if is_correct else "❌ Wrong!"
        await query.answer(feedback_msg, show_alert=False)
    except Exception as e:
        logger.error(f"Answer feedback error: {e}")

    # Send public notification (briefly visible)
    try:
        emoji = "✅" if is_correct else "❌"
        feedback = await context.bot.send_message(
            chat_id,
            f"{emoji} {query.from_user.first_name} answered!",
            reply_to_message_id=query.message.message_id
        )
        # Schedule deletion after 2 seconds
        await asyncio.sleep(2)
        await feedback.delete()
    except Exception as e:
        logger.error(f"Public notification error: {e}")

async def show_leaderboard(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    quiz = active_quizzes.pop(chat_id, None)
    if not quiz:
        return

    # Include all participants who answered at least once
    participants = set()
    for answered_set in quiz["answered"].values():
        participants |= answered_set

    # Prepare scores
    scores = []
    for user_id in participants:
        try:
            user = await context.bot.get_chat(user_id)
            name = user.first_name or user.username or f"User_{user_id}"
            scores.append((name, quiz["scores"].get(user_id, 0)))
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            continue

    # Sort descending
    scores.sort(key=lambda x: x[1], reverse=True)

    # Format leaderboard
    text = "🏆 *FINAL LEADERBOARD* 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]

    if not scores:
        text += "❌ No valid participants!\n"
    else:
        for i, (name, score) in enumerate(scores[:10]):
            medal = medals[i] if i < 3 else f"🏅 {i+1}."
            text += f"{medal} *{name}* - `{score} pts`\n"

    # Update stats for all players
    for user_id in participants:
        try:
            user = await context.bot.get_chat(user_id)
            user_name = user.first_name or user.username or f"User_{user_id}"
            user_score = quiz["scores"].get(user_id, 0)
            
            # Count correct answers
            correct_count = 0
            for q_idx in range(len(quiz["questions"])):
                if user_id in quiz["answered"].get(q_idx, set()) and quiz["scores"].get(user_id, 0) > 0:
                    correct_count += 1
            
            # Update database stats
            DatabaseService.update_user_stats(
                user_id,
                games_played_increment=1,
                questions_answered_increment=len(quiz["questions"]),
                correct_answers_increment=correct_count,
                total_score_increment=user_score,
                first_name=user_name
            )
            
            # Record game session
            DatabaseService.record_game_session(
                user_id=user_id,
                quiz_id=quiz["id"],
                chat_id=chat_id,
                score=user_score,
                total_questions=len(quiz["questions"]),
                correct_answers=correct_count,
                completion_time=None
            )
            
            # Update group stats if in group
            if chat_id < 0:  # Negative chat_id indicates group chat
                DatabaseService.update_group_user_stats(chat_id, user_id,
                    games_played_increment=1,
                    questions_answered_increment=len(quiz["questions"]),
                    correct_answers_increment=correct_count,
                    total_score_increment=user_score
                )
            
        except Exception as e:
            logger.error(f"Error updating stats for user {user_id}: {e}")
            continue

    # Add creator attribution
    try:
        creator_id = quiz.get("creator")
        if creator_id:
            creator = await context.bot.get_chat(creator_id)
            creator_name = creator.first_name or creator.username or "Unknown"
            text += f"\n_Created by {creator_name}_"
    except:
        pass

    # Add signature
    text += "\n\n_My Lord - @tslm9_"

    await context.bot.send_message(
        chat_id,
        text,
        parse_mode="Markdown"
    )

# Enhanced feature handlers
async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle category selection during quiz creation"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id not in sessions or sessions[user_id]["stage"] != "category":
        await query.edit_message_text("❌ Invalid session state!")
        return
    
    category_map = {
        "cat_general": "📚 General Knowledge",
        "cat_science": "🧪 Science", 
        "cat_history": "🏛️ History",
        "cat_entertainment": "🎮 Entertainment",
        "cat_sports": "⚽ Sports",
        "cat_technology": "💻 Technology",
        "cat_geography": "🌍 Geography",
        "cat_culture": "🎨 Art & Culture",
        "cat_literature": "📖 Literature",
        "cat_custom": "🔤 Custom"
    }
    
    sessions[user_id]["category"] = category_map.get(query.data, "Custom")
    sessions[user_id]["stage"] = "difficulty"
    
    # Show difficulty selection
    keyboard = [
        [InlineKeyboardButton("🟢 Easy", callback_data="diff_easy")],
        [InlineKeyboardButton("🟡 Medium", callback_data="diff_medium")],
        [InlineKeyboardButton("🔴 Hard", callback_data="diff_hard")],
        [InlineKeyboardButton("🔥 Expert", callback_data="diff_expert")]
    ]
    
    await query.edit_message_text(
        f"✅ Category: {sessions[user_id]['category']}\n\n"
        "🎯 Now choose difficulty level:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def difficulty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle difficulty selection during quiz creation"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id not in sessions or sessions[user_id]["stage"] != "difficulty":
        await query.edit_message_text("❌ Invalid session state!")
        return
    
    difficulty_map = {
        "diff_easy": "🟢 Easy",
        "diff_medium": "🟡 Medium", 
        "diff_hard": "🔴 Hard",
        "diff_expert": "🔥 Expert"
    }
    
    sessions[user_id]["difficulty"] = difficulty_map.get(query.data, "Easy")
    sessions[user_id]["stage"] = "title"
    
    await query.edit_message_text(
        f"✅ Category: {sessions[user_id]['category']}\n"
        f"✅ Difficulty: {sessions[user_id]['difficulty']}\n\n"
        "🏷️ Send a title for your quiz:",
        parse_mode="Markdown"
    )

async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show personal user statistics"""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    
    stats = DatabaseService.get_user(user_id)
    if not stats:
        # Create new user
        DatabaseService.create_or_update_user(user_id, {
            "first_name": user_name,
            "games_played": 0,
            "questions_answered": 0,
            "correct_answers": 0,
            "quizzes_created": 0,
            "best_streak": 0,
            "current_streak": 0,
            "total_score": 0,
            "rank": "🥉 Bronze"
        })
        stats = DatabaseService.get_user(user_id) or {
            "games_played": 0,
            "questions_answered": 0,
            "correct_answers": 0,
            "quizzes_created": 0,
            "best_streak": 0,
            "current_streak": 0,
            "achievements": [],
            "total_score": 0,
            "rank": "🥉 Bronze"
        }
    accuracy = (stats["correct_answers"] / max(stats["questions_answered"], 1)) * 100
    
    # Determine rank based on score
    if stats["total_score"] >= 1000:
        rank = "💎 Diamond"
    elif stats["total_score"] >= 500:
        rank = "🥇 Gold"
    elif stats["total_score"] >= 200:
        rank = "🥈 Silver"
    else:
        rank = "🥉 Bronze"
    
    text = f"📊 *{user_name}'s Statistics*\n\n"
    text += f"🏆 Rank: {rank}\n"
    text += f"⭐ Total Score: {stats['total_score']}\n"
    text += f"🎮 Games Played: {stats['games_played']}\n"
    text += f"❓ Questions Answered: {stats['questions_answered']}\n"
    text += f"✅ Correct Answers: {stats['correct_answers']}\n"
    text += f"📈 Accuracy: {accuracy:.1f}%\n"
    text += f"🔥 Best Streak: {stats['best_streak']}\n"
    text += f"📝 Quizzes Created: {stats['quizzes_created']}\n"
    
    if stats.get("achievements") and len(stats["achievements"]) > 0:
        text += f"\n🏅 Achievements:\n"
        for achievement in stats["achievements"]:
            text += f"• {achievement}\n"
    else:
        text += f"\n🏅 Achievements: None yet\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def topplayers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top players leaderboard"""
    players = DatabaseService.get_top_players(10)
    
    if not players:
        await update.message.reply_text("📊 No players ranked yet!")
        return
    
    text = "🏆 *Top Players Leaderboard*\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, player in enumerate(players):
        try:
            if not player.get("first_name"):
                user = await context.bot.get_chat(player["id"])
                name = user.first_name or user.username or f"Player {i+1}"
            else:
                name = player["first_name"]
            
            medal = medals[i] if i < len(medals) else f"{i+1}."
            text += f"{medal} *{name}* - {player['total_score']} pts\n"
        except:
            continue
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def quickquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a quick random quiz"""
    if update.message.chat.type == "private":
        await update.message.reply_text("❌ Quick quiz only available in group chats!")
        return
    
    # Create a random quiz
    sample_questions = [
        {"text": "What is the capital of France?", "options": ["London", "Paris", "Berlin", "Madrid"], "answer": 1},
        {"text": "Which planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "answer": 1},
        {"text": "What is 15 + 27?", "options": ["41", "42", "43", "44"], "answer": 1},
        {"text": "Who painted the Mona Lisa?", "options": ["Van Gogh", "Picasso", "Da Vinci", "Monet"], "answer": 2},
        {"text": "What is the largest ocean?", "options": ["Atlantic", "Indian", "Arctic", "Pacific"], "answer": 3}
    ]
    
    # Select 3 random questions
    quiz_questions = random.sample(sample_questions, min(3, len(sample_questions)))
    
    chat_id = update.message.chat.id
    quiz_id = generate_quiz_id()
    
    # Show ready button
    keyboard = [[InlineKeyboardButton("✅ I'm Ready", callback_data=f"ready_{quiz_id}")]]
    
    msg = await update.message.reply_text(
        "⚡ *Quick Quiz!* ⚡\n"
        f"🧠 Random questions ({len(quiz_questions)} questions)\n"
        f"Click the button when you're ready.\n"
        f"At least 2 players needed to start.\n\n"
        f"👤 Players ready: 0",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    # Store waiting room
    waiting_rooms[chat_id] = {
        "quiz_id": quiz_id,
        "ready_users": set(),
        "scores": {},
        "answered": {},
        "questions": quiz_questions,
        "creator": update.message.from_user.id,
        "message_id": msg.message_id,
        "category": "⚡ Quick Quiz",
        "difficulty": "🟡 Random"
    }

async def streak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's current streak"""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    
    streak = DatabaseService.get_streak_data(user_id)
    
    text = f"🔥 *{user_name}'s Streak*\n\n"
    text += f"🔥 Current Streak: {streak['current']}\n"
    text += f"🏆 Best Streak: {streak['best']}\n"
    
    if streak['current'] >= 10:
        text += f"\n🎉 Amazing! You're on fire!"
    elif streak['current'] >= 5:
        text += f"\n⭐ Great job! Keep it up!"
    elif streak['current'] >= 1:
        text += f"\n👍 Good start! Keep going!"
    else:
        text += f"\n💪 Start your streak by answering correctly!"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def dailychallenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start daily challenge"""
    if update.message.chat.type == "private":
        await update.message.reply_text("❌ Daily challenge only available in group chats!")
        return
    
    # Check if already completed today
    today = datetime.now().strftime("%Y-%m-%d")
    user_id = update.message.from_user.id
    
    if user_id not in user_stats:
        user_stats[user_id] = {"daily_completed": []}
    
    if today in user_stats[user_id].get("daily_completed", []):
        await update.message.reply_text("✅ You've already completed today's challenge!")
        return
    
    # Create daily challenge questions
    daily_questions = [
        {"text": "Daily Challenge: What year did the Berlin Wall fall?", "options": ["1987", "1989", "1991", "1993"], "answer": 1},
        {"text": "Daily Challenge: Which element has the symbol 'Au'?", "options": ["Silver", "Gold", "Copper", "Aluminum"], "answer": 1},
        {"text": "Daily Challenge: What is the square root of 144?", "options": ["11", "12", "13", "14"], "answer": 1}
    ]
    
    chat_id = update.message.chat.id
    quiz_id = generate_quiz_id()
    
    keyboard = [[InlineKeyboardButton("🎯 Accept Challenge", callback_data=f"ready_{quiz_id}")]]
    
    msg = await update.message.reply_text(
        "🎯 *Daily Challenge!* 🎯\n"
        f"🏆 Special challenge for today\n"
        f"💎 Extra rewards for completion\n"
        f"Click to accept the challenge!\n\n"
        f"👤 Players ready: 0",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    waiting_rooms[chat_id] = {
        "quiz_id": quiz_id,
        "ready_users": set(),
        "scores": {},
        "answered": {},
        "questions": daily_questions,
        "creator": user_id,
        "message_id": msg.message_id,
        "category": "🎯 Daily Challenge",
        "difficulty": "🔥 Special",
        "is_daily": True
    }

async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available quiz categories"""
    category_counts = DatabaseService.get_quiz_categories()
    
    text = "📚 *Available Categories*\n\n"
    
    if category_counts:
        for category, count in sorted(category_counts.items()):
            text += f"{category}: {count} quizzes\n"
    else:
        text += "No quizzes available yet!\n"
    
    text += "\n💡 Use /createquiz to add new quizzes!"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics"""
    db_stats = DatabaseService.get_database_stats()
    active_sessions = len(sessions)
    active_waiting = len(waiting_rooms)
    active_games = len(active_quizzes)

    text = f"📊 *Bot Statistics*\n\n"
    text += f"📝 Total Quizzes: {db_stats.get('total_quizzes', 0)}\n"
    text += f"👥 Total Users: {db_stats.get('total_users', 0)}\n"
    text += f"🎮 Games Played: {db_stats.get('total_games', 0)}\n"
    text += f"❓ Total Questions: {db_stats.get('total_questions', 0)}\n"
    text += f"🏆 Available Achievements: {db_stats.get('total_achievements', 0)}\n"
    text += f"⏳ Active Sessions: {active_sessions}\n"
    text += f"🎯 Active Games: {active_games}\n"
    text += f"⏰ Waiting Rooms: {active_waiting}\n"

    await update.message.reply_text(text, parse_mode="Markdown")

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling update:", exc_info=context.error)

    # Handle Conflict error separately
    if isinstance(context.error, Conflict):
        logger.critical("Conflict error detected. Exiting to prevent multiple instances.")
        os._exit(1)

    # For other errors, safely handle callback
    try:
        if update is not None and hasattr(update, 'callback_query'):
            await update.callback_query.answer("⚠️ An error occurred. Please try again.")
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

def run_bot():
    """Run the bot in a separate thread"""
    try:
        # Create application with job queue enabled
        application = Application.builder().token(TOKEN).concurrent_updates(True).build()

        # Command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", start))
        application.add_handler(CommandHandler("createquiz", createquiz))
        application.add_handler(CommandHandler("cancel", cancel))
        application.add_handler(CommandHandler("done", done))
        application.add_handler(CommandHandler("stats", stats))
        application.add_handler(CommandHandler("mystats", mystats))
        application.add_handler(CommandHandler("topplayers", topplayers))
        application.add_handler(CommandHandler("quickquiz", quickquiz))
        application.add_handler(CommandHandler("dailychallenge", dailychallenge))
        application.add_handler(CommandHandler("categories", categories))
        application.add_handler(CommandHandler("streak", streak))
        application.add_handler(CommandHandler("broadcast", broadcast))
        application.add_handler(CommandHandler("grouptop", grouptop))
        application.add_handler(CommandHandler("animequiz", animequiz))

        # Message handlers
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_quiz_creation
        ))

        # Callback handlers
        application.add_handler(CallbackQueryHandler(ready_handler, pattern=r"^ready_"))
        application.add_handler(CallbackQueryHandler(answer_handler, pattern=r"^ans_"))
        application.add_handler(CallbackQueryHandler(category_handler, pattern=r"^cat_"))
        application.add_handler(CallbackQueryHandler(difficulty_handler, pattern=r"^diff_"))

        # Error handling
        application.add_error_handler(error_handler)

        # Schedule auto quiz every 15 minutes
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(start_auto_anime_quiz, interval=900, first=60)  # 900 seconds = 15 minutes

        # Start bot
        logger.info("Starting Telegram bot...")
        application.run_polling()
    except Exception as e:
        logger.error(f"Error running bot: {e}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all groups (owner only)"""
    user_id = update.message.from_user.id
    
    if user_id != BOT_OWNER_ID:
        await update.message.reply_text("❌ This command is only available to the bot owner.")
        return
    
    if not context.args:
        await update.message.reply_text("📢 Usage: /broadcast <message>")
        return
    
    message = " ".join(context.args)
    groups = DatabaseService.get_active_groups()
    
    if not groups:
        await update.message.reply_text("❌ No active groups found.")
        return
    
    # Create broadcast record
    broadcast_id = DatabaseService.create_broadcast_message(user_id, message)
    
    if not broadcast_id:
        await update.message.reply_text("❌ Failed to create broadcast record.")
        return
    
    await update.message.reply_text(f"📢 Starting broadcast to {len(groups)} groups...")
    
    sent_count = 0
    failed_count = 0
    
    for chat_id, title in groups:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📢 *Broadcast Message*\n\n{message}",
                parse_mode="Markdown"
            )
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"Failed to send broadcast to {chat_id}: {e}")
    
    # Update broadcast stats
    DatabaseService.update_broadcast_stats(broadcast_id, sent_count, failed_count, True)
    
    await update.message.reply_text(
        f"✅ Broadcast completed!\n"
        f"📤 Sent: {sent_count}\n"
        f"❌ Failed: {failed_count}"
    )

async def grouptop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top players in current group"""
    if update.message.chat.type == "private":
        await update.message.reply_text("❌ This command only works in group chats!")
        return
    
    chat_id = update.message.chat.id
    chat_title = update.message.chat.title or "This Group"
    
    # Add group to database if not exists
    DatabaseService.add_group_chat(chat_id, chat_title, update.message.chat.type)
    
    players = DatabaseService.get_group_top_players(chat_id, 10)
    
    if not players:
        await update.message.reply_text(f"🏆 No players ranked in {chat_title} yet!\nPlay some quizzes to appear on the leaderboard!")
        return
    
    text = f"🏆 *{chat_title} Top Players*\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, player in enumerate(players):
        name = player.get("first_name", "Unknown")
        if player.get("username"):
            name = f"@{player['username']}"
        
        medal = medals[i] if i < len(medals) else f"{i+1}."
        text += f"{medal} *{name}* - {player['total_score']} pts\n"
        text += f"    📊 {player['correct_answers']}/{player['questions_answered']} correct\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def animequiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start an anime/manhwa quiz"""
    if update.message.chat.type == "private":
        await update.message.reply_text("❌ Anime quiz only available in group chats!")
        return
    
    chat_id = update.message.chat.id
    chat_title = update.message.chat.title or "This Group"
    
    # Add group to database
    DatabaseService.add_group_chat(chat_id, chat_title, update.message.chat.type)
    
    # Create anime quiz
    questions = get_random_anime_questions(5)
    quiz_id = generate_quiz_id()
    
    # Show ready button
    keyboard = [[InlineKeyboardButton("🎌 I'm Ready!", callback_data=f"ready_{quiz_id}")]]
    
    msg = await update.message.reply_text(
        "🎌 *Anime & Manhwa Quiz!* 🎌\n"
        f"📚 {len(questions)} questions about anime/manhwa\n"
        f"Click the button when you're ready.\n"
        f"At least 2 players needed to start.\n\n"
        f"👤 Players ready: 0",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    # Store waiting room
    waiting_rooms[chat_id] = {
        "quiz_id": quiz_id,
        "ready_users": set(),
        "scores": {},
        "answered": {},
        "questions": questions,
        "creator": update.message.from_user.id,
        "message_id": msg.message_id,
    }

async def start_auto_anime_quiz(context: ContextTypes.DEFAULT_TYPE):
    """Start automatic anime quiz in groups"""
    groups = DatabaseService.get_active_groups()
    
    for chat_id, title in groups:
        try:
            if DatabaseService.should_auto_quiz(chat_id):
                # Create random anime quiz
                questions = get_random_anime_questions(3)
                quiz_id = generate_quiz_id()
                
                # Show ready button
                keyboard = [[InlineKeyboardButton("🎌 Ready for Anime Quiz!", callback_data=f"ready_{quiz_id}")]]
                
                msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎌 *Auto Anime Quiz!* 🎌\n"
                         "📚 Test your anime/manhwa knowledge!\n"
                         "🎯 3 questions about popular series\n"
                         "Click the button when you're ready.\n\n"
                         "👤 Players ready: 0",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                
                # Store waiting room
                waiting_rooms[chat_id] = {
                    "quiz_id": quiz_id,
                    "ready_users": set(),
                    "scores": {},
                    "answered": {},
                    "questions": questions,
                    "creator": "auto_quiz",
                    "message_id": msg.message_id,
                    "auto_quiz": True
                }
                
                DatabaseService.update_last_auto_quiz(chat_id)
                
        except Exception as e:
            logger.error(f"Error sending auto quiz to {chat_id}: {e}")

def main():
    # Start keep-alive server in background
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    
    # Run the bot in main thread
    run_bot()

if __name__ == "__main__":
    main()