"""
link_extractor.py
Извлекает t.me/ ссылки из inline-кнопок сообщения (ReplyInlineMarkup).
Запускается только для подозрительных сообщений (category != "чистое").

Два выходных потока:
1. discovered_links.jsonl  — каждая найденная ссылка с полным контекстом
2. channel_mentions.json   — счётчик упоминаний каналов/ботов (для графа)
"""

import re
import json
import os
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("link_extractor")

DISCOVERED_FILE = os.environ.get("DISCOVERED_LINKS_FILE", "discovered_links.jsonl")
MENTIONS_FILE = os.environ.get("CHANNEL_MENTIONS_FILE", "channel_mentions.json")

_lock = threading.Lock()

# Определяем тип ссылки: бот или канал
BOT_RE = re.compile(r"t\.me/([a-zA-Z0-9_]+bot\b|[a-zA-Z0-9_]+_bot\b)", re.IGNORECASE)
CHANNEL_RE = re.compile(r"t\.me/(?!joinchat|c/)([a-zA-Z0-9_]{5,32})(?:/\d+)?$")
INVITE_RE = re.compile(r"t\.me/(?:\+|joinchat/)([a-zA-Z0-9_-]+)")
TG_URL_RE = re.compile(r"https?://t\.me/\S+")


def _classify_link(url: str) -> str:
    """Возвращает 'bot', 'invite', 'channel' или 'other'."""
    if BOT_RE.search(url):
        return "bot"
    if INVITE_RE.search(url):
        return "invite"
    if CHANNEL_RE.search(url):
        return "channel"
    return "other"


def _extract_username(url: str) -> str | None:
    """Вытаскивает @username или hash из ссылки."""
    m = INVITE_RE.search(url)
    if m:
        return f"+{m.group(1)}"
    m = CHANNEL_RE.search(url)
    if m:
        return f"@{m.group(1)}"
    m = BOT_RE.search(url)
    if m:
        return f"@{m.group(1)}"
    return None


def extract_links_from_buttons(message) -> list[dict]:
    """
    Парсит message.buttons (Telethon ReplyInlineMarkup).
    Возвращает список dict: {button_text, url, link_type, target}
    """
    found = []
    if not message.buttons:
        return found

    for row in message.buttons:
        # message.buttons может быть как список списков, так и список объектов
        buttons = row if isinstance(row, (list, tuple)) else [row]
        for btn in buttons:
            url = getattr(btn, "url", None)
            if not url:
                continue
            if "t.me/" not in url:
                continue
            link_type = _classify_link(url)
            target = _extract_username(url)
            found.append({
                "button_text": getattr(btn, "text", ""),
                "url": url,
                "link_type": link_type,
                "target": target,
            })
    return found


def save_discovered_link(report: dict, btn: dict):
    """
    Сохраняет одну найденную ссылку в discovered_links.jsonl.
    Вызывается для каждой кнопки отдельно.
    """
    record = {
        "found_at": datetime.now(timezone.utc).isoformat(),
        "source_channel": report.get("channel"),
        "source_channel_id": report.get("channel_id"),
        "source_channel_username": report.get("channel_username"),
        "source_message_id": report.get("message_id"),
        "source_message_link": report.get("message_link"),
        "source_message_date": report.get("message_date"),
        "source_category": report.get("category"),
        "source_risk_score": report.get("risk_score"),
        "button_text": btn.get("button_text"),
        "discovered_url": btn.get("url"),
        "link_type": btn.get("link_type"),   # bot / channel / invite / other
        "target": btn.get("target"),          # @username или +hash
    }
    with _lock:
        with open(DISCOVERED_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def update_mentions(source_channel: str, target: str, link_type: str):
    """
    Обновляет channel_mentions.json:
    {
      "@target": {
        "total_mentions": N,
        "link_type": "channel",
        "mentioned_by": {"@source1": 3, "@source2": 1}
      }
    }
    """
    if not target:
        return

    with _lock:
        # Загружаем текущее состояние
        data = {}
        if os.path.exists(MENTIONS_FILE):
            try:
                with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = {}

        if target not in data:
            data[target] = {
                "total_mentions": 0,
                "link_type": link_type,
                "mentioned_by": {}
            }

        data[target]["total_mentions"] += 1
        data[target]["link_type"] = link_type  # обновляем на случай уточнения

        mentioned_by = data[target].setdefault("mentioned_by", {})
        mentioned_by[source_channel] = mentioned_by.get(source_channel, 0) + 1

        with open(MENTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def process_links(report: dict, message) -> list[dict]:
    """
    Главная функция — вызывается из collector.py после получения report.
    Извлекает ссылки, сохраняет локально И возвращает список для отправки на сервер.
    """
    if report.get("category") == "чистое":
        return []

    buttons = extract_links_from_buttons(message)
    if not buttons:
        return []

    source_channel = report.get("channel_username") or report.get("channel") or str(report.get("channel_id"))
    results = []

    for btn in buttons:
        save_discovered_link(report, btn)
        update_mentions(source_channel, btn.get("target"), btn.get("link_type"))
        results.append({
            "button_text": btn.get("button_text"),
            "discovered_url": btn.get("url"),
            "link_type": btn.get("link_type"),
            "target": btn.get("target"),
        })
        logger.info(
            f"Найдена ссылка [{btn['link_type']}] {btn['target']} "
            f"в кнопке '{btn['button_text']}' "
            f"из канала {source_channel}"
        )

    return results