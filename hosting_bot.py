import logging
import sqlite3
import os
import zipfile
import subprocess
import asyncio
import shutil
import html
import json
import psutil
import sys
import pandas as pd
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters
)

# ================= CONFIGURATION =================
BOT_TOKEN = "8837969854:AAEMfAFywOS9xWAQWKA-EyFWSOBiEe9aWl0"
ADMIN_ID = 6806376826  # ID bhittik Admin
ADMIN_USERNAME = "JohnRipper1337"  # Username bhittik Admin
HOSTED_BOTS_DIR = "hosted_bots"
PRODUCT_STORAGE_DIR = "products_data"
PRODUCTS_DB_FILE = "products.json"

# Payment Details Configuration
BKASH_NUMBER = "01757373094"
NAGAD_NUMBER = "01757373094"
ROCKET_NUMBER = "01757373094"
BINANCE_USDT_BEP20 = "Contact With Admin"

os.makedirs(HOSTED_BOTS_DIR, exist_ok=True)
os.makedirs(PRODUCT_STORAGE_DIR, exist_ok=True)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Global trackers
running_processes = {}
running_tasks = {}

# ================= PRODUCT JSON FUNCTIONS =================
def load_products():
    if os.path.exists(PRODUCTS_DB_FILE):
        try:
            with open(PRODUCTS_DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_products(data):
    with open(PRODUCTS_DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ================= DATABASE SETUP & AUTO MIGRATION =================
def init_db():
    conn = sqlite3.connect("hosting_bot.db")
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            join_date TEXT,
            has_claimed_trial INTEGER DEFAULT 0,
            is_locked INTEGER DEFAULT 0,
            balance REAL DEFAULT 0
        )
    ''')
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'first_name' not in columns:
        try: cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT DEFAULT 'User'")
        except Exception as e: logging.error(f"Migration error: {e}")
    if 'balance' not in columns:
        try: cursor.execute("ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0")
        except Exception as e: logging.error(f"Migration error: {e}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hosted_bots (
            bot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bot_name TEXT,
            expiry_date TEXT,
            status TEXT DEFAULT 'STOPPED'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            months INTEGER,
            amount REAL,
            trx_id TEXT DEFAULT '',
            status TEXT,
            prod_id TEXT DEFAULT '',
            quantity INTEGER DEFAULT 1
        )
    ''')

    cursor.execute("PRAGMA table_info(orders)")
    order_cols = [col[1] for col in cursor.fetchall()]
    if 'prod_id' not in order_cols:
        try: cursor.execute("ALTER TABLE orders ADD COLUMN prod_id TEXT DEFAULT ''")
        except Exception as e: logging.error(f"Migration error prod_id: {e}")
    if 'quantity' not in order_cols:
        try: cursor.execute("ALTER TABLE orders ADD COLUMN quantity INTEGER DEFAULT 1")
        except Exception as e: logging.error(f"Migration error quantity: {e}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deposit_requests (
            deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            trx_id TEXT,
            status TEXT DEFAULT 'PENDING'
        )
    ''')

    conn.commit()
    conn.close()

# ================= HELPER FUNCTIONS =================
def is_admin(user):
    if not user: return False
    if user.id == ADMIN_ID: return True
    if user.username and user.username.strip('@').lower() == ADMIN_USERNAME.lower(): return True
    return False

def get_user(user_id):
    conn = sqlite3.connect("hosting_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, join_date, has_claimed_trial, is_locked, balance, first_name FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def update_user_balance(user_id, amount):
    conn = sqlite3.connect("hosting_bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def register_user(user_id, username, first_name="User"):
    conn = sqlite3.connect("hosting_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    is_new = cursor.fetchone() is None

    now = datetime.now().strftime("%Y-%m-%d")
    clean_username = username if username else "Unknown"
    clean_first_name = first_name if first_name else "User"
    
    cursor.execute('''
        INSERT INTO users (user_id, username, first_name, join_date, has_claimed_trial, is_locked, balance)
        VALUES (?, ?, ?, ?, 0, 0, 0)
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, first_name = excluded.first_name
    ''', (user_id, clean_username, clean_first_name, now))
    conn.commit()
    
    cursor.execute("SELECT has_claimed_trial FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    claimed = res[0] if res else 0

    if claimed == 0:
        exp_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        cursor.execute("INSERT INTO hosted_bots (user_id, bot_name, expiry_date, status) VALUES (?, ?, ?, 'ACTIVE')",
                       (user_id, "Free_Trial_Bot_Slot", exp_date))
        cursor.execute("UPDATE users SET has_claimed_trial = 1 WHERE user_id = ?", (user_id,))
        conn.commit()

    conn.close()
    return is_new

def get_user_bots(user_id):
    conn = sqlite3.connect("hosting_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT bot_id, bot_name, expiry_date, status FROM hosted_bots WHERE user_id = ?", (user_id,))
    bots = cursor.fetchall()
    conn.close()
    return bots

# ================= VIRTUAL ENV & EXECUTOR HELPERS =================
def setup_virtualenv(bot_dir):
    """Proti slot-er jonno alada isolated Virtual Environment create kore."""
    venv_dir = os.path.join(bot_dir, "venv")
    if not os.path.exists(venv_dir):
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
    
    if os.name == "nt":
        python_bin = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_bin = os.path.join(venv_dir, "Scripts", "pip.exe")
    else:
        python_bin = os.path.join(venv_dir, "bin", "python")
        pip_bin = os.path.join(venv_dir, "bin", "pip")
        
    return python_bin, pip_bin

async def log_streamer(bot_id, chat_id, process, context):
    """Terminal-er live log collect kore Telegram update kora."""
    log_buffer = ["🚀 **[Live Logs Initialized]**\n"]
    msg = await context.bot.send_message(chat_id=chat_id, text=log_buffer[0], parse_mode="HTML")
    last_update = datetime.now()

    try:
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text_line = line.decode('utf-8', errors='ignore')
            log_buffer.append(html.escape(text_line))
            
            # Message length control & throttling updates
            if len("\n".join(log_buffer)) > 3500:
                log_buffer = log_buffer[-50:] # Keep recent 50 lines
                
            if (datetime.now() - last_update).total_seconds() > 2:
                formatted_logs = "💻 <b>Live Terminal Output (Slot #" + str(bot_id) + "):</b>\n<pre>" + "".join(log_buffer[-30:]) + "</pre>"
                try:
                    await msg.edit_text(formatted_logs, parse_mode="HTML")
                except Exception:
                    pass
                last_update = datetime.now()
                
    except asyncio.CancelledError:
        pass
    finally:
        return_code = await process.wait()
        final_msg = f"\n🛑 <b>Process Exited with Code:</b> {return_code}"
        log_buffer.append(final_msg)
        formatted_logs = "💻 <b>Final Terminal Output (Slot #" + str(bot_id) + "):</b>\n<pre>" + "".join(log_buffer[-35:]) + "</pre>"
        try:
            await msg.edit_text(formatted_logs, parse_mode="HTML")
        except Exception:
            pass

# ================= BOTTOM PERSISTENT KEYBOARD =================
def get_bottom_reply_keyboard(user):
    keyboard = [
        [KeyboardButton("🛒 Products Store"), KeyboardButton("💳 Deposit Fund")],
        [KeyboardButton("📊 Dashboard"), KeyboardButton("➕ Buy Slot")],
        [KeyboardButton("🎛️ C2 Panel"), KeyboardButton("📁 File Manager")],
        [KeyboardButton("📥 Excel Report"), KeyboardButton("🌐 Support")]
    ]
    if is_admin(user):
        keyboard.insert(0, [KeyboardButton("👑 Admin Panel")])
        
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def back_to_main_button():
    return InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")

# ================= BOT COMMANDS SETUP =================
async def post_init(application):
    commands = [
        BotCommand("start", "🚀 Start / Main Menu"),
        BotCommand("products", "🛒 Products Store"),
        BotCommand("deposit", "💳 Deposit Points/Funds"),
        BotCommand("dashboard", "📊 Account Dashboard"),
        BotCommand("c2panel", "🎛️ C2 Bot Control Panel"),
        BotCommand("files", "📁 File Manager"),
        BotCommand("pricing", "💵 Buy Hosting Slots"),
        BotCommand("help", "🌐 Support & Help Desk")
    ]
    await application.bot.set_my_commands(commands)

# ================= START COMMAND =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = register_user(user.id, user.username or "Unknown", user.first_name)
    
    if is_new:
        notification = (
            "🚨 <b>[C2 Alert] New User Started System!</b>\n\n"
            f"👤 <b>Name:</b> {html.escape(user.first_name)}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"🔗 <b>Username:</b> @{user.username if user.username else 'N/A'}"
        )
        try: await context.bot.send_message(chat_id=ADMIN_ID, text=notification, parse_mode="HTML")
        except Exception as e: logging.error(f"Failed to send admin notification: {e}")

    db_user = get_user(user.id)
    if db_user and db_user[4] == 1 and not is_admin(user):
        await update.message.reply_text("🔒 <b>Your account has been locked.</b>\nPlease contact system administration.", parse_mode="HTML")
        return

    admin_status = "👑 <b>[ADMIN MODE ACTIVE]</b>" if is_admin(user) else ""

    welcome_msg = (
        f"👋 <b>Welcome, {html.escape(user.first_name)}!</b> {admin_status}\n\n"
        f"🤖 <b>Global Multi-Bot Automation & C2 Hosting Platform</b>\n\n"
        f"💰 <b>Your Balance:</b> <code>{db_user[5]} Points</code>\n"
        f"🎁 <b>Bonus Unlocked:</b> You have received a <b>1-Month Free Bot Hosting Slot</b>!\n\n"
        f"👇 <b>নিচের ফিক্সড মেনু বাটন থেকে আপনার প্রয়োজনীয় অপশন বেছে নিন:</b>"
    )
    await update.message.reply_text(welcome_msg, reply_markup=get_bottom_reply_keyboard(user), parse_mode="HTML")

# ================= FILE MANAGER RENDER HELPER =================
def build_file_manager_keyboard(user_id, bot_id, subpath=""):
    bot_dir = os.path.abspath(os.path.join(HOSTED_BOTS_DIR, str(user_id), str(bot_id)))
    target_dir = os.path.abspath(os.path.join(bot_dir, subpath))

    if not target_dir.startswith(bot_dir) or not os.path.exists(target_dir):
        target_dir = bot_dir
        subpath = ""

    buttons = []
    
    if subpath:
        parent_sub = os.path.dirname(subpath)
        safe_parent = parent_sub.replace("/", "|") if parent_sub else "root"
        buttons.append([InlineKeyboardButton("⬆️ Up Folder", callback_data=f"fm_nav_{bot_id}_{safe_parent}")])

    try:
        items = sorted(os.listdir(target_dir))
        for item in items[:15]:
            if item == "venv": continue # Ignore virtual environment folder display
            item_path = os.path.join(target_dir, item)
            rel_path = os.path.relpath(item_path, bot_dir).replace("\\", "/").replace("/", "|")
            if os.path.isdir(item_path):
                buttons.append([InlineKeyboardButton(f"📁 {item}", callback_data=f"fm_nav_{bot_id}_{rel_path}")])
            else:
                buttons.append([InlineKeyboardButton(f"📄 {item}", callback_data=f"fm_file_{bot_id}_{rel_path}")])
    except Exception as e:
        logging.error(f"FM Error: {e}")

    buttons.append([InlineKeyboardButton("📤 Upload ZIP Script", callback_data=f"upload_script_{bot_id}")])
    buttons.append([back_to_main_button()])
    return InlineKeyboardMarkup(buttons), subpath

# ================= CALLBACK QUERY HANDLER =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    db_user = get_user(user_id)

    if db_user and db_user[4] == 1 and not is_admin(user):
        await query.edit_message_text("🔒 <b>Account Locked. Operations restricted.</b>", parse_mode="HTML")
        return

    data = query.data

    if data == "noop":
        return

    elif data == "back_main":
        context.user_data.clear()
        db_user = get_user(user_id)
        welcome_msg = (
            f"👋 <b>Welcome back, {html.escape(user.first_name)}!</b>\n\n"
            f"💰 <b>Your Balance:</b> <code>{db_user[5]} Points</code>\n"
            f"নিচের মেনু বাটন ব্যবহার করুন:"
        )
        await query.edit_message_text(welcome_msg, parse_mode="HTML")
        return

    # --- BUY HOSTING SUBSCRIPTION SLOTS ---
    elif data.startswith("buy_"):
        months_map = {"buy_1": (1, 30), "buy_2": (2, 60), "buy_3": (3, 80)}
        if data in months_map:
            months, price = months_map[data]
            context.user_data['slot_months'] = months
            context.user_data['slot_price'] = price
            context.user_data['action'] = "AWAITING_SLOT_PAYMENT_TRX"

            payment_msg = (
                f"🛒 <b>Hosting Subscription Order</b>\n"
                f"⏱️ <b>Duration:</b> {months} Month(s)\n"
                f"💰 <b>Price:</b> {price} BDT\n\n"
                f"Please pay <b>{price} BDT</b> using any of the payment methods:\n"
                f"📱 <b>Bkash:</b> <code>{BKASH_NUMBER}</code>\n"
                f"📱 <b>Nagad:</b> <code>{NAGAD_NUMBER}</code>\n"
                f"📱 <b>Rocket:</b> <code>{ROCKET_NUMBER}</code>\n"
                f"🌐 <b>Binance (BEP20):</b> <code>{BINANCE_USDT_BEP20}</code>\n\n"
                f"👉 **Payment সম্পন্ন করে আপনার Transaction ID (TrxID) নিচে পাঠাও:**"
            )
            await query.edit_message_text(payment_msg, reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]), parse_mode="HTML")

    # --- FILE MANAGER NAVIGATION & EDITING ---
    elif data.startswith("fm_explore_") or data.startswith("fm_nav_"):
        parts = data.split("_")
        bot_id = parts[2]
        raw_path = parts[3] if len(parts) > 3 else "root"
        subpath = "" if raw_path == "root" else raw_path.replace("|", "/")

        reply_markup, cur_sub = build_file_manager_keyboard(user_id, bot_id, subpath)
        path_str = f"/{cur_sub}" if cur_sub else "/"
        await query.edit_message_text(
            f"📁 <b>File Manager (Slot #{bot_id})</b>\n"
            f"📂 <b>Path:</b> <code>{path_str}</code>\n\n"
            f"নিচে আপনার ফাইল এবং ডিরেক্টরি দেখানো হলো:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    elif data.startswith("fm_file_"):
        parts = data.split("_")
        bot_id = parts[2]
        rel_path = parts[3].replace("|", "/")
        bot_dir = os.path.abspath(os.path.join(HOSTED_BOTS_DIR, str(user_id), str(bot_id)))
        file_path = os.path.abspath(os.path.join(bot_dir, rel_path))

        if os.path.exists(file_path) and os.path.isfile(file_path):
            file_name = os.path.basename(file_path)
            size = os.path.getsize(file_path)
            
            content_preview = ""
            if size < 50000: # Files smaller than 50KB can be edited directly
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content_preview = f.read()
                except Exception:
                    content_preview = "[Binary/Unreadable Content]"
            else:
                content_preview = "[File too large to edit directly in Telegram]"

            btns = [
                [InlineKeyboardButton("✏️ Edit File", callback_data=f"edit_file_{bot_id}_{parts[3]}")],
                [InlineKeyboardButton("🔙 Back to Explorer", callback_data=f"fm_explore_{bot_id}_root")]
            ]

            msg = (
                f"📄 <b>File Info:</b> <code>{file_name}</code>\n"
                f"📊 <b>Size:</b> {size} Bytes\n\n"
                f"<b>Content Preview:</b>\n<pre>{html.escape(content_preview[:1000])}</pre>"
            )
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

    elif data.startswith("edit_file_"):
        parts = data.split("_")
        bot_id = parts[2]
        rel_path = parts[3].replace("|", "/")
        
        context.user_data['action'] = "AWAITING_FILE_CONTENT_EDIT"
        context.user_data['edit_bot_id'] = bot_id
        context.user_data['edit_rel_path'] = rel_path

        await query.edit_message_text(
            f"✏️ <b>Edit File:</b> <code>{os.path.basename(rel_path)}</code>\n\n"
            f"Please send the complete updated text/code for this file as a message below:",
            reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]),
            parse_mode="HTML"
        )

    elif data.startswith("upload_script_"):
        bot_id = data.split("_")[2]
        context.user_data['target_bot_id'] = bot_id
        await query.edit_message_text(
            f"📤 <b>Upload Script for Slot #{bot_id}</b>\n\n"
            f"Please send your bot source code as a <b>ZIP file</b> in this chat.",
            reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]),
            parse_mode="HTML"
        )

    # --- C2 BOT PANEL CONTROLS ---
    elif data.startswith("manage_bot_"):
        bot_id = int(data.split("_")[2])
        bots = get_user_bots(user_id)
        bot_info = next((b for b in bots if b[0] == bot_id), None)

        if not bot_info:
            await query.edit_message_text("❌ Slot not found.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
            return

        is_running = bot_id in running_processes and running_processes[bot_id].returncode is None
        status_str = "🟢 RUNNING" if is_running else "🔴 STOPPED"

        controls = [
            [
                InlineKeyboardButton("▶️ Start (With Terminal)" if not is_running else "🔄 Restart", callback_data=f"start_bot_{bot_id}"),
                InlineKeyboardButton("⏹️ Stop", callback_data=f"stop_bot_{bot_id}")
            ],
            [InlineKeyboardButton("📁 File Manager", callback_data=f"fm_explore_{bot_id}_root")],
            [back_to_main_button()]
        ]
        await query.edit_message_text(
            f"🎛️ <b>Bot Control Panel</b>\n\n"
            f"🆔 <b>Slot ID:</b> #{bot_id}\n"
            f"📌 <b>Bot Name:</b> {bot_info[1]}\n"
            f"⏳ <b>Expiry:</b> {bot_info[2]}\n"
            f"⚡ <b>Status:</b> {status_str}",
            reply_markup=InlineKeyboardMarkup(controls),
            parse_mode="HTML"
        )

    elif data.startswith("start_bot_"):
        bot_id = int(data.split("_")[2])
        bot_dir = os.path.abspath(os.path.join(HOSTED_BOTS_DIR, str(user_id), str(bot_id)))
        
        main_py = os.path.join(bot_dir, "main.py")
        if not os.path.exists(main_py):
            py_files = [f for f in os.listdir(bot_dir) if f.endswith('.py')] if os.path.exists(bot_dir) else []
            if py_files: main_py = os.path.join(bot_dir, py_files[0])

        if not os.path.exists(main_py):
            await query.edit_message_text("❌ No Python script found. Upload a ZIP file first using File Manager.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
            return

        # Stop existing process if active
        if bot_id in running_processes and running_processes[bot_id].returncode is None:
            running_processes[bot_id].terminate()
            if bot_id in running_tasks:
                running_tasks[bot_id].cancel()

        status_msg = await query.edit_message_text("⚙️ <b>Preparing isolated environment & checking dependencies...</b>", parse_mode="HTML")

        # 1. Setup Isolated Python Venv for this Slot
        python_bin, pip_bin = setup_virtualenv(bot_dir)

        # 2. Check and Install Requirements if present
        req_file = os.path.join(bot_dir, "requirements.txt")
        if os.path.exists(req_file):
            await status_msg.edit_text("📦 <b>Auto-installing `requirements.txt` packages... Please wait!</b>", parse_mode="HTML")
            pip_proc = await asyncio.create_subprocess_exec(
                pip_bin, "install", "-r", req_file,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await pip_proc.communicate()

        await status_msg.edit_text(f"🚀 **Launching Slot #{bot_id} with Live Output...**", parse_mode="HTML")

        # 3. Launch Process & Stream Logs Live to Telegram Chat
        proc = await asyncio.create_subprocess_exec(
            python_bin, "-u", os.path.basename(main_py),
            cwd=bot_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        running_processes[bot_id] = proc
        
        # Start Live Terminal Task
        task = asyncio.create_task(log_streamer(bot_id, query.message.chat_id, proc, context))
        running_tasks[bot_id] = task

    elif data.startswith("stop_bot_"):
        bot_id = int(data.split("_")[2])
        if bot_id in running_processes and running_processes[bot_id].returncode is None:
            running_processes[bot_id].terminate()
            if bot_id in running_tasks:
                running_tasks[bot_id].cancel()
                del running_tasks[bot_id]
            del running_processes[bot_id]
            await query.edit_message_text(f"⏹️ Bot Slot #{bot_id} stopped successfully.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
        else:
            await query.edit_message_text("⚠️ Bot is not running.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))

    # --- ADMIN / OTHER SYSTEM HANDLERS ---
    elif data == "admin_stats" and is_admin(user):
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        
        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM hosted_bots")
        total_bots = cursor.fetchone()[0]
        conn.close()

        msg = (
            f"🖥️ <b>System Infrastructure Stats</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💻 <b>CPU Load:</b> {cpu}%\n"
            f"🧠 <b>RAM Usage:</b> {ram}%\n"
            f"💾 <b>Disk Usage:</b> {disk}%\n"
            f"👥 <b>Total Users:</b> {total_users}\n"
            f"🤖 <b>Hosted Bots:</b> {total_bots}\n"
            f"🟢 <b>Active Processes:</b> {len(running_processes)}"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]), parse_mode="HTML")

# ================= ZIP FILE UPLOAD & EXTRACTOR =================
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot_id = context.user_data.get('target_bot_id')

    if not bot_id:
        await update.message.reply_text("⚠️ Please select a slot from File Manager first before uploading script.")
        return

    doc = update.message.document
    if not doc.file_name.endswith(".zip"):
        await update.message.reply_text("❌ Only <b>ZIP files</b> are supported!", parse_mode="HTML")
        return

    bot_dir = os.path.abspath(os.path.join(HOSTED_BOTS_DIR, str(user.id), str(bot_id)))
    os.makedirs(bot_dir, exist_ok=True)
    
    zip_path = os.path.join(bot_dir, "script.zip")
    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(zip_path)

    # Extract ZIP
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(bot_dir)
        os.remove(zip_path)
        
        context.user_data.pop('target_bot_id', None)
        await update.message.reply_text(
            f"✅ <b>Source code uploaded & extracted successfully for Slot #{bot_id}!</b>\n"
            f"Go to <b>🎛️ C2 Panel</b> to launch your bot.",
            parse_mode="HTML",
            reply_markup=get_bottom_reply_keyboard(user)
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to extract ZIP file: {e}")

# ================= TEXT MESSAGE HANDLER =================
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip() if update.message.text else ""
    db_user = get_user(user_id)
    action = context.user_data.get('action')

    # 1. EDIT FILE SAVE LOGIC
    if action == "AWAITING_FILE_CONTENT_EDIT":
        bot_id = context.user_data.get('edit_bot_id')
        rel_path = context.user_data.get('edit_rel_path')
        bot_dir = os.path.abspath(os.path.join(HOSTED_BOTS_DIR, str(user_id), str(bot_id)))
        file_path = os.path.abspath(os.path.join(bot_dir, rel_path))

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
            
            context.user_data.pop('action', None)
            context.user_data.pop('edit_bot_id', None)
            context.user_data.pop('edit_rel_path', None)
            
            await update.message.reply_text(
                f"✅ <b>File <code>{os.path.basename(rel_path)}</code> saved successfully!</b>\n"
                f"You can now restart your bot from C2 Panel to apply changes.",
                parse_mode="HTML",
                reply_markup=get_bottom_reply_keyboard(user)
            )
            return
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to save file: {e}")
            return

    # 2. PERSISTENT NAVIGATION BUTTONS
    if text == "🎛️ C2 Panel":
        bots = get_user_bots(user_id)
        if not bots:
            await update.message.reply_text("❌ You don't have any active hosting slots.")
            return

        buttons = []
        for b in bots:
            st = "🟢 RUNNING" if b[0] in running_processes and running_processes[b[0]].returncode is None else "🔴 STOPPED"
            buttons.append([InlineKeyboardButton(f"🤖 Slot #{b[0]} - {b[1]} ({st})", callback_data=f"manage_bot_{b[0]}")])

        await update.message.reply_text("🎛️ <b>C2 Bot Control Panel</b>\nSelect a slot to manage:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    elif text == "📁 File Manager":
        bots = get_user_bots(user_id)
        if not bots:
            await update.message.reply_text("❌ You don't have any hosting slots to manage files.")
            return

        buttons = []
        for b in bots:
            buttons.append([InlineKeyboardButton(f"📁 Slot #{b[0]} Files", callback_data=f"fm_explore_{b[0]}_root")])

        await update.message.reply_text("📁 <b>Select a Slot File Manager:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    elif text == "📊 Dashboard":
        bots = get_user_bots(user_id)
        running_count = sum(1 for b in bots if b[0] in running_processes and running_processes[b[0]].returncode is None)
        msg = (
            f"📊 <b>Account Dashboard</b>\n"
            f"👤 <b>Name:</b> {html.escape(user.first_name)}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"💰 <b>Balance:</b> <code>{db_user[5]} Points</code>\n"
            f"🤖 <b>Total Slots:</b> {len(bots)}\n"
            f"⚡ <b>Active Running Bots:</b> {running_count}"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    elif text == "➕ Buy Slot":
        buttons = [
            [InlineKeyboardButton("🛒 1 Month - 30 BDT", callback_data="buy_1")],
            [InlineKeyboardButton("🛒 2 Months - 60 BDT", callback_data="buy_2")],
            [InlineKeyboardButton("🛒 3 Months - 80 BDT", callback_data="buy_3")]
        ]
        await update.message.reply_text("➕ <b>Choose Hosting Plan:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

# ================= MAIN APPLICATION LAUNCH =================
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    print("🚀 Host & C2 Bot System Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
