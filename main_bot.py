import asyncio, uuid, os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties

import config, database, keyboards, telethon_worker

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()

class IsAdmin(Filter):
    async def __call__(self, message: Message) -> bool: return message.from_user.id == config.ADMIN_CHAT_ID

class AppState(StatesGroup):
    dep_custom = State(); dep_ss = State(); dep_reject = State()
    ord_target = State(); ord_count = State(); ord_confirm = State()
    admin_broadcast = State(); admin_send = State()

@dp.message(CommandStart())
async def start_cmd(message: Message):
    if not database.get_user(message.chat.id):
        database.create_user(message.chat.id, message.from_user.full_name, message.from_user.username)
        await bot.send_message(config.ADMIN_CHAT_ID, f"🚨 <b>New User:</b> {message.from_user.full_name} (@{message.from_user.username})")
    
    if database.get_user(message.chat.id).get('is_banned'): return await message.answer("❌ You are banned.")
    await message.answer("👋 Welcome to <b>Telegram SMM</b>!", reply_markup=keyboards.main_menu())

# --- Wallet ---
@dp.message(F.text == "💳 Wallet")
async def wallet_cmd(message: Message):
    bal = database.get_user(message.chat.id).get('wallet_balance', 0)
    await message.answer(f"💳 <b>Wallet Balance:</b> ₹{bal}\nSelect amount to deposit:", reply_markup=keyboards.deposit_menu())

@dp.callback_query(F.data.startswith("dep_"))
async def dep_cb(call: CallbackQuery, state: FSMContext):
    amt = call.data.split("_")[1]
    if amt == "custom":
        await call.message.answer("✏️ Enter amount (Min ₹50):")
        await state.set_state(AppState.dep_custom)
    else:
        await process_dep(call.message, int(amt), state)
    await call.answer()

@dp.message(AppState.dep_custom)
async def dep_cust_msg(msg: Message, state: FSMContext):
    if not msg.text.isdigit() or int(msg.text) < 50: return await msg.answer("⚠️ Enter valid amount ≥ 50.")
    await process_dep(msg, int(msg.text), state)

async def process_dep(msg: Message, amt: int, state: FSMContext):
    await state.update_data(amt=amt)
    settings = database.get_settings()
    text = f"🏦 <b>Pay ₹{amt}</b>\nUPI: <code>{settings.get('upi_id', 'Not Set')}</code>\nUpload Screenshot below."
    await bot.send_message(msg.chat.id, text)
    await state.set_state(AppState.dep_ss)

@dp.message(AppState.dep_ss, F.photo)
async def handle_ss(msg: Message, state: FSMContext):
    data = await state.get_data(); amt = data['amt']; pay_id = str(uuid.uuid4())[:8]
    ss_id = msg.photo[-1].file_id
    database.log_payment(pay_id, msg.chat.id, amt, "pending", ss_id=ss_id)
    await msg.answer("✅ Screenshot submitted for admin approval.")
    
    admin_text = f"💳 <b>New Deposit:</b> ₹{amt}\nUser: {msg.chat.id}"
    await bot.send_photo(config.ADMIN_CHAT_ID, photo=ss_id, caption=admin_text, reply_markup=keyboards.admin_approval_menu(msg.chat.id, amt, pay_id))
    await state.clear()

@dp.callback_query(F.data.startswith("approve_"), IsAdmin())
async def approve_dep(call: CallbackQuery):
    _, uid, amt, pid = call.data.split("_"); uid, amt = int(uid), int(amt)
    database.update_wallet(uid, amt); database.log_payment(pid, uid, amt, "approved")
    await call.message.edit_caption(caption=call.message.caption + "\n\n✅ APPROVED")
    await bot.send_message(uid, f"🎉 ₹{amt} added to your wallet!")

# --- Orders ---
@dp.message(F.text == "📦 Plan")
async def plan_cmd(msg: Message): await msg.answer("📦 Select Service:", reply_markup=keyboards.service_selection_menu())

@dp.callback_query(F.data.startswith("srv_"))
async def srv_cb(call: CallbackQuery, state: FSMContext):
    srv = call.data.split("_")[1]; await state.update_data(srv=srv)
    await call.message.edit_text("🔗 Send Target Link / Forward Message:", reply_markup=keyboards.cancel_menu())
    await state.set_state(AppState.ord_target)

@dp.message(AppState.ord_target)
async def target_msg(msg: Message, state: FSMContext):
    target = msg.text if msg.text else f"{msg.forward_from_chat.id}_{msg.forward_from_message_id}"
    await state.update_data(tgt=target)
    await msg.answer("✏️ Enter quantity you want to buy:", reply_markup=keyboards.cancel_menu())
    await state.set_state(AppState.ord_count)

@dp.message(AppState.ord_count)
async def count_msg(msg: Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("⚠️ Number only.")
    cnt = int(msg.text); data = await state.get_data()
    price = database.get_service_plans(data['srv']).get('unit_price', 1.0) * cnt
    await state.update_data(cnt=cnt, price=price)
    
    text = f"🛒 <b>Order:</b> {data['srv']}\nTarget: {data['tgt'][:20]}\nCount: {cnt}\n<b>Total: ₹{price}</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Pay from Wallet", callback_data="pay_ord")], [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_order")]])
    await msg.answer(text, reply_markup=kb)
    await state.set_state(AppState.ord_confirm)

@dp.callback_query(F.data == "pay_ord")
async def pay_ord(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    od = {"user_id": call.message.chat.id, "type": data['srv'], "target": data['tgt'], "count": data['cnt'], "config": ""}
    oid = database.process_order_payment(call.message.chat.id, data['price'], od)
    if oid:
        await call.message.edit_text(f"✅ Order <code>{oid}</code> processing.")
        asyncio.create_task(telethon_worker.process_order(oid, od))
    else:
        await call.message.edit_text("❌ Insufficient Wallet Balance.")
    await state.clear()

@dp.callback_query(F.data == "cancel_order")
async def cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear(); await call.message.edit_text("🚫 Cancelled.")

# --- Admin Broadcast ---
@dp.message(Command("broadcast"), IsAdmin())
async def bcast_cmd(msg: Message, state: FSMContext):
    await msg.answer("📢 Send message to broadcast:"); await state.set_state(AppState.admin_broadcast)

@dp.message(AppState.admin_broadcast, IsAdmin())
async def bcast_exec(msg: Message, state: FSMContext):
    users = database.get_all_users()
    for u in users.keys():
        try: await msg.copy_to(int(u)); await asyncio.sleep(0.05)
        except: pass
    await msg.answer("✅ Broadcast sent."); await state.clear()
