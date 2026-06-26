import json
import networkx as nx

INPUT_JSON = "classified_topics.json"
OUTPUT_GEXF = "forum_graph.gexf"

# Загружаем данные
with open(INPUT_JSON, "r", encoding="utf-8") as f:
    posts = json.load(f)

# Создаем граф
G = nx.Graph()

for post in posts:

    title = str(post.get("title", "")).strip()
    author = str(post.get("author", "")).strip()
    last_user = str(post.get("last_user", "")).strip()
    intent = str(post.get("intent", "")).strip()

    categories = post.get("categories", [])
    url = str(post.get("url", "")).strip()

    # ---------- АВТОР ----------
    if author:
        G.add_node(
            f"author:{author}",
            label=author,
            node_type="author"
        )

    # ---------- ПОСЛЕДНИЙ ОТВЕТИВШИЙ ----------
    if last_user:
        G.add_node(
            f"user:{last_user}",
            label=last_user,
            node_type="user"
        )

    # ---------- ТЕМА ----------
    if title:
        G.add_node(
            f"topic:{title}",
            label=title,
            node_type="topic",
            url=url
        )

    # ---------- INTENT ----------
    if intent:
        G.add_node(
            f"intent:{intent}",
            label=intent,
            node_type="intent"
        )

    # ---------- СВЯЗИ ----------

    # Автор -> Тема
    if author and title:
        G.add_edge(
            f"author:{author}",
            f"topic:{title}",
            relation="created_topic"
        )

    # Последний ответивший -> Тема
    if last_user and title:
        G.add_edge(
            f"user:{last_user}",
            f"topic:{title}",
            relation="replied"
        )

    # Автор -> Последний ответивший
    if author and last_user:
        G.add_edge(
            f"author:{author}",
            f"user:{last_user}",
            relation="interaction"
        )

    # Тема -> Intent
    if title and intent:
        G.add_edge(
            f"topic:{title}",
            f"intent:{intent}",
            relation="intent"
        )

    # Категории
    for category in categories:

        category = str(category).strip()

        if not category:
            continue

        G.add_node(
            f"category:{category}",
            label=category,
            node_type="category"
        )

        if title:
            G.add_edge(
                f"topic:{title}",
                f"category:{category}",
                relation="category"
            )

        if author:
            G.add_edge(
                f"author:{author}",
                f"category:{category}",
                relation="interested_in"
            )

# Сохраняем граф
nx.write_gexf(G, OUTPUT_GEXF)

print("=" * 50)
print("Готово!")
print(f"Узлов: {G.number_of_nodes()}")
print(f"Связей: {G.number_of_edges()}")
print(f"Файл сохранён: {OUTPUT_GEXF}")
print("=" * 50)