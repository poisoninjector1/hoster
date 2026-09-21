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
import pandas as pd
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters
)

# ================= CONFIGURATION =================
BOT_TOKEN = "8837969854:AAEMfAFywOS9xWAQWKA-EyFWSOBiEe9aWl0"
ADMIN_ID = 6806376826  # ID ভিত্তিক অ্যাডমিন
ADMIN_USERNAME = "JohnRipper1337"  # ইউজারনেম ভিত্তিক অ্যাডমিন
HOSTED_BOTS_DIR = "hosted_bots"
PRODUCT_STORAGE_DIR = "products_data"
PRODUCTS_DB_FILE = "products.json"

# Payment Details Configuration
print("Send Money Only")
BKASH_NUMBER = "01757373094"
NAGAD_NUMBER = "01757373094"
ROCKET_NUMBER = "01757373094"
BINANCE_USDT_BEP20 = "Contact With Admin"

os.makedirs(HOSTED_BOTS_DIR, exist_ok=True)
os.makedirs(PRODUCT_STORAGE_DIR, exist_ok=True)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

running_processes = {}

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

def run_script_for_bot(bot_dir, main_file):
    return subprocess.Popen(["python", main_file], cwd=bot_dir, stderr=subprocess.PIPE)

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
        for item in items[:15]:  # Safety limit
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

    # --- FILE MANAGER NAVIGATION ---
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
            await query.edit_message_text(
                f"📄 <b>File Info:</b> <code>{file_name}</code>\n"
                f"📊 <b>Size:</b> {size} Bytes",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Explorer", callback_data=f"fm_explore_{bot_id}_root")]
                ]),
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

        is_running = bot_id in running_processes and running_processes[bot_id].poll() is None
        status_str = "🟢 RUNNING" if is_running else "🔴 STOPPED"

        controls = [
            [
                InlineKeyboardButton("▶️ Start" if not is_running else "🔄 Restart", callback_data=f"start_bot_{bot_id}"),
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

        if bot_id in running_processes and running_processes[bot_id].poll() is None:
            running_processes[bot_id].terminate()

        proc = run_script_for_bot(bot_dir, os.path.basename(main_py))
        running_processes[bot_id] = proc

        await query.edit_message_text(f"🚀 Bot Slot #{bot_id} started successfully!", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))

    elif data.startswith("stop_bot_"):
        bot_id = int(data.split("_")[2])
        if bot_id in running_processes and running_processes[bot_id].poll() is None:
            running_processes[bot_id].terminate()
            del running_processes[bot_id]
            await query.edit_message_text(f"⏹️ Bot Slot #{bot_id} stopped.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
        else:
            await query.edit_message_text("⚠️ Bot is not running.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))

    # --- BUY PRODUCT ---
    elif data.startswith("buy_prod_"):
        prod_id = data.split("_")[2]
        products = load_products()
        
        if prod_id not in products:
            await query.edit_message_text("❌ Product not found!", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
            return

        product = products[prod_id]
        context.user_data['selected_prod_id'] = prod_id
        context.user_data['action'] = "AWAITING_PRODUCT_QUANTITY"

        await query.edit_message_text(
            f"📦 <b>{product['name']}</b>\n"
            f"💰 <b>Unit Price:</b> {product['price']} Points\n\n"
            f"👉 <b>আপনি কয়টি (Quantity) নিতে চান?</b>\n"
            f"নিচে পরিমাণটি লিখুন (যেমন: <code>1</code>, <code>2</code>, <code>5</code>):",
            reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]),
            parse_mode="HTML"
        )

    # --- ADMIN ACTIONS ---
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
            f"🖥️ <b>System & Server Infrastructure Stats</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💻 <b>CPU Load:</b> {cpu}%\n"
            f"🧠 <b>RAM Usage:</b> {ram}%\n"
            f"💾 <b>Disk Usage:</b> {disk}%\n"
            f"👥 <b>Total Users:</b> {total_users}\n"
            f"🤖 <b>Hosted Bots:</b> {total_bots}\n"
            f"🟢 <b>Active Processes:</b> {len(running_processes)}"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]), parse_mode="HTML")

    elif data == "admin_users" and is_admin(user):
        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, is_locked, balance FROM users LIMIT 20")
        users = cursor.fetchall()
        conn.close()

        msg = "👥 <b>User Management List (Top 20)</b>\n\n"
        for u in users:
            status = "🔴 Locked" if u[2] == 1 else "🟢 Active"
            msg += f"• ID: <code>{u[0]}</code> | @{u[1]} | {status} | Bal: {u[3]}\n"

        buttons = [
            [InlineKeyboardButton("🔒 Lock User", callback_data="admin_lock_usr"), InlineKeyboardButton("🔓 Unlock User", callback_data="admin_unlock_usr")],
            [back_to_main_button()]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    elif data == "admin_lock_usr" and is_admin(user):
        context.user_data['action'] = "AWAITING_LOCK_ID"
        await query.edit_message_text("🔒 Enter User ID to Lock:", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))

    elif data == "admin_unlock_usr" and is_admin(user):
        context.user_data['action'] = "AWAITING_UNLOCK_ID"
        await query.edit_message_text("🔓 Enter User ID to Unlock:", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))

    elif data == "admin_broadcast" and is_admin(user):
        context.user_data['action'] = "AWAITING_BROADCAST_MSG"
        await query.edit_message_text("📢 Send broadcast text message to all users:", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))

    elif data == "admin_active_bots" and is_admin(user):
        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT bot_id, user_id, bot_name FROM hosted_bots")
        bots = cursor.fetchall()
        conn.close()

        msg = "🤖 <b>All Hosting Bot Slots</b>\n\n"
        for b in bots:
            st = "🟢 RUNNING" if b[0] in running_processes and running_processes[b[0]].poll() is None else "🔴 STOPPED"
            msg += f"• Slot #{b[0]} | User: <code>{b[1]}</code> | {b[2]} | {st}\n"

        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]), parse_mode="HTML")

    elif data.startswith("approve_slot_"):
        if not is_admin(user): return
        _, _, ord_id, u_id, mths = data.split("_")
        u_id, mths = int(u_id), int(mths)

        exp_date = (datetime.now() + timedelta(days=30 * mths)).strftime("%Y-%m-%d")
        
        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO hosted_bots (user_id, bot_name, expiry_date, status) VALUES (?, ?, ?, 'ACTIVE')",
                       (u_id, f"Slot_{mths}Months", exp_date))
        cursor.execute("UPDATE orders SET status = 'APPROVED' WHERE order_id = ?", (ord_id,))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"✅ Subscription Order #{ord_id} Approved! New Slot created for User <code>{u_id}</code>.", parse_mode="HTML")
        try: await context.bot.send_message(chat_id=u_id, text=f"🎉 <b>Hosting Slot Approved!</b>\nYour new slot is now available in **C2 Panel** with validity until {exp_date}.", parse_mode="HTML")
        except Exception: pass

    elif data.startswith("reject_slot_"):
        if not is_admin(user): return
        _, _, ord_id, u_id = data.split("_")
        u_id = int(u_id)

        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = 'REJECTED' WHERE order_id = ?", (ord_id,))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"❌ Subscription Order #{ord_id} Rejected.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
        try: await context.bot.send_message(chat_id=u_id, text=f"❌ Your Hosting Slot Request #{ord_id} was rejected by Admin.", parse_mode="HTML")
        except Exception: pass

    elif data.startswith("deliver_prod_"):
        if not is_admin(user): return
        _, _, ord_id, u_id = data.split("_")
        context.user_data['action'] = "AWAITING_DELIVERY_DATA"
        context.user_data['target_order_id'] = int(ord_id)
        context.user_data['target_user_id'] = int(u_id)

        await query.edit_message_text(
            f"🎁 <b>Deliver Order #{ord_id} to User <code>{u_id}</code></b>\n\n"
            f"👉 <b>পণ্যের বিবরণ / আইডি পেস্ট করুন (যেমন Text):</b>\n"
            f"অথবা সরাসরি ফাইল পাঠাতে পারেন।",
            reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]),
            parse_mode="HTML"
        )

    elif data.startswith("reject_prod_"):
        if not is_admin(user): return
        _, _, ord_id, u_id, amt = data.split("_")
        u_id = int(u_id)
        amt = float(amt)

        update_user_balance(u_id, amt)

        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = 'REJECTED' WHERE order_id = ?", (ord_id,))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"❌ Product Order #{ord_id} Rejected and <code>{amt}</code> Points refunded.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]), parse_mode="HTML")
        try: await context.bot.send_message(chat_id=u_id, text=f"❌ Your Product Order #{ord_id} was rejected. <code>{amt}</code> Points refunded to your balance.", parse_mode="HTML")
        except Exception: pass

    elif data.startswith("approve_dep_"):
        if not is_admin(user): return
        _, _, dep_id, u_id, amt = data.split("_")
        u_id = int(u_id)
        amt = float(amt)

        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE deposit_requests SET status = 'APPROVED' WHERE deposit_id = ?", (dep_id,))
        conn.commit()
        conn.close()

        update_user_balance(u_id, amt)

        await query.edit_message_text(f"✅ Deposit Request #{dep_id} Approved! Added <code>{amt}</code> Points to User <code>{u_id}</code>.", parse_mode="HTML")
        try: await context.bot.send_message(chat_id=u_id, text=f"🎉 <b>Deposit Approved!</b>\n<code>{amt} Points</code> have been added to your balance.", parse_mode="HTML")
        except Exception: pass

    elif data.startswith("reject_dep_"):
        if not is_admin(user): return
        _, _, dep_id, u_id = data.split("_")
        u_id = int(u_id)

        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE deposit_requests SET status = 'REJECTED' WHERE deposit_id = ?", (dep_id,))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"❌ Deposit Request #{dep_id} Rejected.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
        try: await context.bot.send_message(chat_id=u_id, text=f"❌ Your Deposit Request #{dep_id} was rejected by Admin.", parse_mode="HTML")
        except Exception: pass

    elif data == "admin_add_product":
        if not is_admin(user): return
        context.user_data['action'] = "AWAITING_PRODUCT_DATA"
        await query.edit_message_text(
            "➕ <b>Add New Product</b>\n\nSend product details in format:\n<code>[ID]|[Name]|[Price]</code>\n\n<i>Example:</i> <code>p1|Netflix 1 Month|150</code>",
            reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]),
            parse_mode="HTML"
        )

    elif data == "admin_remove_product":
        if not is_admin(user): return
        products = load_products()
        if not products:
            await query.edit_message_text("❌ No products found.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]))
            return

        buttons = []
        for p_id, p_info in products.items():
            buttons.append([InlineKeyboardButton(f"❌ Delete {p_info['name']}", callback_data=f"del_prod_{p_id}")])
        buttons.append([back_to_main_button()])

        await query.edit_message_text("🗑️ <b>Select a product to delete:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    elif data.startswith("del_prod_"):
        if not is_admin(user): return
        p_id = data.split("_")[2]
        products = load_products()

        if p_id in products:
            del products[p_id]
            save_products(products)
            await query.edit_message_text(f"✅ Product <code>{p_id}</code> removed successfully.", reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]), parse_mode="HTML")

    elif data == "admin_edit_price":
        if not is_admin(user): return
        context.user_data['action'] = "AWAITING_PRICE_EDIT"
        await query.edit_message_text(
            "✏️ <b>Edit Product Price</b>\n\nSend Product ID and New Price in format:\n<code>[ID]|[NewPrice]</code>\n\n<i>Example:</i> <code>p1|200</code>",
            reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]),
            parse_mode="HTML"
        )

    elif data == "admin_fund_user":
        if not is_admin(user): return
        context.user_data['action'] = "AWAITING_USER_FUND"
        await query.edit_message_text(
            "💳 <b>Add/Manage User Points/Fund</b>\n\nSend User ID and Amount in format:\n<code>[UserID]|[Amount]</code>\n\n<i>Example:</i> <code>6806376826|500</code>",
            reply_markup=InlineKeyboardMarkup([[back_to_main_button()]]),
            parse_mode="HTML"
        )

# ================= TEXT & REPLY KEYBOARD LISTENER =================
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip() if update.message.text else ""
    db_user = get_user(user_id)
    action = context.user_data.get('action')

    # 1. FIXED BOTTOM MENU COMMANDS
    if text == "🛒 Products Store":
        products = load_products()
        if not products:
            await update.message.reply_text("🛒 <b>No products available at the moment.</b>", parse_mode="HTML")
            return

        buttons = []
        for p_id, p_info in products.items():
            buttons.append([InlineKeyboardButton(f"📦 {p_info['name']} - {p_info['price']} Points", callback_data=f"buy_prod_{p_id}")])

        await update.message.reply_text(
            f"🛒 <b>Available Products Store</b>\n"
            f"💰 <b>Your Balance:</b> <code>{db_user[5]} Points</code>\n\n"
            f"Select a product to order:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )
        return

    elif text == "💳 Deposit Fund":
        context.user_data['action'] = "AWAITING_DEPOSIT_AMOUNT"
        await update.message.reply_text("💳 <b>আপনি কত টাকা Add করতে চান?</b>\n\nপরিমাণটি নিচে লিখে জানান (যেমন: <code>100</code>):", parse_mode="HTML")
        return

    elif text == "📊 Dashboard":
        bots = get_user_bots(user_id)
        running_count = sum(1 for b in bots if b[0] in running_processes and running_processes[b[0]].poll() is None)
        msg = (
            f"📊 <b>Dashboard</b>\n"
            f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
            f"💰 <b>Balance:</b> <code>{db_user[5]} Points</code>\n"
            f"🤖 <b>Slots:</b> {len(bots)} | 🟢 <b>Running:</b> {running_count}"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    elif text == "➕ Buy Slot":
        keyboard = [
            [InlineKeyboardButton("💵 1 Month - 30 BDT", callback_data="buy_1"), InlineKeyboardButton("💵 2 Months - 60 BDT", callback_data="buy_2")],
            [InlineKeyboardButton("🔥 3 Months Offer - 80 BDT", callback_data="buy_3")]
        ]
        await update.message.reply_text("📋 <b>Hosting Subscription Plans:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    elif text == "🎛️ C2 Panel":
        bots = get_user_bots(user_id)
        if not bots:
            await update.message.reply_text("❌ No active bot slots found.", parse_mode="HTML")
            return

        keyboard = []
        for bot in bots:
            bot_id, name, exp, status = bot
            is_active = bot_id in running_processes and running_processes[bot_id].poll() is None
            keyboard.append([InlineKeyboardButton(f"🤖 {name} ({'🟢 RUNNING' if is_active else '🔴 STOPPED'})", callback_data=f"manage_bot_{bot_id}")])
        
        await update.message.reply_text("🎛️ <b>Select a bot to manage:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    elif text == "📁 File Manager":
        bots = get_user_bots(user_id)
        if not bots:
            await update.message.reply_text("❌ No slots found.", parse_mode="HTML")
            return

        keyboard = []
        for bot in bots:
            keyboard.append([InlineKeyboardButton(f"📁 Slot #{bot[0]} - {bot[1]}", callback_data=f"fm_explore_{bot[0]}_root")])
        await update.message.reply_text("📁 <b>File Manager Slot Selection:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    elif text == "📥 Excel Report":
        conn = sqlite3.connect("hosting_bot.db")
        df_bots = pd.read_sql_query("SELECT * FROM hosted_bots WHERE user_id = ?", conn, params=(user_id,))
        conn.close()
        
        excel_path = f"report_{user_id}.xlsx"
        df_bots.to_excel(excel_path, index=False)
        await context.bot.send_document(chat_id=user_id, document=open(excel_path, 'rb'), filename="Bot_Report.xlsx")
        os.remove(excel_path)
        return

    elif text == "🌐 Support":
        await update.message.reply_text(f"🌐 <b>Support Desk:</b>\nContact Admin: @{ADMIN_USERNAME}\nID: <code>{ADMIN_ID}</code>", parse_mode="HTML")
        return

    elif text == "👑 Admin Panel" and is_admin(user):
        admin_keyboard = [
            [InlineKeyboardButton("🖥️ Server Stats", callback_data="admin_stats"), InlineKeyboardButton("👥 User Management", callback_data="admin_users")],
            [InlineKeyboardButton("🤖 Active Bots List", callback_data="admin_active_bots"), InlineKeyboardButton("📢 Broadcast Msg", callback_data="admin_broadcast")],
            [InlineKeyboardButton("➕ Add Product", callback_data="admin_add_product"), InlineKeyboardButton("🗑️ Remove Product", callback_data="admin_remove_product")],
            [InlineKeyboardButton("✏️ Edit Price", callback_data="admin_edit_price"), InlineKeyboardButton("💳 Manage Fund", callback_data="admin_fund_user")]
        ]
        await update.message.reply_text("👑 <b>Admin Control Panel:</b>", reply_markup=InlineKeyboardMarkup(admin_keyboard), parse_mode="HTML")
        return

    # 2. STATE INPUT HANDLERS
    if action == "AWAITING_SLOT_PAYMENT_TRX":
        trx_id = text.strip()
        months = context.user_data.get('slot_months', 1)
        price = context.user_data.get('slot_price', 0)

        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO orders (user_id, months, amount, trx_id, status) VALUES (?, ?, ?, ?, 'PENDING')",
                       (user_id, months, price, trx_id))
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text("✅ Your Subscription Payment Request has been submitted! Admin will verify soon.")

        # Admin alert
        admin_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve Slot", callback_data=f"approve_slot_{order_id}_{user_id}_{months}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_slot_{order_id}_{user_id}")]
        ])
        admin_msg = (
            f"💳 <b>New Hosting Slot Request #{order_id}</b>\n\n"
            f"👤 User: {html.escape(user.first_name)} (<code>{user_id}</code>)\n"
            f"⏱️ Duration: <code>{months} Month(s)</code>\n"
            f"💰 Amount: <code>{price} BDT</code>\n"
            f"🧾 TrxID: <code>{trx_id}</code>"
        )
        try: await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=admin_markup, parse_mode="HTML")
        except Exception as e: logging.error(f"Failed slot order admin alert: {e}")
        return

    elif action == "AWAITING_PRODUCT_QUANTITY":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ Please enter a valid numeric quantity.")
            return

        qty = int(text)
        prod_id = context.user_data.get('selected_prod_id')
        products = load_products()
        
        if prod_id not in products:
            await update.message.reply_text("❌ Selected product no longer exists.")
            context.user_data.clear()
            return

        product = products[prod_id]
        total_cost = product['price'] * qty

        if db_user[5] < total_cost:
            await update.message.reply_text(
                f"❌ <b>Insufficient Balance!</b>\n\nTotal required: <code>{total_cost} Points</code>\nYour Balance: <code>{db_user[5]} Points</code>",
                parse_mode="HTML"
            )
            context.user_data.clear()
            return

        # Deduct user balance
        update_user_balance(user_id, -total_cost)

        # Record Order
        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO orders (user_id, months, amount, status, prod_id, quantity) VALUES (?, 0, ?, 'PENDING', ?, ?)",
                       (user_id, total_cost, prod_id, qty))
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ <b>Order Placed Successfully!</b>\n\n Order ID: #{order_id}\nProduct: {product['name']}\nQuantity: {qty}\nTotal Cost: {total_cost} Points\n\nYour order is sent to Admin for approval.",
            parse_mode="HTML"
        )

        # Alert Admin
        admin_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Deliver Product", callback_data=f"deliver_prod_{order_id}_{user_id}"),
             InlineKeyboardButton("❌ Reject & Refund", callback_data=f"reject_prod_{order_id}_{user_id}_{total_cost}")]
        ])
        admin_msg = (
            f"🛒 <b>New Product Order #{order_id}!</b>\n\n"
            f"👤 User: {html.escape(user.first_name)} (<code>{user_id}</code>)\n"
            f"📦 Product: {product['name']}\n"
            f"🔢 Quantity: {qty}\n"
            f"💰 Price Deducted: {total_cost} Points"
        )
        try: await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=admin_markup, parse_mode="HTML")
        except Exception as e: logging.error(f"Failed to alert admin: {e}")
        return

    elif action == "AWAITING_DEPOSIT_AMOUNT":
        try:
            amt = float(text)
            if amt <= 0: raise ValueError()
        except ValueError:
            await update.message.reply_text("❌ Enter a valid deposit amount.")
            return

        context.user_data['deposit_amount'] = amt
        context.user_data['action'] = "AWAITING_DEPOSIT_TRX"

        payment_msg = (
            f"💳 <b>Deposit Request: {amt} BDT/Points</b>\n\n"
            f"Please pay using any of the methods below:\n"
            f"📱 <b>Bkash:</b> <code>{BKASH_NUMBER}</code>\n"
            f"📱 <b>Nagad:</b> <code>{NAGAD_NUMBER}</code>\n"
            f"📱 <b>Rocket:</b> <code>{ROCKET_NUMBER}</code>\n"
            f"🌐 <b>Binance (BEP20):</b> <code>{BINANCE_USDT_BEP20}</code>\n\n"
            f"👉 Payment সম্পন্ন করে **Transaction ID (TrxID)** নিচে পাঠাও:"
        )
        await update.message.reply_text(payment_msg, parse_mode="HTML")
        return

    elif action == "AWAITING_DEPOSIT_TRX":
        trx_id = text.strip()
        amt = context.user_data.get('deposit_amount', 0)

        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO deposit_requests (user_id, amount, trx_id, status) VALUES (?, ?, ?, 'PENDING')", (user_id, amt, trx_id))
        dep_id = cursor.lastrowid
        conn.commit()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text("✅ Deposit request submitted to admin for verification.")

        # Admin alert
        admin_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_dep_{dep_id}_{user_id}_{amt}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_dep_{dep_id}_{user_id}")]
        ])
        admin_msg = (
            f"💳 <b>New Deposit Request #{dep_id}</b>\n\n"
            f"👤 User: {html.escape(user.first_name)} (<code>{user_id}</code>)\n"
            f"💰 Amount: <code>{amt} Points</code>\n"
            f"🧾 TrxID: <code>{trx_id}</code>"
        )
        try: await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=admin_markup, parse_mode="HTML")
        except Exception as e: logging.error(f"Failed deposit admin alert: {e}")
        return

    elif action == "AWAITING_DELIVERY_DATA" and is_admin(user):
        ord_id = context.user_data.get('target_order_id')
        target_u_id = context.user_data.get('target_user_id')

        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = 'COMPLETED' WHERE order_id = ?", (ord_id,))
        conn.commit()
        conn.close()

        try:
            await context.bot.send_message(
                chat_id=target_u_id,
                text=f"🎁 <b>Your Order #{ord_id} Delivered!</b>\n\n{text}",
                parse_mode="HTML"
            )
            await update.message.reply_text(f"✅ Order #{ord_id} marked complete and details delivered to User <code>{target_u_id}</code>.", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Failed to deliver message to user: {e}")

        context.user_data.clear()
        return

    elif action == "AWAITING_PRODUCT_DATA" and is_admin(user):
        parts = text.split("|")
        if len(parts) != 3:
            await update.message.reply_text("❌ Invalid format. Use: <code>[ID]|[Name]|[Price]</code>", parse_mode="HTML")
            return
        p_id, p_name, p_price = parts[0].strip(), parts[1].strip(), parts[2].strip()
        try:
            price_val = float(p_price)
        except ValueError:
            await update.message.reply_text("❌ Invalid price.")
            return

        products = load_products()
        products[p_id] = {"name": p_name, "price": price_val}
        save_products(products)

        context.user_data.clear()
        await update.message.reply_text(f"✅ Product <b>{p_name}</b> added with ID <code>{p_id}</code>!", parse_mode="HTML")
        return

    elif action == "AWAITING_PRICE_EDIT" and is_admin(user):
        parts = text.split("|")
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: <code>[ID]|[NewPrice]</code>", parse_mode="HTML")
            return
        p_id, p_price = parts[0].strip(), parts[1].strip()
        products = load_products()

        if p_id not in products:
            await update.message.reply_text("❌ Product ID not found.")
            return

        try:
            products[p_id]['price'] = float(p_price)
            save_products(products)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Price updated for <code>{p_id}</code>!", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Invalid price number.")
        return

    elif action == "AWAITING_USER_FUND" and is_admin(user):
        parts = text.split("|")
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: <code>[UserID]|[Amount]</code>", parse_mode="HTML")
            return
        try:
            target_id, amt = int(parts[0].strip()), float(parts[1].strip())
            update_user_balance(target_id, amt)
            context.user_data.clear()
            await update.message.reply_text(f"✅ Added <code>{amt}</code> Points to User <code>{target_id}</code>.", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID or Amount.")
        return

    elif action == "AWAITING_LOCK_ID" and is_admin(user):
        if text.isdigit():
            conn = sqlite3.connect("hosting_bot.db")
            conn.execute("UPDATE users SET is_locked = 1 WHERE user_id = ?", (int(text),))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(f"🔒 User <code>{text}</code> locked.", parse_mode="HTML")
        return

    elif action == "AWAITING_UNLOCK_ID" and is_admin(user):
        if text.isdigit():
            conn = sqlite3.connect("hosting_bot.db")
            conn.execute("UPDATE users SET is_locked = 0 WHERE user_id = ?", (int(text),))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(f"🔓 User <code>{text}</code> unlocked.", parse_mode="HTML")
        return

    elif action == "AWAITING_BROADCAST_MSG" and is_admin(user):
        conn = sqlite3.connect("hosting_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users_list = cursor.fetchall()
        conn.close()

        count = 0
        for u in users_list:
            try:
                await context.bot.send_message(chat_id=u[0], text=f"📢 <b>Announcement:</b>\n\n{text}", parse_mode="HTML")
                count += 1
            except Exception: pass

        context.user_data.clear()
        await update.message.reply_text(f"✅ Broadcast sent to {count} users.")
        return

# ================= DOCUMENT / ZIP UPLOAD HANDLER =================
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    target_bot_id = context.user_data.get('target_bot_id')

    if not target_bot_id:
        await update.message.reply_text("⚠️ Please select a bot slot in File Manager before uploading ZIP.")
        return

    doc = update.message.document
    if not doc.file_name.endswith('.zip'):
        await update.message.reply_text("❌ Only <b>ZIP</b> files are allowed!", parse_mode="HTML")
        return

    bot_dir = os.path.abspath(os.path.join(HOSTED_BOTS_DIR, str(user_id), str(target_bot_id)))
    os.makedirs(bot_dir, exist_ok=True)

    zip_file_path = os.path.join(bot_dir, "uploaded_code.zip")
    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(zip_file_path)

    try:
        with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
            zip_ref.extractall(bot_dir)
        os.remove(zip_file_path)
        context.user_data.pop('target_bot_id', None)
        await update.message.reply_text(f"✅ Script ZIP unzipped and updated for Slot #{target_bot_id}!")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to extract ZIP: {e}")

# ================= MAIN APPLICATION ENTRYPOINT =================
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
import asyncio
import logging
from aiohttp import web

# লগিং সেটআপ
logging.basicConfig(level=logging.INFO)

# রুট ইউআরএল হ্যান্ডলার
async def handle_ping(request):
    return web.Response(text="I am alive!", status=200)

# সার্ভার স্টার্ট করার ফাংশন
async def start_uptime_server(port=8080):
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/ping', handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render বা অন্যান্য প্ল্যাটফর্মের PORT এনভায়রনমেন্ট ভ্যারিয়েবল সাপোর্ট
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"⚡ Uptime Web Server running on port {port}")

# মূল টেলিগ্রাম বট কোডে যুক্ত করার নিয়ম:
# আপনার main.py ফাইলে asyncio event loop চলাকালীন নিচে এভাবে কল করতে পারেন:
# asyncio.create_task(start_uptime_server(8080))
    
    print("🤖 Bot is up and running...")
    app.run_polling()

if __name__ == "__main__":
    main()
