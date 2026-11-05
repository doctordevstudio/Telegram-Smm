from aiogram.filters import BaseFilter
from aiogram.types import Message

from .roles import is_owner, is_staff


class TextNoCmd(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and not message.text.startswith("/")


class NotCmd(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return not (message.text and message.text.startswith("/"))


class IsOwner(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return is_owner(message.from_user.id)


class IsStaff(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return is_staff(message.from_user.id)
