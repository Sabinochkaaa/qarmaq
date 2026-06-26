import pandas as pd
import json
from collections import Counter
import os

print("Текущая папка:")
print(os.getcwd())

print("\nФайлы в папке:")
print(os.listdir())

CSV_FILE = "probiv-work-2026-06-25-3.csv"

CRYPTO_CATEGORIES = {
    "куплю базу данных казахстан": [
        "егов",
        "2025-2026 сливы",
        "2025 слив граждан"
    ],

    "продам базу данных": [
        "алматы",
        "казахстан",
        "свежые сливы в казахстане"
    ],

    "exchange": [
        "обменник",
        "обмен",
        "exchange"
    ],

    "mining": [
        "майнинг",
        "mining",
        "ферма"
    ],

    "wallet": [
        "кошелек",
        "кошелёк",
        "wallet"
    ],

    "p2p": [
        "p2p"
    ],

    "defi": [
        "defi"
    ],

    "nft": [
        "nft"
    ],

    "airdrop": [
        "airdrop"
    ]
}

def detect_crypto(text):
    text = str(text).lower()

    found = []

    for category, keywords in CRYPTO_CATEGORIES.items():

        for keyword in keywords:

            if keyword in text:
                found.append(category)
                break

    return found


df = pd.read_csv(CSV_FILE)

results = []

category_counter = Counter()

for _, row in df.iterrows():

    title = str(row.get("data", ""))
    description = str(row.get("data2", ""))

    full_text = f"{title} {description}"

    crypto_tags = detect_crypto(full_text)

    for tag in crypto_tags:
        category_counter[tag] += 1

    item = {
        "title": title,
        "description": description,
        "author": row.get("name"),
        "date": row.get("data3"),
        "forum": row.get("data4"),
        "search_url": row.get("web_scraper_start_url"),
        "crypto_tags": crypto_tags,
        "text_length": len(description)
    }

    results.append(item)

with open(
    "crypto_topics.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        results,
        f,
        ensure_ascii=False,
        indent=4
    )

stats = {
    "total_topics": len(results),
    "crypto_distribution": dict(category_counter)
}

with open(
    "crypto_statistics.json",
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
print(f"Тем найдено: {len(results)}")
print()

print("Криптовалюты и категории:")

for k, v in sorted(
        category_counter.items(),
        key=lambda x: x[1],
        reverse=True):

    print(f"{k}: {v}")

print()
print("Файлы созданы:")
print("crypto_topics.json")
print("crypto_statistics.json")
print("=" * 50)