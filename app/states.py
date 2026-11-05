from aiogram.fsm.state import State, StatesGroup


class ChannelSt(StatesGroup):
    link = State()
    days = State()
    renew_days = State()


class ServiceSt(StatesGroup):
    days = State()
    rate = State()


class NumSt(StatesGroup):
    value = State()


class AdminMgmtSt(StatesGroup):
    add_id = State()


class ProxySt(StatesGroup):
    url = State()
