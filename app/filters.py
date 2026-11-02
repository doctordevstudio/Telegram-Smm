from aiogram.filters import BaseFilter
from aiogram.types import Message


class TextNoCmd(BaseFilter):
    """Plain text that is not a /command."""

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and not message.text.startswith("/")


class NotCmd(BaseFilter):
    """Any content type (photo, video, file, text...) except a /command."""

    async def __call__(self, message: Message) -> bool:
        return not (message.text and message.text.startswith("/"))
