import os
import json
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Folder to store deliverable product files
PRODUCT_STORAGE_DIR = "products_data"
if not os.path.exists(PRODUCT_STORAGE_DIR):
    os.makedirs(PRODUCT_STORAGE_DIR)

# File to store product metadata (JSON)
PRODUCTS_DB_FILE = "products.json"

def load_products():
    if os.path.exists(PRODUCTS_DB_FILE):
        with open(PRODUCTS_DB_FILE, "r") as f:
            return json.load(f)
    return {}

def save_products(data):
    with open(PRODUCTS_DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Global variables/references to external state
# Ensure HOSTER_ADMIN_ID, user_balances dict, and pending_orders dict are managed properly
HOSTER_ADMIN_ID = 123456789  # Replace with actual Hoster Admin Telegram ID
user_balances = {}  # Format: {user_id: balance_amount}
pending_orders = {}  # Format: {order_id: {details}}
user_states = {}     # State machine for admin interactive inputs

# ----------------------------------------------------
# USER PANEL HANDLERS
# ----------------------------------------------------

@Client.on_message(filters.command("start") | filters.regex("^/start$"))
async def start_handler(client, message):
    # Main menu layout with the requested "Products" button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Products", callback_data="user_view_products")],
        [InlineKeyboardButton("💳 My Account / Balance", callback_data="user_check_balance")]
    ])
    await message.reply_text("welcome to the bot! Select an option below:", reply_markup=keyboard)

@Client.on_callback_query(filters.regex("^user_view_products$"))
async def user_view_products(client, callback_query):
    products = load_products()
    if not products:
        await callback_query.answer("No products available at the moment.", show_alert=True)
        return

    buttons = []
    for p_id, p_info in products.items():
        buttons.append([InlineKeyboardButton(f"{p_info['name']} - {p_info['price']} Points", callback_data=f"buy_prod_{p_id}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

    await callback_query.message.edit_text(
        "📦 **Available Products:**\nSelect a product to place an order.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex("^buy_prod_"))
async def buy_product_confirm(client, callback_query):
    prod_id = callback_query.data.split("_")[2]
    products = load_products()
    
    if prod_id not in products:
        await callback_query.answer("Product not found!", show_alert=True)
        return

    product = products[prod_id]
    user_id = callback_query.from_user.id
    current_balance = user_balances.get(user_id, 0)

    if current_balance < product['price']:
        await callback_query.answer(f"Insufficient funds! Price: {product['price']} Points, Your Balance: {current_balance} Points.", show_alert=True)
        return

    # Deduct funds immediately upon ordering
    user_balances[user_id] -= product['price']

    # Generate unique order ID
    order_id = f"ORD-{callback_query.id}"
    
    pending_orders[order_id] = {
        "user_id": user_id,
        "product_id": prod_id,
        "product_name": product['name'],
        "price": product['price'],
        "status": "PENDING"
    }

    await callback_query.message.edit_text(
        f"✅ **Order Submitted!**\n\nOrder ID: `{order_id}`\nProduct: {product['name']}\nDeducted Points: {product['price']}\n\nYour order is currently pending approval from the Hoster Admin."
    )

    # Send Notification to Hoster Admin Panel
    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve Order", callback_data=f"admin_approve_{order_id}"),
            InlineKeyboardButton("❌ Reject Order", callback_data=f"admin_reject_{order_id}")
        ]
    ])
    
    await client.send_message(
        chat_id=HOSTER_ADMIN_ID,
        text=f"🚨 **New Pending Order Received!**\n\n"
             f"**Order ID:** `{order_id}`\n"
             f"**User ID:** `{user_id}`\n"
             f"**Product:** {product['name']}\n"
             f"**Price Paid:** {product['price']} Points",
        reply_markup=admin_keyboard
    )

# ----------------------------------------------------
# HOSTER ADMIN PANEL HANDLERS
# ----------------------------------------------------

@Client.on_message(filters.command("admin") & filters.user(HOSTER_ADMIN_ID))
async def admin_panel(client, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Product", callback_data="admin_add_product")],
        [InlineKeyboardButton("🗑 Remove Product", callback_data="admin_remove_product")],
        [InlineKeyboardButton("✏️ Edit Product Price", callback_data="admin_edit_price")],
        [InlineKeyboardButton("💰 Manage User Fund", callback_data="admin_fund_user")]
    ])
    await message.reply_text("⚙️ **Hoster Admin Panel Control**", reply_markup=keyboard)

@Client.on_callback_query(filters.regex("^admin_approve_"))
async def admin_approve_order(client, callback_query):
    order_id = callback_query.data.split("_")[2]
    
    if order_id not in pending_orders or pending_orders[order_id]["status"] != "PENDING":
        await callback_query.answer("Order no longer active or already processed.", show_alert=True)
        return

    order = pending_orders[order_id]
    prod_id = order["product_id"]
    user_id = order["user_id"]
    
    # Check deliverable file in product storage folder
    prod_file_path = os.path.join(PRODUCT_STORAGE_DIR, f"{prod_id}.txt")

    if os.path.exists(prod_file_path):
        await client.send_document(
            chat_id=user_id,
            document=prod_file_path,
            caption=f"🎉 Your order for **{order['product_name']}** has been approved by the Admin!"
        )
    else:
        await client.send_message(
            chat_id=user_id,
            text=f"🎉 Your order for **{order['product_name']}** has been approved by the Admin!"
        )

    pending_orders[order_id]["status"] = "APPROVED"
    await callback_query.message.edit_text(f"✅ Order `{order_id}` approved successfully and product delivered.")

    # Mandatory step: Ask admin how much fund/bonus to add to user account post-approval
    user_states[callback_query.from_user.id] = {"action": "ADD_FUND_POST_APPROVAL", "target_user": user_id}
    await client.send_message(
        chat_id=HOSTER_ADMIN_ID,
        text=f"🔢 **Fund Allocation Request**\nEnter the amount of fund/points to add to User `{user_id}`'s account balance:"
    )

@Client.on_callback_query(filters.regex("^admin_reject_"))
async def admin_reject_order(client, callback_query):
    order_id = callback_query.data.split("_")[2]
    
    if order_id not in pending_orders or pending_orders[order_id]["status"] != "PENDING":
        await callback_query.answer("Order no longer active or already processed.", show_alert=True)
        return

    order = pending_orders[order_id]
    user_id = order["user_id"]
    
    # Refund points
    user_balances[user_id] = user_balances.get(user_id, 0) + order["price"]
    pending_orders[order_id]["status"] = "REJECTED"

    await callback_query.message.edit_text(f"❌ Order `{order_id}` has been rejected and points refunded to user.")
    await client.send_message(
        chat_id=user_id,
        text=f"❌ Your order `{order_id}` was rejected by the admin. `{order['price']}` Points have been refunded to your account."
    )

@Client.on_callback_query(filters.regex("^admin_add_product$"))
async def admin_add_product_start(client, callback_query):
    user_states[callback_query.from_user.id] = {"action": "AWAITING_PRODUCT_DATA"}
    await callback_query.message.edit_text(
        "➕ **Add Product**\nPlease send product details in format:\n`[ID]|[Name]|[Price]`\n\nExample: `p1|Netflix 1 Month|150`"
    )

@Client.on_callback_query(filters.regex("^admin_remove_product$"))
async def admin_remove_product_start(client, callback_query):
    products = load_products()
    if not products:
        await callback_query.answer("No products available.", show_alert=True)
        return

    buttons = []
    for p_id, p_info in products.items():
        buttons.append([InlineKeyboardButton(f"❌ Delete {p_info['name']}", callback_data=f"delete_prod_{p_id}")])
    
    await callback_query.message.edit_text("Select a product to delete:", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex("^delete_prod_"))
async def admin_delete_product_confirm(client, callback_query):
    prod_id = callback_query.data.split("_")[2]
    products = load_products()
    
    if prod_id in products:
        del products[prod_id]
        save_products(products)
        
        # Remove corresponding folder asset if exists
        prod_file_path = os.path.join(PRODUCT_STORAGE_DIR, f"{prod_id}.txt")
        if os.path.exists(prod_file_path):
            os.remove(prod_file_path)

        await callback_query.message.edit_text(f"✅ Product `{prod_id}` removed successfully.")

@Client.on_callback_query(filters.regex("^admin_edit_price$"))
async def admin_edit_price_start(client, callback_query):
    user_states[callback_query.from_user.id] = {"action": "AWAITING_PRICE_EDIT"}
    await callback_query.message.edit_text(
        "✏️ **Edit Product Price**\nSend product ID and new price formatted as:\n`[ID]|[NewPrice]`\n\nExample: `p1|200`"
    )

@Client.on_message(filters.private & filters.user(HOSTER_ADMIN_ID) & ~filters.command(["start", "admin"]))
async def admin_text_input_handler(client, message):
    admin_id = message.from_user.id
    if admin_id not in user_states:
        return

    state = user_states[admin_id]
    action = state.get("action")

    if action == "AWAITING_PRODUCT_DATA":
        try:
            p_id, name, price = message.text.split("|")
            products = load_products()
            products[p_id.strip()] = {
                "name": name.strip(),
                "price": float(price.strip())
            }
            save_products(products)
            
            # Create a placeholder file inside the isolated product directory
            prod_file_path = os.path.join(PRODUCT_STORAGE_DIR, f"{p_id.strip()}.txt")
            with open(prod_file_path, "w") as f:
                f.write(f"Product Delivery Content for {name.strip()}")

            await message.reply_text(f"✅ Product **{name.strip()}** added successfully. File initialized in `{PRODUCT_STORAGE_DIR}`.")
            del user_states[admin_id]
        except Exception:
            await message.reply_text("❌ Invalid format! Use: `[ID]|[Name]|[Price]`")

    elif action == "AWAITING_PRICE_EDIT":
        try:
            p_id, new_price = message.text.split("|")
            products = load_products()
            p_id = p_id.strip()
            
            if p_id in products:
                products[p_id]["price"] = float(new_price.strip())
                save_products(products)
                await message.reply_text(f"✅ Price updated for `{p_id}` to {new_price.strip()} Points.")
            else:
                await message.reply_text("❌ Product ID not found.")
            del user_states[admin_id]
        except Exception:
            await message.reply_text("❌ Invalid format! Use: `[ID]|[NewPrice]`")

    elif action == "ADD_FUND_POST_APPROVAL":
        try:
            amount = float(message.text.strip())
            target_user = state["target_user"]
            user_balances[target_user] = user_balances.get(target_user, 0) + amount
            
            await message.reply_text(f"✅ Successfully added `{amount}` funds to User `{target_user}`.")
            await client.send_message(
                chat_id=target_user,
                text=f"💳 `{amount}` Points have been added to your account balance by the Admin!"
            )
            del user_states[admin_id]
        except ValueError:
            await message.reply_text("❌ Invalid number! Please enter a numeric value.")