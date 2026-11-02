from aiogram.fsm.state import State, StatesGroup


class ChannelSt(StatesGroup):
    link = State()


class OrderSt(StatesGroup):
    post = State()
    stream = State()
    qty = State()
    split = State()
    reactions = State()


class DepositSt(StatesGroup):
    amount = State()
    screenshot = State()


class AdminSt(StatesGroup):
    reject_reason = State()
    broadcast = State()
    send = State()
    qr = State()
