"""
report_builder.py
Собирает внутренние report-словари (как их делает collector.py, с русскими
полями и русскими категориями) в формат QarmaqReport, который ожидает
сервер Сабины на /ingest (см. схему в Swagger /docs).

Внутренний report НЕ меняется — он по-прежнему нужен в исходном виде
stats_tracker.py, link_extractor.py и для логов. Перевод в формат сервера
происходит только здесь, перед отправкой.
"""

import re
from datetime import datetime, timezone

# Категории сервера. ВНИМАНИЕ: LOAN и SUSPICIOUS_JOB у нас нет —
# Groq никогда их не вернёт, т.к. они не описаны в groq_classifier.py.
CATEGORY_MAP = {
    "пирамида": "PYRAMID",
    "дроп": "DROP",
    "наркотики": "DRUG",
    "казино": "CASINO",
    "утечка_бд": "LEAK",
    "чистое": "CLEAN",
}

PHONE_RE = re.compile(r"^\+?\d[\d\-\s()]{7,14}\d$")


def map_category(category: str) -> str:
    return CATEGORY_MAP.get(category, "CLEAN")


def _split_contacts(contacts: list) -> tuple[list, list]:
    """extracted_contacts у нас — общий список @username + телефонов вперемешку.
    Серверу нужно раздельно phones / usernames."""
    phones, usernames = [], []
    for c in contacts or []:
        if c.startswith("@"):
            usernames.append(c)
        elif PHONE_RE.match(c):
            phones.append(c)
        else:
            usernames.append(c)
    return phones, usernames


def report_to_llm_result(report: dict) -> dict:
    """Один внутренний report -> один элемент all_llm_results / suspicious_messages (LLMResult)."""
    phones, usernames = _split_contacts(report.get("extracted_contacts"))
    return {
        "id": report.get("message_id"),
        "category": map_category(report.get("category", "чистое")),
        "risk_score": report.get("risk_score", 0.0),
        # ближайший аналог "red_flags" — какие категории подсветил префильтр
        "red_flags": report.get("prefilter_hints", []) or [],
        "extracted": {
            "phones": phones,
            "usernames": usernames,
            "amounts": [],  # не извлекаем суммы денег — можно добавить отдельно при необходимости
        },
        "summary": report.get("explanation"),
    }


class ChannelBatch:
    """
    Копит результаты анализа по одному каналу, чтобы в конце собрать единый
    QarmaqReport и отправить его одним запросом на /ingest.
    """

    def __init__(self, channel, project: str = "qarmaq", include_clean_in_results: bool = True):
        self.channel = channel
        self.project = project
        self.include_clean_in_results = include_clean_in_results
        self.total_messages = 0
        self.messages_analyzed = 0
        self.suspicious_found = 0
        self.errors = 0
        self.category_stats: dict = {}
        self.suspicious_messages: list = []
        self.all_llm_results: list = []

    def add_report(self, report: dict | None):
        """report=None означает пустое сообщение без текста — считаем его
        как 'увиденное', но не анализированное."""
        self.total_messages += 1
        if report is None:
            return

        self.messages_analyzed += 1
        llm_result = report_to_llm_result(report)
        is_suspicious = report.get("category") != "чистое"

        # category_stats считаем по всем проанализированным, независимо от фильтра
        cat = llm_result["category"]
        self.category_stats[cat] = self.category_stats.get(cat, 0) + 1

        if is_suspicious or self.include_clean_in_results:
            self.all_llm_results.append(llm_result)

        if is_suspicious:
            self.suspicious_found += 1
            self.suspicious_messages.append(llm_result)

    def add_error(self):
        self.total_messages += 1
        self.errors += 1

    def is_empty(self) -> bool:
        return self.total_messages == 0

    def has_results(self) -> bool:
        """Сервер отвергает батч, где нет ни all_llm_results, ни suspicious_messages."""
        return bool(self.all_llm_results or self.suspicious_messages)

    def build_payload(self, model: str | None = None) -> dict:
        channel_name = (
            getattr(self.channel, "title", None)
            or getattr(self.channel, "username", None)
            or str(getattr(self.channel, "id", "unknown"))
        )
        channel_tg_id = getattr(self.channel, "id", None)
        return {
            "meta": {
                "project": self.project,
                "channel": channel_name,
                "channel_tg_id": channel_tg_id,
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "total_messages": self.total_messages,
                "messages_analyzed": self.messages_analyzed,
                "suspicious_found": self.suspicious_found,
                "errors": self.errors,
                "model": model,
            },
            "category_stats": self.category_stats,
            "suspicious_messages": self.suspicious_messages,
            "all_llm_results": self.all_llm_results,
        }


def single_message_payload(report: dict, channel, project: str = "qarmaq", model: str | None = None, include_clean_in_results: bool = True) -> dict:
    """Для live-режима: один новый message -> мини-QarmaqReport из одного элемента."""
    batch = ChannelBatch(channel, project=project, include_clean_in_results=include_clean_in_results)
    batch.add_report(report)
    return batch.build_payload(model=model)