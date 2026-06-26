"""
stats_tracker.py
Ведёт live-статистику работы скрипта в stats.json.
Обновляется после каждого обработанного сообщения и после каждого бэкфилла.
"""

import os
import json
import threading
import logging
from datetime import datetime, timezone

logger = logging.getLogger("stats_tracker")

STATS_FILE = os.environ.get("STATS_FILE", "stats.json")
MENTIONS_FILE = os.environ.get("CHANNEL_MENTIONS_FILE", "channel_mentions.json")

_lock = threading.Lock()


def _load() -> dict:
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "last_updated": None,
        "session_started": datetime.now(timezone.utc).isoformat(),
        "channels_monitored": 0,
        "messages_total": 0,
        "messages_suspicious": 0,
        "suspicious_percent": 0.0,
        "by_category": {
            "наркотики": 0,
            "дроп": 0,
            "пирамида": 0,
            "казино": 0,
            "утечка_бд": 0,
        },
        "discovered_channels_total": 0,
        "discovered_bots_total": 0,
        "discovered_invites_total": 0,
        "top5_mentioned": [],
        "top5_source_channels": {},
        "activity_by_hour": {str(h): 0 for h in range(24)},
        "live_mode": False,
    }


def _save(data: dict):
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_channels_count(count: int):
    """Вызывается один раз при старте — сколько каналов мониторим."""
    with _lock:
        data = _load()
        data["channels_monitored"] = count
        _save(data)


def set_live_mode(is_live: bool):
    """Переключает флаг live-режима."""
    with _lock:
        data = _load()
        data["live_mode"] = is_live
        _save(data)


def update_from_report(report: dict):
    """
    Вызывается после каждого обработанного сообщения.
    Обновляет счётчики категорий, активность по часам, топы.
    """
    if not report:
        return

    with _lock:
        data = _load()

        # Общие счётчики
        data["messages_total"] += 1
        category = report.get("category", "чистое")

        if category != "чистое":
            data["messages_suspicious"] += 1
            data["by_category"][category] = data["by_category"].get(category, 0) + 1

            # Топ каналов-источников подозрительного контента
            source = report.get("channel", "unknown")
            top_sources = data.setdefault("top5_source_channels", {})
            top_sources[source] = top_sources.get(source, 0) + 1

        # Процент подозрительных
        if data["messages_total"] > 0:
            data["suspicious_percent"] = round(
                data["messages_suspicious"] / data["messages_total"] * 100, 2
            )

        # Активность по часам (UTC)
        msg_date = report.get("message_date")
        if msg_date:
            try:
                hour = str(datetime.fromisoformat(msg_date).hour)
                data["activity_by_hour"][hour] = data["activity_by_hour"].get(hour, 0) + 1
            except (ValueError, KeyError):
                pass

        # Счётчики найденных ссылок из кнопок
        for link in report.get("discovered_links", []):
            ltype = link.get("link_type")
            if ltype == "bot":
                data["discovered_bots_total"] += 1
            elif ltype == "channel":
                data["discovered_channels_total"] += 1
            elif ltype == "invite":
                data["discovered_invites_total"] += 1

        _save(data)


def update_top_mentioned():
    """
    Пересчитывает топ-5 упоминаемых каналов/ботов из channel_mentions.json.
    Вызывается после бэкфилла каждого канала.
    """
    if not os.path.exists(MENTIONS_FILE):
        return

    try:
        with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
            mentions = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    sorted_mentions = sorted(
        mentions.items(),
        key=lambda x: x[1].get("total_mentions", 0),
        reverse=True
    )[:5]

    top5 = [
        {
            "target": target,
            "count": info.get("total_mentions", 0),
            "link_type": info.get("link_type", "unknown"),
            "mentioned_by_count": len(info.get("mentioned_by", {})),
        }
        for target, info in sorted_mentions
    ]

    with _lock:
        data = _load()
        data["top5_mentioned"] = top5
        _save(data)