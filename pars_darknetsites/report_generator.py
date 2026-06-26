import json
import os

HTML_FILE = "report.html"

all_posts = []

# ==========================
# Загружаем первый JSON
# ==========================

try:
    with open(
        "classified_topics.json",
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

        for item in data:
            item["source"] = "Legalizer"

        all_posts.extend(data)

except Exception as e:
    print("Не найден classified_topics.json")
    print(e)

# ==========================
# Загружаем второй JSON
# ==========================

try:
    with open(
        "crypto_topics.json",
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

        for item in data:
            item["source"] = "Probiv"

        all_posts.extend(data)

except Exception as e:
    print("Не найден crypto_topics.json")
    print(e)

# ==========================
# Картинки
# ==========================

images_html = ""

if os.path.exists("images"):

    for img in os.listdir("images"):

        if img.lower().endswith(
            (
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp"
            )
        ):

            images_html += f"""
            <img src="images/{img}">
            """

# ==========================
# HTML
# ==========================

html = f"""
<html>

<head>

<meta charset="utf-8">

<title>Crypto Investigation Report</title>

<style>

body{{
    font-family:Arial;
    background:#f5f5f5;
    margin:20px;
}}

.card{{
    background:white;
    border-radius:10px;
    padding:15px;
    margin-bottom:20px;
    box-shadow:0px 0px 10px rgba(0,0,0,0.1);
}}

img{{
    max-width:350px;
    margin:10px;
}}

.tag{{
    display:inline-block;
    padding:5px 10px;
    background:#eee;
    border-radius:5px;
    margin:3px;
}}

</style>

</head>

<body>

<h1>Forum Intelligence Report</h1>

<p>
Всего записей: {len(all_posts)}
</p>

<h2>Скриншоты</h2>

{images_html}

<hr>

"""

# ==========================
# Карточки
# ==========================

for item in all_posts:

    title = item.get("title", "")

    author = item.get("author", "")

    date = item.get("date") \
        or item.get("created") \
        or ""

    source = item.get("source", "")

    description = item.get("description", "")

    url = (
        item.get("topic_url")
        or item.get("url")
        or item.get("search_url")
        or item.get("web_scraper_start_url")
        or "#"
    )

    tags = []

    if "categories" in item:
        tags.extend(item["categories"])

    if "crypto_tags" in item:
        tags.extend(item["crypto_tags"])

    tags_html = ""

    for tag in tags:
        tags_html += f"""
        <span class="tag">{tag}</span>
        """

    html += f"""

    <div class="card">

        <h2>{title}</h2>

        <b>Источник:</b> {source}<br>

        <b>Автор:</b> {author}<br>

        <b>Дата:</b> {date}<br><br>

        {tags_html}

        <br><br>

        <b>Описание:</b><br>
        {description}

        <br><br>

        <a href="{url}" target="_blank">
        Открыть тему
        </a>

    </div>

    """

html += """
</body>
</html>
"""

with open(
    HTML_FILE,
    "w",
    encoding="utf-8"
) as f:

    f.write(html)

print("=" * 50)
print("ГОТОВО")
print("Создан report.html")
print("=" * 50)