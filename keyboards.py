from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Home"), KeyboardButton(text="📢 My Channel"), KeyboardButton(text="📦 Plan")],
            [KeyboardButton(text="💳 Wallet"), KeyboardButton(text="📜 History"), KeyboardButton(text="🎧 Customer Support")]
        ], resize_keyboard=True
    )

def deposit_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₹50", callback_data="dep_50"), InlineKeyboardButton(text="₹100", callback_data="dep_100")],
        [InlineKeyboardButton(text="₹150", callback_data="dep_150"), InlineKeyboardButton(text="₹200", callback_data="dep_200")],
        [InlineKeyboardButton(text="✏️ Custom Amount", callback_data="dep_custom")]
    ])

def admin_approval_menu(user_id, amount, pay_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{user_id}_{amount}_{pay_id}")],
        [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{user_id}_{amount}_{pay_id}")]
    ])

def service_selection_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Subscribers", callback_data="srv_subscribers")],
        [InlineKeyboardButton(text="👁️ Views", callback_data="srv_views")],
        [InlineKeyboardButton(text="❤️ Reactions", callback_data="srv_reactions")],
        [InlineKeyboardButton(text="🎥 Live Stream Viewers", callback_data="srv_livestream")]
    ])

def cancel_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_order")]])
