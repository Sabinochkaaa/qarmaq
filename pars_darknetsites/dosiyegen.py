import json

with open("crypto_topics.json", "r", encoding="utf-8") as f:
    data = json.load(f)

html = """
<html>
<head>
<meta charset="utf-8">
<title>Crypto Dossier</title>

<style>

body{
    font-family: Arial;
    background:#f5f5f5;
    margin:20px;
}

.card{
    background:white;
    border-radius:10px;
    padding:15px;
    margin-bottom:20px;
    box-shadow:0 0 10px rgba(0,0,0,0.1);
}

img{
    max-width:300px;
    margin-top:10px;
}

</style>

</head>
<body>

<h1>Crypto Dossier</h1>

"""

for item in data:

    html += f"""
    <div class="card">

    <h2>{item.get("title","")}</h2>

    <b>Автор:</b> {item.get("author","")}<br>

    <b>Дата:</b> {item.get("date","")}<br>

    <b>Категории:</b> {", ".join(item.get("crypto_tags",[]))}<br>

    <b>Описание:</b><br>
    {item.get("description","")}<br><br>

    </div>
    """

html += """
</body>
</html>
"""

with open(
    "report.html",
    "w",
    encoding="utf-8"
) as f:
    f.write(html)

print("Создан report.html")