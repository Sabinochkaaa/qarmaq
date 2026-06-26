import pandas as pd
import json
from collections import Counter

# ==========================
# НАСТРОЙКИ
# ==========================

CSV_FILE = r"legalizer-cc-2026-06-25-3.csv"

CARD_CATEGORIES = {
    "bank_cards": [
        "visa",
        "mastercard",
        "карта",
        "карточка",
        "дебетовая",
        "кредитная"
    ],

    "virtual_cards": [
        "виртуальная карта",
        "virtual card",
        "vcc"
    ],

    "gift_cards": [
        "gift card",
        "amazon gift",
        "steam gift",
        "подарочная карта"
    ],

    "bank_accounts": [
        "счет",
        "счёт",
        "банковский аккаунт",
        "банк аккаунт"
    ]
}

BUY_WORDS = [
    "куплю карту алматы",
    "ищу",
    "нужна",
    "нужны",
    "любая карточка казахстана",
    "покупка"
]

SELL_WORDS = [
    "продам",
    "продаю",
    "продажа"
]

# ==========================
# ФУНКЦИИ
# ==========================

def detect_intent(text):
    text = str(text).lower()

    for word in BUY_WORDS:
        if word in text:
            return "buy"

    for word in SELL_WORDS:
        if word in text:
            return "sell"

    return "other"


def detect_categories(text):
    text = str(text).lower()

    found = []

    for category, keywords in CARD_CATEGORIES.items():

        for keyword in keywords:

            if keyword in text:
                found.append(category)
                break

    return found


def clean_number(value):
    try:
        return int(str(value).replace(" ", "").replace(",", ""))
    except:
        return 0


# ==========================
# ЗАГРУЗКА CSV
# ==========================

df = pd.read_csv(CSV_FILE)

results = []

category_counter = Counter()
intent_counter = Counter()

# ==========================
# АНАЛИЗ
# ==========================

for _, row in df.iterrows():

    title = str(row.get("data", ""))

    categories = detect_categories(title)
    intent = detect_intent(title)

    for c in categories:
        category_counter[c] += 1

    intent_counter[intent] += 1

    item = {
        "title": title,
        "url": row.get("web_scraper_start_url"),
        "author": row.get("name"),
        "last_user": row.get("name2"),
        "created": row.get("data2"),
        "updated": row.get("data3"),
        "views": clean_number(row.get("data5")),
        "replies": clean_number(row.get("data6")),
        "intent": intent,
        "categories": categories
    }

    results.append(item)

# ==========================
# СОХРАНЕНИЕ ТЕМ
# ==========================

with open(
    "classified_topics.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        results,
        f,
        ensure_ascii=False,
        indent=4
    )

# ==========================
# СТАТИСТИКА
# ==========================

stats = {
    "total_topics": len(results),
    "categories": dict(category_counter),
    "intent_distribution": dict(intent_counter)
}

with open(
    "forum_statistics.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        stats,
        f,
        ensure_ascii=False,
        indent=4
    )

print("=" * 50)
print("ГОТОВО")
print(f"Всего тем: {len(results)}")
print("")

print("Категории:")

for k, v in category_counter.items():
    print(f"{k}: {v}")

print("")
print("Типы тем:")

for k, v in intent_counter.items():
    print(f"{k}: {v}")

print("")
print("Файлы созданы:")
print("classified_topics.json")
print("forum_statistics.json")
print("=" * 50)