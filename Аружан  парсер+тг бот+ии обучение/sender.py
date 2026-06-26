"""
sender.py — обновлено под сервер Сабины (версия 2)

Эндпоинты:
- /ingest/report  — основной отчёт по сообщению
- /ingest/links   — найденные ссылки из кнопок

mentions и stats сервер считает сам — их больше не отправляем.
"""

import os
import logging
import requests

from storage import save_fallback

logger = logging.getLogger("sender")

SERVER_URL = os.environ.get("QARMAQ_SERVER_URL", "")
INGEST_TOKEN = os.environ.get("QARMAQ_INGEST_TOKEN", "")


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-Ingest-Token": INGEST_TOKEN,
    }


def _check_server_url() -> bool:
    return bool(SERVER_URL) and "ЗАМЕНИ-НА-АДРЕС" not in SERVER_URL


def _post(endpoint: str, payload: dict, fallback_type: str) -> bool:
    if not _check_server_url():
        logger.warning(f"QARMAQ_SERVER_URL не настроен — сохраняю {fallback_type} локально")
        save_fallback({**payload, "_fallback_type": fallback_type})
        return False

    url = SERVER_URL.rstrip("/") + endpoint
    try:
        response = requests.post(url, json=payload, headers=_headers(), timeout=15)
        if response.status_code == 200:
            return True
        elif response.status_code == 401:
            logger.error("Сервер отверг токен — попроси новый у Сабины")
        else:
            logger.error(f"[{endpoint}] Сервер ответил {response.status_code}: {response.text[:200]}")
    except requests.exceptions.ConnectionError:
        logger.error(f"[{endpoint}] Сервер недоступен — сохраняю локально")
    except requests.exceptions.Timeout:
        logger.error(f"[{endpoint}] Таймаут 15 сек")
    except Exception as e:
        logger.error(f"[{endpoint}] Неожиданная ошибка: {e}")

    save_fallback({**payload, "_fallback_type": fallback_type})
    return False


def send_report(report: dict) -> bool:
    """
    Отправляет основной отчёт на /ingest/report.
    Если в отчёте есть discovered_links — отправляет их отдельно на /ingest/links.
    """
    ok = _post("/ingest/report", report, fallback_type="report")

    # Отправляем ссылки отдельным запросом если есть
    discovered = report.get("discovered_links", [])
    if discovered:
        _send_links(report, discovered)

    return ok


def _send_links(report: dict, links: list) -> bool:
    """Отправляет найденные ссылки на /ingest/links (Вариант А по инструкции Сабины)."""
    if not links:
        return True

    payload = {
        "source_channel": report.get("channel"),
        "source_channel_id": report.get("channel_id"),
        "source_channel_username": report.get("channel_username"),
        "source_message_id": report.get("message_id"),
        "source_message_link": report.get("message_link"),
        "source_message_date": report.get("message_date"),
        "source_category": report.get("category"),
        "source_risk_score": report.get("risk_score"),
        "links": links,
    }
    return _post("/ingest/links", payload, fallback_type="discovered_links")


def send_mentions_snapshot() -> bool:
    """
    Сервер Сабины теперь сам считает mentions и stats —
    эта функция оставлена для совместимости но ничего не отправляет.
    """
    return True


def check_server_health() -> bool:
    """
    Проверяет доступность сервера при старте.
    GET /health -> {"status": "alive", "database": "ok"}
    """
    if not _check_server_url():
        return False

    url = SERVER_URL.rstrip("/") + "/health"
    try:
        response = requests.get(url, headers=_headers(), timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "alive" and data.get("database") == "ok":
                logger.info(f"Сервер доступен и база данных в порядке ✓")
                return True
            else:
                logger.warning(f"Сервер ответил но статус неожиданный: {data}")
                return False
        else:
            logger.warning(f"Health check вернул {response.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Сервер недоступен при старте: {e}")
        return False