"""
groq_classifier.py
Финальная AI-классификация через Groq с встроенным rate limiter.

Rate limiter гарантирует не более MAX_RPM запросов в минуту —
скрипт работает медленнее но надёжно, без 429 ошибок.
"""

import os
import json
import re
import time
import logging
import threading
from collections import deque

from groq import Groq

logger = logging.getLogger("groq_classifier")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
CLEAN_THRESHOLD = float(os.environ.get("CLEAN_THRESHOLD", "0.5"))

# Бесплатный тариф Groq: 30 запросов/минуту, ставим 25 для запаса
MAX_RPM = int(os.environ.get("GROQ_MAX_RPM", "25"))

CATEGORIES = ["пирамида", "дроп", "наркотики", "казино", "утечка_бд", "чистое"]

_client = None

# --- Rate limiter ---
# Храним timestamps последних MAX_RPM запросов в скользящем окне 60 сек
_request_times: deque = deque()
_rate_lock = threading.Lock()


def _wait_for_slot():
    """
    Блокирует поток пока не освободится слот в окне 60 сек.
    Гарантирует не более MAX_RPM запросов в минуту.
    """
    while True:
        with _rate_lock:
            now = time.monotonic()
            # Убираем запросы старше 60 секунд
            while _request_times and now - _request_times[0] >= 60.0:
                _request_times.popleft()

            if len(_request_times) < MAX_RPM:
                # Слот есть — занимаем и выходим
                _request_times.append(now)
                return

            # Слотов нет — считаем сколько ждать
            oldest = _request_times[0]
            wait_sec = 60.0 - (now - oldest) + 0.1  # +0.1 запас

        logger.info(f"Rate limit: жду {wait_sec:.1f} сек до следующего Groq-запроса...")
        time.sleep(wait_sec)


def get_client() -> Groq:
    global _client
    if _client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY не задан в переменных окружения")
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


# --- Дневной лимит токенов (TPD) ---
# В отличие от RPM (запросы в минуту), это суточная квота. Если её исчерпали —
# быстрые ретраи (5/10/15 сек) бессмысленны, Groq просит ждать минуты.
# Поэтому при обнаружении такой ошибки ставим глобальную паузу до момента,
# который сам Groq указал в сообщении ("Please try again in Xm Ys").
_daily_limit_lock = threading.Lock()
_daily_limit_until: float = 0.0  # time.monotonic(), до которого не дёргаем Groq вообще

_RETRY_AFTER_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s")


def _parse_retry_after(error_message: str):
    """Парсит 'Please try again in 10m16.032s' -> секунды (float) или None."""
    m = _RETRY_AFTER_RE.search(error_message)
    if not m:
        return None
    minutes = float(m.group(1)) if m.group(1) else 0.0
    seconds = float(m.group(2))
    return minutes * 60 + seconds


def _is_daily_limit_error(error_message: str) -> bool:
    return "tokens per day" in error_message.lower() or "TPD" in error_message


def _set_daily_pause(wait_sec: float):
    global _daily_limit_until
    with _daily_limit_lock:
        _daily_limit_until = time.monotonic() + wait_sec


def _daily_pause_remaining() -> float:
    with _daily_limit_lock:
        return max(0.0, _daily_limit_until - time.monotonic())


SYSTEM_PROMPT = f"""Ты — модератор, анализирующий сообщения из Telegram-каналов на признаки
незаконной/мошеннической рекламы. Тебе присылают уже очищенный от эмодзи и
нормализованный текст (исходно он мог быть написан "стилизованными" символами
для обхода фильтров — учитывай это, текст может содержать остаточные опечатки
из-за нормализации).

Классифицируй сообщение СТРОГО в одну из категорий:
- "пирамида" — финансовая пирамида / схема пассивного дохода без реальной деятельности,
  обещания гарантированной прибыли, реферальные многоуровневые схемы
- "дроп" — поиск дропов, скупка/обнал банковских карт, сбор реквизитов для незаконных операций
- "наркотики" — реклама продажи психоактивных веществ (под любым эвфемизмом)
- "казино" — реклама нелицензированных онлайн-казино/ставок
- "утечка_бд" — продажа/слив персональных данных, "пробив" людей по номеру/паспорту
- "чистое" — обычное, не подозрительное сообщение

Оцени risk_score от 0.0 до 1.0 (коэффициент уверенности, что это
подозрительная/незаконная реклама):
- 0.0–0.5 — недостаточно явных признаков → категория должна быть "чистое"
- 0.5–0.75 — есть подозрительные признаки, но без явной прямой рекламы
- 0.75–1.0 — явная прямая реклама незаконной деятельности

Отвечай ТОЛЬКО валидным JSON, без markdown-разметки, без пояснений до или после,
в формате:
{{"category": "<одна из {CATEGORIES}>", "risk_score": <float 0.0-1.0>, "explanation": "<краткое объяснение на русском, 1-2 предложения>"}}
"""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


_SALVAGE_CATEGORY_RE = re.compile(r'"category"\s*:\s*"([^"]*)"')
_SALVAGE_RISK_RE = re.compile(r'"risk_score"\s*:\s*([\d.]+)')
_SALVAGE_EXPLANATION_RE = re.compile(r'"explanation"\s*:\s*"(.*?)"\s*[,}]', re.DOTALL)


def _salvage_json(text: str) -> dict | None:
    """
    Если json.loads не смог распарсить ответ модели (битый JSON — частая
    проблема у маленьких моделей типа llama-3.1-8b-instant, не экранирующих
    кавычки внутри строк), пытаемся вытащить нужные поля регулярками.
    Возвращает None, если не нашли хотя бы category.
    """
    cat_m = _SALVAGE_CATEGORY_RE.search(text)
    if not cat_m:
        return None
    risk_m = _SALVAGE_RISK_RE.search(text)
    expl_m = _SALVAGE_EXPLANATION_RE.search(text)
    return {
        "category": cat_m.group(1),
        "risk_score": float(risk_m.group(1)) if risk_m else 0.0,
        "explanation": expl_m.group(1) if expl_m else "(восстановлено из повреждённого JSON)",
    }


def classify_message(normalized_text: str, prefilter_hints=None, max_retries=2) -> dict:
    """
    Возвращает dict: {category, risk_score, explanation}
    Rate limiter гарантирует не более MAX_RPM запросов в минуту.
    Если уже стоит пауза из-за дневного лимита токенов (TPD) — сразу
    возвращает безопасный fallback без обращения к Groq.
    При ошибке — возвращает безопасный fallback с category="чистое".
    """
    remaining = _daily_pause_remaining()
    if remaining > 0:
        logger.warning(
            f"Дневной лимит токенов Groq ещё не освободился "
            f"(осталось ~{remaining/60:.1f} мин) — пропускаю AI-анализ"
        )
        return {
            "category": "чистое",
            "risk_score": 0.0,
            "explanation": "Пропущено: суточный лимит токенов Groq временно исчерпан.",
        }

    client = get_client()

    hints_note = ""
    if prefilter_hints:
        hints_note = f"\n(Подсказка от препроцессора: похоже на {', '.join(prefilter_hints)})"

    user_prompt = f"Текст сообщения:\n\"\"\"\n{normalized_text}\n\"\"\"{hints_note}"

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            # Ждём свободный слот перед каждой попыткой
            _wait_for_slot()

            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content
            cleaned = _strip_code_fences(raw)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as je:
                salvaged = _salvage_json(cleaned)
                if salvaged is None:
                    raise je
                logger.warning(f"JSON от модели был невалиден, восстановил вручную: {cleaned[:200]!r}")
                data = salvaged

            category = data.get("category", "чистое")
            risk_score = float(data.get("risk_score", 0.0))
            explanation = data.get("explanation", "")

            if category not in CATEGORIES:
                category = "чистое"
            risk_score = max(0.0, min(1.0, risk_score))

            if risk_score <= CLEAN_THRESHOLD:
                category = "чистое"

            return {
                "category": category,
                "risk_score": round(risk_score, 2),
                "explanation": explanation,
            }

        except Exception as e:
            last_error = e
            error_str = str(e)
            logger.warning(f"Groq classify attempt {attempt} failed: {e}")

            if _is_daily_limit_error(error_str):
                wait_sec = _parse_retry_after(error_str) or 600.0
                logger.error(
                    f"Суточный лимит токенов Groq исчерпан — ставлю паузу на "
                    f"{wait_sec/60:.1f} мин, дальше сообщения не будут анализироваться AI до этого момента"
                )
                _set_daily_pause(wait_sec)
                break  # бессмысленно ретраить дальше — лимит не RPM, а дневной

            # При обычной ошибке (не дневной лимит) ждём дольше перед retry
            time.sleep(5 * (attempt + 1))

    logger.error(f"Groq classify failed after retries: {last_error}")
    return {
        "category": "чистое",
        "risk_score": 0.0,
        "explanation": f"Ошибка анализа AI: {last_error}",
    }