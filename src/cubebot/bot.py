"""Telegram-facing portion of CubeBot."""

from __future__ import annotations

import html
import logging
import re
from typing import TYPE_CHECKING, TypeVar

import httpx
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from cubebot.bencode import BencodeError, validate_torrent
from cubebot.transmission_rpc import AddedTorrent, Torrent, TransmissionError, TransmissionRPC

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cubebot.config import Settings

logger = logging.getLogger(__name__)

_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
_PAGE_SIZE = 8
_MAX_MAGNET_LENGTH = 16_384
_BYTES_PER_KIB = 1024
T = TypeVar("T")


class TelegramBot:
    """Private bot handlers bound to one Transmission RPC client."""

    def __init__(self, settings: Settings, rpc: TransmissionRPC) -> None:
        self._settings = settings
        self._rpc = rpc

    def register(self, application: Application) -> None:
        """Register all CubeBot handlers on a Telegram application."""
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("list", self.list_torrents))
        application.add_handler(CommandHandler("pause", self.pause_command))
        application.add_handler(CommandHandler("resume", self.resume_command))
        application.add_handler(MessageHandler(filters.Document.ALL, self.add_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_magnet_from_message))
        application.add_handler(CallbackQueryHandler(self.callback))
        application.add_error_handler(self.error_handler)

    async def start(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show usage information to an authorised user."""
        if not await self._authorised(update):
            return
        await self._reply_html(
            update,
            "<b>CubeBot</b> управляет вашим Transmission.\n\n"
            "Отправьте magnet-ссылку или файл <code>.torrent</code>.\n"
            "Команды: /status, /list, /pause &lt;hash&gt;, /resume &lt;hash&gt;.",
        )

    async def status(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Report Transmission availability and RPC version."""
        if not await self._authorised(update):
            return

        info = await self._with_rpc_error_reply(update, self._rpc.session_info)
        if info is None:
            return
        rpc_version = info.rpc_version if info.rpc_version is not None else "unknown"
        await self._reply_html(
            update,
            f"<b>Transmission доступен</b>\nВерсия: <code>{html.escape(info.version)}</code>\n"
            f"RPC: <code>{rpc_version}</code>",
        )

    async def list_torrents(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the first page of torrents and their controls."""
        if not await self._authorised(update):
            return

        torrents = await self._with_rpc_error_reply(update, self._rpc.list_torrents)
        if torrents is None:
            return
        await self._reply_torrent_page(update, torrents, page=0)

    async def pause_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Pause the torrent identified by the command argument."""
        await self._run_hash_command(update, context, self._rpc.stop, "Торрент приостановлен.")

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Resume the torrent identified by the command argument."""
        await self._run_hash_command(update, context, self._rpc.start, "Торрент запущен.")

    async def add_magnet_from_message(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Validate and add a magnet link received as a text message."""
        if not await self._authorised(update):
            return
        message = update.effective_message
        magnet = (message.text or "").strip() if message else ""
        if not magnet.lower().startswith("magnet:?"):
            await self._reply_html(
                update,
                "Отправьте magnet-ссылку или файл <code>.torrent</code>.",
            )
            return
        if len(magnet) > _MAX_MAGNET_LENGTH:
            await self._reply_html(update, "Magnet-ссылка слишком длинная.")
            return

        added = await self._with_rpc_error_reply(update, lambda: self._rpc.add_magnet(magnet))
        if added is not None:
            await self._reply_added(update, added)

    async def add_document(  # noqa: PLR0911 - guard clauses keep upload validation linear
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Validate and add an uploaded torrent document."""
        if not await self._authorised(update):
            return
        message = update.effective_message
        document = message.document if message else None
        if document is None:
            return
        filename = document.file_name or ""
        if not filename.lower().endswith(".torrent"):
            await self._reply_html(update, "Поддерживаются только файлы <code>.torrent</code>.")
            return
        if document.file_size and document.file_size > self._settings.max_torrent_file_bytes:
            await self._reply_html(
                update,
                f"Файл больше лимита {_format_bytes(self._settings.max_torrent_file_bytes)}.",
            )
            return

        try:
            telegram_file = await context.bot.get_file(document.file_id)
            torrent_file = bytes(await telegram_file.download_as_bytearray())
        except TelegramError as error:  # Telegram retries are handled by the library itself.
            logger.warning("Could not download Telegram document: type=%s", type(error).__name__)
            await self._reply_html(update, "Не удалось скачать файл из Telegram. Попробуйте ещё раз.")
            return

        if len(torrent_file) > self._settings.max_torrent_file_bytes:
            await self._reply_html(
                update,
                f"Файл больше лимита {_format_bytes(self._settings.max_torrent_file_bytes)}.",
            )
            return
        try:
            validate_torrent(torrent_file)
        except BencodeError:
            await self._reply_html(update, "Файл не похож на корректный <code>.torrent</code>.")
            return

        added = await self._with_rpc_error_reply(update, lambda: self._rpc.add_metainfo(torrent_file))
        if added is not None:
            await self._reply_added(update, added)

    async def callback(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Dispatch an inline torrent control or pagination action."""
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        if not await self._authorised(update):
            return

        action, separator, argument = (query.data or "").partition(":")
        if action == "page" and separator and argument.isdecimal():
            await self._edit_torrent_page(query, int(argument))
            return
        if action == "info" and separator and _HASH_PATTERN.fullmatch(argument):
            await self._edit_torrent_detail(query, argument.lower())
            return
        if not separator or not _HASH_PATTERN.fullmatch(argument):
            await query.edit_message_text("Кнопка устарела. Выполните /list ещё раз.")
            return
        torrent_hash = argument.lower()

        try:
            await self._run_torrent_action(query, action, torrent_hash)
        except (TransmissionError, ValueError, httpx.HTTPError) as error:
            logger.warning("Transmission action failed: action=%s type=%s", action, type(error).__name__)
            await query.edit_message_text("Не удалось выполнить действие в Transmission. Попробуйте ещё раз.")

    async def _run_torrent_action(self, query: CallbackQuery, action: str, torrent_hash: str) -> None:
        if action == "start":
            await self._rpc.start(torrent_hash)
            await query.edit_message_text("Торрент запущен.")
        elif action == "stop":
            await self._rpc.stop(torrent_hash)
            await query.edit_message_text("Торрент приостановлен.")
        elif action == "remove":
            await query.edit_message_text(
                "Удалить торрент из Transmission, сохранив файлы?",
                reply_markup=_confirmation_keyboard("remove_yes", torrent_hash, "Да, удалить торрент"),
            )
        elif action == "delete":
            await query.edit_message_text(
                "Удалить торрент <b>вместе с файлами</b>?",
                parse_mode=ParseMode.HTML,
                reply_markup=_confirmation_keyboard("delete_yes", torrent_hash, "Да, удалить файлы"),
            )
        elif action == "remove_yes":
            await self._rpc.remove(torrent_hash, delete_data=False)
            await query.edit_message_text("Торрент удалён. Данные сохранены.")
        elif action == "delete_yes":
            await self._rpc.remove(torrent_hash, delete_data=True)
            await query.edit_message_text("Торрент и его данные удалены.")
        elif action == "cancel":
            await query.edit_message_text("Удаление отменено.")
        else:
            await query.edit_message_text("Кнопка устарела. Выполните /list ещё раз.")

    async def _reply_torrent_page(self, update: Update, torrents: tuple[Torrent, ...], page: int) -> None:
        if not torrents:
            await self._reply_html(update, "В Transmission нет торрентов.")
            return
        text, keyboard = _torrent_page(torrents, page)
        await self._reply_html(update, text, reply_markup=keyboard)

    async def _edit_torrent_page(self, query: CallbackQuery, page: int) -> None:
        try:
            torrents = await self._rpc.list_torrents()
        except (TransmissionError, ValueError, httpx.HTTPError) as error:
            logger.warning("Transmission RPC operation failed: type=%s", type(error).__name__)
            await query.edit_message_text("Transmission недоступен или отклонил запрос. Попробуйте ещё раз.")
            return
        if not torrents:
            await query.edit_message_text("В Transmission нет торрентов.")
            return
        text, keyboard = _torrent_page(torrents, page)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def _edit_torrent_detail(self, query: CallbackQuery, torrent_hash: str) -> None:
        try:
            torrents = await self._rpc.list_torrents()
        except (TransmissionError, ValueError, httpx.HTTPError) as error:
            logger.warning("Transmission RPC operation failed: type=%s", type(error).__name__)
            await query.edit_message_text("Transmission недоступен или отклонил запрос. Попробуйте ещё раз.")
            return
        torrent = next((item for item in torrents if item.hash_string == torrent_hash), None)
        if torrent is None:
            await query.edit_message_text("Торрент больше не найден. Выполните /list ещё раз.")
            return
        await query.edit_message_text(
            _torrent_text(torrent), parse_mode=ParseMode.HTML, reply_markup=_torrent_keyboard(torrent)
        )

    async def error_handler(self, _update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log an unhandled Telegram error without exposing its details."""
        # Some third-party HTTP exceptions include the complete request URL.  Do
        # not log the exception body here: a Telegram API URL can contain the bot
        # token in its path.
        error_type = type(context.error).__name__ if context.error else "UnknownError"
        logger.error("Unhandled Telegram update error: type=%s", error_type)

    async def _run_hash_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        operation: Callable[[str], Awaitable[None]],
        success_message: str,
    ) -> None:
        if not await self._authorised(update):
            return
        if len(context.args) != 1 or not _HASH_PATTERN.fullmatch(context.args[0]):
            await self._reply_html(update, "Укажите 40-символьный hash: <code>/pause HASH</code>.")
            return
        try:
            await operation(context.args[0].lower())
        except (TransmissionError, ValueError, httpx.HTTPError) as error:
            logger.warning("Transmission RPC operation failed: type=%s", type(error).__name__)
            await self._reply_html(update, "Transmission недоступен или отклонил запрос. Попробуйте ещё раз.")
        else:
            await self._reply_html(update, success_message)

    async def _authorised(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return False
        if chat.type != "private":
            await self._reply_html(update, "Бот работает только в личном чате.")
            return False
        if user.id not in self._settings.allowed_user_ids:
            logger.warning("Rejected unauthorised Telegram user: user_id=%s", user.id)
            await self._reply_html(update, "Доступ запрещён.")
            return False
        return True

    async def _with_rpc_error_reply(self, update: Update, operation: Callable[[], Awaitable[T]]) -> T | None:
        try:
            return await operation()
        except (TransmissionError, ValueError, httpx.HTTPError) as error:
            logger.warning("Transmission RPC operation failed: type=%s", type(error).__name__)
            await self._reply_html(update, "Transmission недоступен или отклонил запрос. Попробуйте ещё раз.")
            return None

    async def _reply_added(self, update: Update, added: AddedTorrent) -> None:
        prefix = "Этот торрент уже есть" if added.duplicate else "Торрент добавлен"
        await self._reply_html(update, f"{prefix}: <b>{html.escape(added.name)}</b>")

    async def _reply_html(
        self,
        update: Update,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        message = update.effective_message
        if message is not None:
            await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


def build_application(settings: Settings, rpc: TransmissionRPC) -> Application:
    """Build an application with an orderly RPC-client shutdown hook."""

    async def close_rpc(_application: Application) -> None:
        await rpc.aclose()

    # Explicitly disable inherited HTTP(S)/SOCKS proxy settings so Telegram
    # tokens, RPC credentials, and torrent metainfo cannot reach an accidental
    # proxy inherited from the host or container environment.
    application = (
        Application.builder()
        .token(settings.bot_token)
        .request(HTTPXRequest(httpx_kwargs={"trust_env": False}))
        .get_updates_request(HTTPXRequest(httpx_kwargs={"trust_env": False}))
        .post_shutdown(close_rpc)
        .build()
    )
    service = TelegramBot(settings, rpc)
    service.register(application)
    return application


def _torrent_text(torrent: Torrent) -> str:
    progress = min(max(torrent.percent_done, 0.0), 1.0) * 100
    lines = [
        f"<b>{html.escape(torrent.name)}</b>",
        f"{torrent.status_label.capitalize()} · {progress:.1f}%",
        f"↓ {_format_rate(torrent.rate_download)} · ↑ {_format_rate(torrent.rate_upload)}",
        f"Размер: {_format_bytes(torrent.size_when_done)}",
        f"<code>{html.escape(torrent.hash_string)}</code>",
    ]
    if torrent.error_string:
        lines.append(f"Ошибка: {html.escape(torrent.error_string)}")
    return "\n".join(lines)


def _torrent_keyboard(torrent: Torrent) -> InlineKeyboardMarkup:
    action = "start" if torrent.is_stopped else "stop"
    action_label = "Запустить" if torrent.is_stopped else "Пауза"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(action_label, callback_data=f"{action}:{torrent.hash_string}")],
            [InlineKeyboardButton("Удалить (данные оставить)", callback_data=f"remove:{torrent.hash_string}")],
            [InlineKeyboardButton("Удалить вместе с файлами", callback_data=f"delete:{torrent.hash_string}")],
            [InlineKeyboardButton("К списку", callback_data="page:0")],
        ]
    )


def _torrent_page(torrents: tuple[Torrent, ...], requested_page: int) -> tuple[str, InlineKeyboardMarkup]:
    page_count = max(1, (len(torrents) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(max(requested_page, 0), page_count - 1)
    start = page * _PAGE_SIZE
    visible = torrents[start : start + _PAGE_SIZE]
    lines = [f"<b>Торренты {start + 1}–{start + len(visible)} из {len(torrents)}</b>"]
    buttons: list[list[InlineKeyboardButton]] = []
    for number, torrent in enumerate(visible, start=start + 1):
        name = _truncate(torrent.name, 44)
        lines.append(
            f"{number}. <b>{html.escape(name)}</b> — {torrent.status_label}, "
            f"{min(max(torrent.percent_done, 0.0), 1.0) * 100:.1f}%"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{number}. {_truncate(torrent.name, 42)}", callback_data=f"info:{torrent.hash_string}"
                )
            ]
        )
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton("← Назад", callback_data=f"page:{page - 1}"))
    if page < page_count - 1:
        navigation.append(InlineKeyboardButton("Вперёд →", callback_data=f"page:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def _confirmation_keyboard(action: str, torrent_hash: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(label, callback_data=f"{action}:{torrent_hash}"),
                InlineKeyboardButton("Отмена", callback_data=f"cancel:{torrent_hash}"),
            ]
        ]
    )


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _format_bytes(value: int) -> str:
    value = max(value, 0)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < _BYTES_PER_KIB or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} {unit}"
        amount /= _BYTES_PER_KIB
    return f"{amount:.1f} TiB"


def _format_rate(value: int) -> str:
    return f"{_format_bytes(value)}/s"
