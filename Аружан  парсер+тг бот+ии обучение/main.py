"""
main.py — финальная версия с health check и статистикой
"""

import os
import asyncio
import logging

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from collector import build_report
from channel_resolver import discover_from_dialogs, resolve_from_links
from storage import init_db, is_processed, mark_processed
from sender import send_report, check_server_health
from stats_tracker import (
    set_channels_count, set_live_mode,
    update_from_report, update_top_mentioned
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_NAME = os.environ.get("TG_SESSION_NAME", "qarmaq_session")

BACKFILL_LIMIT = int(os.environ.get("BACKFILL_LIMIT", "300"))
BACKFILL_DELAY_SEC = float(os.environ.get("BACKFILL_DELAY_SEC", "2"))
SEND_ONLY_SUSPICIOUS = os.environ.get("SEND_ONLY_SUSPICIOUS", "true").lower() == "true"

CHANNELS_FILE = os.environ.get("CHANNELS_FILE", "channels.txt")
CHANNEL_SOURCE = os.environ.get("CHANNEL_SOURCE", "auto").lower()
FOLDER_NAME = os.environ.get("TG_FOLDER_NAME", "").strip() or None

client = None


async def handle_report(report: dict | None, channel_id: int, message_id: int):
    if report is None:
        mark_processed(channel_id, message_id)
        return

    update_from_report(report)

    should_send = (not SEND_ONLY_SUSPICIOUS) or (report["category"] != "чистое")
    if should_send:
        send_report(report)
        if report["category"] != "чистое":
            discovered = report.get("discovered_links", [])
            links_info = f" | {len(discovered)} ссылок в кнопках" if discovered else ""
            logger.info(
                f"[{report['category']} | score={report['risk_score']}]{links_info} "
                f"{report['channel']} -> {report['message_link']}"
            )

    mark_processed(channel_id, message_id)


async def backfill_channel(chat):
    logger.info(f"Бэкфилл канала: {getattr(chat, 'title', chat.id)} (лимит {BACKFILL_LIMIT})")
    count = 0
    try:
        async for message in client.iter_messages(chat, limit=BACKFILL_LIMIT):
            if is_processed(chat.id, message.id):
                continue
            try:
                report = await build_report(message, chat)
            except Exception as e:
                logger.error(f"Ошибка обработки сообщения {message.id} в {chat.id}: {e}")
                continue
            await handle_report(report, chat.id, message.id)
            count += 1
    except FloodWaitError as e:
        logger.warning(f"FloodWait на канале {chat.id}: ждём {e.seconds} сек")
        await asyncio.sleep(e.seconds + 1)

    logger.info(f"Бэкфилл {getattr(chat, 'title', chat.id)} завершён — {count} сообщений")
    update_top_mentioned()


async def on_new_message(event):
    chat = await event.get_chat()
    try:
        report = await build_report(event.message, chat)
    except Exception as e:
        logger.error(f"Ошибка нового сообщения {event.message.id} в {chat.id}: {e}")
        return
    await handle_report(report, chat.id, event.message.id)

    if report and report.get("discovered_links"):
        update_top_mentioned()


async def main():
    global client
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    init_db()
    await client.start()
    logger.info("Подключение к Telegram установлено")

    # Проверяем сервер при старте
    if check_server_health():
        logger.info("Сервер Сабины доступен — отчёты будут отправляться онлайн")
    else:
        logger.warning("Сервер недоступен — отчёты будут сохраняться в reports_fallback.jsonl")

    if CHANNEL_SOURCE == "auto":
        logger.info("CHANNEL_SOURCE=auto — беру каналы из диалогов" +
                    (f" (папка: {FOLDER_NAME})" if FOLDER_NAME else ""))
        resolved_chats = await discover_from_dialogs(client, folder_name=FOLDER_NAME)
    else:
        logger.info(f"CHANNEL_SOURCE=links — беру из {CHANNELS_FILE}")
        from collector import load_channels
        channel_refs = load_channels(CHANNELS_FILE)
        if not channel_refs:
            logger.error(f"{CHANNELS_FILE} пуст")
            return
        resolved_chats = await resolve_from_links(client, channel_refs)

    if not resolved_chats:
        logger.error("Нет каналов для мониторинга")
        return

    set_channels_count(len(resolved_chats))

    for chat in resolved_chats:
        await backfill_channel(chat)
        await asyncio.sleep(BACKFILL_DELAY_SEC)

    update_top_mentioned()

    set_live_mode(True)
    client.add_event_handler(on_new_message, events.NewMessage(chats=resolved_chats))
    logger.info(f"Бэкфилл завершён ({len(resolved_chats)} каналов). Live-режим активен.")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())