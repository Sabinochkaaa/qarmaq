"""
collector.py
Превращает "сырое" Telegram-сообщение в готовый отчёт.
Теперь также извлекает t.me/ ссылки из inline-кнопок подозрительных сообщений.
"""

import re
import logging
from datetime import datetime, timezone

from normalize import normalize_text
from prefilter import check_message
from groq_classifier import classify_message
from link_extractor import process_links

logger = logging.getLogger("collector")

CONTACT_USERNAME_RE = re.compile(r"@[a-zA-Z0-9_]{4,32}")
CONTACT_PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{7,14}\d")


def build_message_link(chat, message_id: int) -> str:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal_id = str(chat.id)
    if internal_id.startswith("-100"):
        internal_id = internal_id[4:]
    elif internal_id.startswith("-"):
        internal_id = internal_id[1:]
    return f"https://t.me/c/{internal_id}/{message_id}"


def extract_contacts(text: str) -> list:
    if not text:
        return []
    usernames = CONTACT_USERNAME_RE.findall(text)
    phones = CONTACT_PHONE_RE.findall(text)
    return list(set(usernames + phones))


async def build_report(message, chat) -> dict | None:
    """
    Возвращает None если сообщение пустое/только медиа без текста.
    Теперь включает discovered_links из inline-кнопок.
    """
    raw_text = message.message or ""
    if not raw_text.strip():
        return None

    normalized = normalize_text(raw_text)
    if not normalized:
        return None

    is_candidate, hints = check_message(normalized)

    sender_id = message.sender_id
    is_channel_post = (sender_id == chat.id) or (sender_id is None)
    sender_type = "channel_post" if is_channel_post else "user"

    extracted_contacts = extract_contacts(raw_text) if is_channel_post else []

    if is_candidate:
        ai_result = classify_message(normalized, prefilter_hints=hints)
    else:
        ai_result = {
            "category": "чистое",
            "risk_score": 0.0,
            "explanation": "Префильтр не нашёл подозрительных паттернов, AI-анализ не запускался.",
        }

    report = {
        "channel": getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat.id),
        "channel_id": chat.id,
        "channel_username": getattr(chat, "username", None),
        "message_id": message.id,
        "message_link": build_message_link(chat, message.id),
        "message_date": message.date.astimezone(timezone.utc).isoformat() if message.date else None,
        "sender_id": sender_id,
        "sender_type": sender_type,
        "extracted_contacts": extracted_contacts,
        "raw_text": raw_text,
        "normalized_text": normalized,
        "category": ai_result["category"],
        "risk_score": ai_result["risk_score"],
        "explanation": ai_result["explanation"],
        "prefilter_candidate": is_candidate,
        "prefilter_hints": hints,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "discovered_links": [],  # заполняется ниже если сообщение подозрительное
    }

    # Извлекаем ссылки из кнопок только для подозрительных сообщений
    if report["category"] != "чистое":
        report["discovered_links"] = process_links(report, message)

    return report


def load_channels(path: str) -> list:
    channels = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            channels.append(line)
    return channels