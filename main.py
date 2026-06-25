import os
import re
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("qarmaq")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не задан! Создай файл .env и укажи строку подключения к Supabase."
    )

INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")


# ─── НОРМАЛИЗАЦИЯ КАТЕГОРИЙ ──────────────────────────────────────────

CATEGORY_MAP = {
    "наркотики": "DRUG",
    "дроп": "DROP",
    "пирамида": "PYRAMID",
    "казино": "CASINO",
    "утечка_бд": "LEAK",
    "утечка": "LEAK",
    "займ": "LOAN",
    "вакансия": "SUSPICIOUS_JOB",
    "чисто": "CLEAN",
}

KNOWN_CATEGORIES = {
    "DRUG", "DROP", "PYRAMID", "CASINO", "LEAK", "LOAN", "SUSPICIOUS_JOB", "CLEAN"
}


def normalize_category(raw: Optional[str]) -> str:
    if not raw:
        return "CLEAN"
    mapped = CATEGORY_MAP.get(raw.lower())
    if mapped:
        return mapped
    upper = raw.upper()
    return upper  # неизвестные категории сохраняем как есть


# ─── ОПРЕДЕЛЕНИЕ СЕТИ КОШЕЛЬКА ───────────────────────────────────────

_ETH_RE  = re.compile(r'^0x[0-9a-fA-F]{40}$')
_TRX_RE  = re.compile(r'^T[0-9a-zA-Z]{33}$')
_BTC_RE  = re.compile(r'^(1|3|bc1)[0-9a-zA-Z]{25,62}$')
_SOL_RE  = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def detect_chain(address: str) -> Optional[str]:
    if _ETH_RE.match(address):
        return "ETH"
    if _TRX_RE.match(address):
        return "TRX"
    if _BTC_RE.match(address):
        return "BTC"
    # SOL определяем последним — паттерн широкий
    if _SOL_RE.match(address):
        return "SOL"
    return None


# ─── ПАРСИНГ ВРЕМЕНИ ────────────────────────────────────────────────

def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def utcnow() -> datetime:
    """datetime.utcnow() устарел в Python 3.12 — используем этот хелпер."""
    return datetime.now(timezone.utc)


# ─── ПУЛ СОЕДИНЕНИЙ С БД ────────────────────────────────────────────

pool: Optional[asyncpg.Pool] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    logger.info("Подключаюсь к базе данных...")
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    logger.info("✅ Пул соединений создан")
    yield
    await pool.close()
    logger.info("Пул соединений закрыт")


app = FastAPI(title="QARMAQ Ingest API v2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── МОДЕЛИ ──────────────────────────────────────────────────────────

class DiscoveredLink(BaseModel):
    target: Optional[str] = None
    link_type: str
    discovered_url: Optional[str] = None
    button_text: Optional[str] = None

    @property
    def target_or_url(self) -> str:
        return self.target or self.discovered_url or "unknown"


class ReportEvent(BaseModel):
    """Тип 'report' — один проанализированный пост."""
    channel: str
    channel_id: int
    channel_username: Optional[str] = None

    message_id: int
    message_link: Optional[str] = None
    message_date: Optional[str] = None

    sender_id: Optional[int] = None
    sender_type: Optional[str] = None

    raw_text: Optional[str] = None
    normalized_text: Optional[str] = None

    category: str
    risk_score: float = 0.0
    explanation: Optional[str] = None

    extracted_contacts: list[Any] = []
    prefilter_hints: list[str] = []
    prefilter_candidate: Optional[bool] = None

    analyzed_at: Optional[str] = None
    model: Optional[str] = None

    discovered_links: list[DiscoveredLink] = []

    # НОВОЕ: Аружан может передавать найденные кошельки явно
    wallet_addresses: list[str] = []

    @field_validator("prefilter_hints", "extracted_contacts", "wallet_addresses", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v if v is not None else []


class DiscoveredLinksEvent(BaseModel):
    source_channel: str
    source_channel_id: int
    source_channel_username: Optional[str] = None
    source_message_id: Optional[int] = None
    source_message_link: Optional[str] = None
    source_message_date: Optional[str] = None
    source_category: Optional[str] = None
    source_risk_score: Optional[float] = None
    found_at: Optional[str] = None

    links: list[DiscoveredLink] = []

    # flat-формат (discovered_links.jsonl)
    target: Optional[str] = None
    link_type: Optional[str] = None
    discovered_url: Optional[str] = None
    button_text: Optional[str] = None

    def resolved_links(self) -> list[DiscoveredLink]:
        if self.links:
            return self.links
        if self.link_type and (self.target or self.discovered_url):
            return [DiscoveredLink(
                target=self.target,
                link_type=self.link_type,
                discovered_url=self.discovered_url,
                button_text=self.button_text,
            )]
        return []


# ─── Старый батч-формат ──────────────────────────────────────────────

class ExtractedData(BaseModel):
    phones: list[str] = []
    usernames: list[str] = []
    amounts: list[Any] = []

    @field_validator("phones", "usernames", "amounts", mode="before")
    @classmethod
    def _drop_none(cls, v):
        if v is None:
            return []
        return [x for x in v if x is not None]


class LLMResult(BaseModel):
    id: int
    category: str
    risk_score: float = 0.0
    red_flags: list[str] = []
    extracted: ExtractedData = ExtractedData()
    summary: Optional[str] = None

    @field_validator("red_flags", mode="before")
    @classmethod
    def _drop_none_flags(cls, v):
        return [x for x in v if x] if v else []


class LegacyReportMeta(BaseModel):
    project: str
    channel: str
    channel_tg_id: Optional[int] = None
    analyzed_at: str
    total_messages: int = 0
    messages_analyzed: int = 0
    suspicious_found: int = 0
    errors: int = 0
    model: Optional[str] = None


class LegacyQarmaqReport(BaseModel):
    meta: LegacyReportMeta
    category_stats: dict[str, int] = {}
    suspicious_messages: list[LLMResult] = []
    all_llm_results: list[LLMResult] = []


# ─── ТОКЕН ───────────────────────────────────────────────────────────

def check_token(request: Request):
    if not INGEST_TOKEN:
        return
    provided = request.headers.get("X-Ingest-Token", "")
    if provided != INGEST_TOKEN:
        raise HTTPException(status_code=401, detail="Неверный или отсутствующий X-Ingest-Token")


# ─── ВНУТРЕННИЕ ФУНКЦИИ ЗАПИСИ В БД ──────────────────────────────────

async def upsert_channel(conn, name: str, tg_id: int, username: Optional[str] = None) -> int:
    return await conn.fetchval(
        """
        insert into channels (name, tg_id, username, last_seen)
        values ($1, $2, $3, now())
        on conflict (tg_id) do update
            set name = excluded.name,
                username = coalesce(excluded.username, channels.username),
                last_seen = now()
        returning id
        """,
        name, tg_id, username,
    )


async def insert_post(conn, channel_id: int, ev: ReportEvent) -> int:
    category_norm = normalize_category(ev.category)
    post_id = await conn.fetchval(
        """
        insert into posts
            (channel_id, message_id, message_link, message_date,
             sender_id, sender_type, raw_text, normalized_text,
             category, category_raw, risk_score, explanation,
             prefilter_hints, analyzed_at, model)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        on conflict (channel_id, message_id) do update
            set risk_score   = excluded.risk_score,
                category     = excluded.category,
                category_raw = excluded.category_raw,
                explanation  = excluded.explanation
        returning id
        """,
        channel_id, ev.message_id, ev.message_link, parse_dt(ev.message_date),
        ev.sender_id, ev.sender_type, ev.raw_text, ev.normalized_text,
        category_norm, ev.category, ev.risk_score, ev.explanation,
        ev.prefilter_hints or [], parse_dt(ev.analyzed_at) or utcnow(), ev.model,
    )
    return post_id


async def insert_links(
    conn,
    source_channel_id: int,
    source_post_id: Optional[int],
    links: list[DiscoveredLink],
) -> int:
    saved = 0
    for link in links:
        target = link.target_or_url
        if target == "unknown":
            continue  # не засоряем граф бессмысленными узлами

        if source_post_id is not None:
            # NULL-safe уникальность: есть пост → используем тройку
            await conn.execute(
                """
                insert into channel_links
                    (source_channel_id, source_post_id, target, link_type, discovered_url, button_text)
                values ($1, $2, $3, $4, $5, $6)
                on conflict (source_channel_id, source_post_id, target)
                where source_post_id is not null
                do nothing
                """,
                source_channel_id, source_post_id, target,
                link.link_type, link.discovered_url, link.button_text,
            )
        else:
            # нет поста → дедупликация по (channel_id, target)
            await conn.execute(
                """
                insert into channel_links
                    (source_channel_id, source_post_id, target, link_type, discovered_url, button_text)
                values ($1, null, $2, $3, $4, $5)
                on conflict (source_channel_id, target)
                where source_post_id is null
                do nothing
                """,
                source_channel_id, target,
                link.link_type, link.discovered_url, link.button_text,
            )
        saved += 1
    return saved


async def upsert_wallets(conn, post_id: int, addresses: list[str]) -> int:
    """Сохраняет кошельки из поста и привязывает к нему через wallet_mentions."""
    saved = 0
    for addr in addresses:
        addr = addr.strip()
        if not addr:
            continue
        chain = detect_chain(addr)
        wallet_id = await conn.fetchval(
            """
            insert into crypto_wallets (address, chain, last_seen)
            values ($1, $2, now())
            on conflict (address) do update
                set last_seen = now(),
                    chain = coalesce(excluded.chain, crypto_wallets.chain)
            returning id
            """,
            addr, chain,
        )
        await conn.execute(
            """
            insert into wallet_mentions (wallet_id, post_id)
            values ($1, $2)
            on conflict (wallet_id, post_id) do nothing
            """,
            wallet_id, post_id,
        )
        saved += 1
    return saved


async def link_post_articles(conn, post_id: int, category: str):
    """Привязывает пост к релевантным статьям РК по его категории."""
    article_ids = await conn.fetch(
        "select id from legal_articles where $1 = any(categories)",
        category,
    )
    for row in article_ids:
        await conn.execute(
            """
            insert into post_articles (post_id, article_id)
            values ($1, $2)
            on conflict (post_id, article_id) do nothing
            """,
            post_id, row["id"],
        )


# ─── ЭНДПОИНТЫ: ПРИЁМ ДАННЫХ ─────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("select 1")
        db_ok = True
    except Exception as e:
        db_ok = False
        logger.error(f"Health check DB error: {e}")
    return {"status": "alive", "service": "QARMAQ", "database": "ok" if db_ok else "error"}


@app.post("/ingest/report")
async def ingest_report(ev: ReportEvent, request: Request):
    """Принимает один проанализированный пост (тип 'report' у Аружан)."""
    check_token(request)

    async with pool.acquire() as conn:
        async with conn.transaction():
            channel_id = await upsert_channel(conn, ev.channel, ev.channel_id, ev.channel_username)
            post_id = await insert_post(conn, channel_id, ev)

            links_saved = 0
            if ev.discovered_links:
                links_saved = await insert_links(conn, channel_id, post_id, ev.discovered_links)

            wallets_saved = 0
            if ev.wallet_addresses:
                wallets_saved = await upsert_wallets(conn, post_id, ev.wallet_addresses)

            # Автоматически привязываем к статьям УК/КоАП
            category_norm = normalize_category(ev.category)
            if category_norm != "CLEAN":
                await link_post_articles(conn, post_id, category_norm)

    return {
        "status": "success",
        "channel": ev.channel,
        "post_id": post_id,
        "links_saved": links_saved,
        "wallets_saved": wallets_saved,
    }


@app.post("/ingest/links")
async def ingest_links(ev: DiscoveredLinksEvent, request: Request):
    """Принимает связи, найденные в подозрительном посте."""
    check_token(request)
    links = ev.resolved_links()
    if not links:
        return {"status": "success", "links_saved": 0}

    async with pool.acquire() as conn:
        async with conn.transaction():
            channel_id = await upsert_channel(
                conn, ev.source_channel, ev.source_channel_id, ev.source_channel_username
            )
            source_post_id = None
            if ev.source_message_id is not None:
                source_post_id = await conn.fetchval(
                    "select id from posts where channel_id = $1 and message_id = $2",
                    channel_id, ev.source_message_id,
                )
            links_saved = await insert_links(conn, channel_id, source_post_id, links)

    return {
        "status": "success",
        "source_channel": ev.source_channel,
        "links_saved": links_saved,
    }


@app.post("/ingest")
async def ingest_legacy(report: LegacyQarmaqReport, request: Request):
    """Принимает старый батч-формат qarmaq_groq.py ('qarmaq_report')."""
    check_token(request)

    analyzed_at = parse_dt(report.meta.analyzed_at) or utcnow()
    results = report.all_llm_results or report.suspicious_messages
    if not results:
        raise HTTPException(
            status_code=400,
            detail="Отчёт пуст: нет ни all_llm_results, ни suspicious_messages"
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            tg_id = report.meta.channel_tg_id
            if tg_id is None:
                tg_id = -abs(hash(report.meta.channel)) % 2_147_483_647

            channel_id = await upsert_channel(conn, report.meta.channel, tg_id)

            posts_saved = 0
            flags_saved = 0
            for item in results:
                category_norm = normalize_category(item.category)
                post_id = await conn.fetchval(
                    """
                    insert into posts
                        (channel_id, message_id, category, category_raw,
                         risk_score, explanation, analyzed_at, model)
                    values ($1, $2, $3, $4, $5, $6, $7, $8)
                    on conflict (channel_id, message_id) do update
                        set risk_score = excluded.risk_score,
                            category = excluded.category
                    returning id
                    """,
                    channel_id, item.id, category_norm, item.category,
                    item.risk_score, item.summary, analyzed_at, report.meta.model,
                )
                posts_saved += 1

                if category_norm != "CLEAN":
                    await link_post_articles(conn, post_id, category_norm)

                flag_rows = [(post_id, f.strip()) for f in item.red_flags if f.strip()]
                if flag_rows:
                    await conn.executemany(
                        "insert into red_flags (post_id, flag_text) values ($1, $2)",
                        flag_rows,
                    )
                    flags_saved += len(flag_rows)

    return {
        "status": "success",
        "channel": report.meta.channel,
        "posts_saved": posts_saved,
        "flags_saved": flags_saved,
    }


# ─── ЭНДПОИНТЫ: ЧТЕНИЕ ───────────────────────────────────────────────

@app.get("/channels")
async def list_channels():
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select c.id, c.name, c.tg_id, c.username,
                   count(distinct p.id)                                     as posts_count,
                   count(distinct p.id) filter (where p.is_suspicious)      as suspicious_count,
                   count(distinct cl.id)                                    as links_found,
                   max(p.risk_score)                                        as max_risk_score,
                   max(p.analyzed_at)                                       as last_analyzed
            from channels c
            left join posts p  on p.channel_id = c.id
            left join channel_links cl on cl.source_channel_id = c.id
            group by c.id
            order by suspicious_count desc, max_risk_score desc nulls last
            """
        )
        return [dict(r) for r in rows]


@app.get("/channel/{tg_id}/dossier")
async def channel_dossier(tg_id: int):
    """
    Полное досье по каналу/магазину:
    метаданные + статистика по категориям + последние подозрительные посты
    + связанные боты/каналы + упоминаемые кошельки.
    """
    async with pool.acquire() as conn:
        channel = await conn.fetchrow(
            "select * from channels where tg_id = $1", tg_id
        )
        if not channel:
            raise HTTPException(status_code=404, detail="Канал не найден")

        cid = channel["id"]

        stats = await conn.fetchrow(
            """
            select
                count(*) filter (where is_suspicious)        as suspicious_count,
                count(*)                                     as total_posts,
                avg(risk_score) filter (where is_suspicious) as avg_risk_score,
                max(risk_score)                              as max_risk_score,
                max(analyzed_at)                             as last_seen
            from posts where channel_id = $1
            """,
            cid,
        )

        by_category = await conn.fetch(
            """
            select category, count(*) as cnt
            from posts where channel_id = $1 and is_suspicious
            group by category order by cnt desc
            """,
            cid,
        )

        recent_posts = await conn.fetch(
            """
            select id, message_id, message_link, message_date, category,
                   risk_score, explanation, raw_text
            from posts
            where channel_id = $1 and is_suspicious
            order by risk_score desc, analyzed_at desc
            limit 20
            """,
            cid,
        )

        links = await conn.fetch(
            """
            select target, link_type, discovered_url, count(*) as mentions
            from channel_links
            where source_channel_id = $1
            group by target, link_type, discovered_url
            order by mentions desc
            """,
            cid,
        )

        wallets = await conn.fetch(
            """
            select distinct cw.address, cw.chain, cw.risk_label, cw.total_volume_usd
            from wallet_mentions wm
            join crypto_wallets cw on cw.id = wm.wallet_id
            join posts p on p.id = wm.post_id
            where p.channel_id = $1
            """,
            cid,
        )

        articles = await conn.fetch(
            """
            select distinct la.code, la.article_number, la.title, la.max_penalty, la.adilet_url
            from post_articles pa
            join legal_articles la on la.id = pa.article_id
            join posts p on p.id = pa.post_id
            where p.channel_id = $1
            order by la.code, la.article_number
            """,
            cid,
        )

        return {
            "channel": dict(channel),
            "stats": dict(stats),
            "by_category": {r["category"]: r["cnt"] for r in by_category},
            "recent_suspicious_posts": [dict(r) for r in recent_posts],
            "linked_targets": [dict(r) for r in links],
            "crypto_wallets": [dict(r) for r in wallets],
            "applicable_articles": [dict(r) for r in articles],
        }


@app.get("/suspicious")
async def list_suspicious(
    limit: int = 50,
    offset: int = 0,
    category: Optional[str] = None,
    channel_tg_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Подозрительные посты с фильтрами по категории, каналу и диапазону дат."""
    filters = ["p.is_suspicious"]
    params: list[Any] = []
    idx = 1

    if category:
        filters.append(f"p.category = ${idx}")
        params.append(category.upper())
        idx += 1
    if channel_tg_id:
        filters.append(f"c.tg_id = ${idx}")
        params.append(channel_tg_id)
        idx += 1
    if date_from:
        dt = parse_dt(date_from)
        if dt:
            filters.append(f"p.message_date >= ${idx}")
            params.append(dt)
            idx += 1
    if date_to:
        dt = parse_dt(date_to)
        if dt:
            filters.append(f"p.message_date <= ${idx}")
            params.append(dt)
            idx += 1

    where = "where " + " and ".join(filters)
    params += [limit, offset]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            select p.*, c.name as channel_name, c.tg_id as channel_tg_id
            from posts p
            join channels c on c.id = p.channel_id
            {where}
            order by p.risk_score desc
            limit ${idx} offset ${idx + 1}
            """,
            *params,
        )
        return [dict(r) for r in rows]


@app.get("/all")
async def list_all(
    limit: int = 200,
    offset: int = 0,
    category: Optional[str] = None,
    channel_tg_id: Optional[int] = None,
):
    filters = []
    params: list[Any] = []
    idx = 1

    if category:
        filters.append(f"p.category = ${idx}")
        params.append(category.upper())
        idx += 1
    if channel_tg_id:
        filters.append(f"c.tg_id = ${idx}")
        params.append(channel_tg_id)
        idx += 1

    where = ("where " + " and ".join(filters)) if filters else ""
    params += [limit, offset]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            select p.*, c.name as channel_name, c.tg_id as channel_tg_id
            from posts p
            join channels c on c.id = p.channel_id
            {where}
            order by p.risk_score desc
            limit ${idx} offset ${idx + 1}
            """,
            *params,
        )
        return [dict(r) for r in rows]


@app.get("/wallet/{address}")
async def wallet_dossier(address: str):
    """
    Досье по криптокошельку:
    метаданные + все посты где упоминался + все каналы.
    Интеграция с блокчейн-эксплорером — на стороне Расула.
    """
    async with pool.acquire() as conn:
        wallet = await conn.fetchrow(
            "select * from crypto_wallets where address = $1", address
        )
        if not wallet:
            raise HTTPException(status_code=404, detail="Кошелёк не найден в базе")

        posts = await conn.fetch(
            """
            select p.id, p.message_id, p.message_link, p.message_date,
                   p.category, p.risk_score, p.explanation,
                   c.name as channel_name, c.tg_id as channel_tg_id, c.username as channel_username
            from wallet_mentions wm
            join posts p on p.id = wm.post_id
            join channels c on c.id = p.channel_id
            where wm.wallet_id = $1
            order by p.risk_score desc, p.message_date desc
            """,
            wallet["id"],
        )

        # Список каналов, в которых упоминался кошелёк
        channels = {}
        for p in posts:
            tg_id = p["channel_tg_id"]
            if tg_id not in channels:
                channels[tg_id] = {
                    "tg_id": tg_id,
                    "name": p["channel_name"],
                    "username": p["channel_username"],
                    "mention_count": 0,
                }
            channels[tg_id]["mention_count"] += 1

        return {
            "wallet": dict(wallet),
            "mention_count": len(posts),
            "channels": list(channels.values()),
            "posts": [dict(p) for p in posts],
            # Расул заполняет это из блокчейн-эксплорера отдельным PATCH:
            "blockchain_data": {
                "tx_count": wallet["tx_count"],
                "total_volume_usd": wallet["total_volume_usd"],
                "risk_label": wallet["risk_label"],
                "explorer_url": _explorer_url(wallet["address"], wallet["chain"]),
            },
        }


def _explorer_url(address: str, chain: Optional[str]) -> Optional[str]:
    """Ссылка на блокчейн-эксплорер для кошелька."""
    urls = {
        "ETH": f"https://etherscan.io/address/{address}",
        "TRX": f"https://tronscan.org/#/address/{address}",
        "BTC": f"https://blockchair.com/bitcoin/address/{address}",
        "SOL": f"https://solscan.io/account/{address}",
    }
    return urls.get(chain) if chain else None


@app.patch("/wallet/{address}/blockchain")
async def update_wallet_blockchain(address: str, request: Request):
    """
    Расул обновляет данные блокчейн-эксплорера для кошелька.
    Body: { "tx_count": int, "total_volume_usd": float, "risk_label": str }
    """
    check_token(request)
    body = await request.json()
    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            update crypto_wallets
            set tx_count         = coalesce($2, tx_count),
                total_volume_usd = coalesce($3, total_volume_usd),
                risk_label       = coalesce($4, risk_label)
            where address = $1
            returning *
            """,
            address,
            body.get("tx_count"),
            body.get("total_volume_usd"),
            body.get("risk_label"),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Кошелёк не найден")
        return {"status": "updated", "wallet": dict(updated)}


@app.get("/wallets")
async def list_wallets(limit: int = 50, chain: Optional[str] = None):
    """Список всех криптокошельков, найденных в постах."""
    async with pool.acquire() as conn:
        if chain:
            rows = await conn.fetch(
                """
                select cw.*, count(wm.post_id) as mention_count
                from crypto_wallets cw
                left join wallet_mentions wm on wm.wallet_id = cw.id
                where cw.chain = $1
                group by cw.id
                order by mention_count desc
                limit $2
                """,
                chain.upper(), limit,
            )
        else:
            rows = await conn.fetch(
                """
                select cw.*, count(wm.post_id) as mention_count
                from crypto_wallets cw
                left join wallet_mentions wm on wm.wallet_id = cw.id
                group by cw.id
                order by mention_count desc
                limit $1
                """,
                limit,
            )
        return [dict(r) for r in rows]


@app.get("/legal/articles")
async def list_articles():
    """Справочник статей УК/КоАП РК, используемых в системе."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select * from legal_articles order by code, article_number"
        )
        return [dict(r) for r in rows]


@app.get("/legal/by-category/{category}")
async def articles_by_category(category: str):
    """Статьи РК, применимые к данной категории нарушения (DRUG, DROP, и т.д.)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select * from legal_articles where $1 = any(categories) order by code, article_number",
            category.upper(),
        )
        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"Нет статей для категории '{category.upper()}'"
            )
        return [dict(r) for r in rows]


@app.get("/graph")
async def get_graph():
    """Узлы и рёбра для графа связей между каналами (NetworkX/vis.js у Расула)."""
    async with pool.acquire() as conn:
        nodes = await conn.fetch("select id, name, tg_id, username from channels")
        edges = await conn.fetch("select * from channel_graph")
        return {
            "nodes": [dict(n) for n in nodes],
            "edges": [dict(e) for e in edges],
        }


@app.get("/mentions")
async def get_mentions(limit: int = 20):
    """Топ упоминаемых ботов/каналов (вместо channel_mentions.json у Аружан)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("select * from mentions_summary limit $1", limit)
        return [dict(r) for r in rows]


@app.get("/stats")
async def get_stats():
    """Сводная статистика по всей базе."""
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            select
                count(distinct c.id)                                         as channels_monitored,
                count(distinct p.id)                                         as messages_total,
                count(distinct p.id) filter (where p.is_suspicious)         as messages_suspicious
            from channels c
            left join posts p on p.channel_id = c.id
            """
        )
        by_category = await conn.fetch(
            """
            select category, count(*) as cnt
            from posts where is_suspicious
            group by category order by cnt desc
            """
        )
        links_total  = await conn.fetchval("select count(*) from channel_links")
        wallets_total = await conn.fetchval("select count(*) from crypto_wallets")

        msgs_total     = totals["messages_total"] or 0
        msgs_suspicious = totals["messages_suspicious"] or 0

        return {
            "channels_monitored":    totals["channels_monitored"],
            "messages_total":        msgs_total,
            "messages_suspicious":   msgs_suspicious,
            "suspicious_percent":    round(100 * msgs_suspicious / msgs_total, 2) if msgs_total else 0,
            "by_category":           {r["category"]: r["cnt"] for r in by_category},
            "discovered_links_total": links_total,
            "crypto_wallets_total":  wallets_total,
        }


@app.get("/export/arujan")
async def export_for_arujan(
    channel_tg_ids: Optional[str] = Query(
        None,
        description="Comma-separated list of channel tg_ids. If omitted — all channels."
    ),
    limit: int = 500,
):
    """
    Выгрузка данных в формате, совместимом с пайплайном Аружан.
    Аружан может передать конкретные channel_tg_ids через запятую.
    Возвращает структуру, аналогичную reports_fallback.jsonl (тип 'report').
    """
    async with pool.acquire() as conn:
        if channel_tg_ids:
            ids = [int(x.strip()) for x in channel_tg_ids.split(",") if x.strip().lstrip("-").isdigit()]
            rows = await conn.fetch(
                """
                select p.*, c.name as channel, c.tg_id as channel_id, c.username as channel_username,
                       coalesce(
                           (select array_agg(cw.address)
                            from wallet_mentions wm join crypto_wallets cw on cw.id = wm.wallet_id
                            where wm.post_id = p.id),
                           '{}'
                       ) as wallet_addresses
                from posts p
                join channels c on c.id = p.channel_id
                where c.tg_id = any($1::bigint[])
                order by p.risk_score desc
                limit $2
                """,
                ids, limit,
            )
        else:
            rows = await conn.fetch(
                """
                select p.*, c.name as channel, c.tg_id as channel_id, c.username as channel_username,
                       coalesce(
                           (select array_agg(cw.address)
                            from wallet_mentions wm join crypto_wallets cw on cw.id = wm.wallet_id
                            where wm.post_id = p.id),
                           '{}'
                       ) as wallet_addresses
                from posts p
                join channels c on c.id = p.channel_id
                order by p.risk_score desc
                limit $1
                """,
                limit,
            )

        # Форматируем в поля, которые Аружан знает
        results = []
        for r in rows:
            d = dict(r)
            results.append({
                "type":              "report",
                "channel":           d["channel"],
                "channel_id":        d["channel_id"],
                "channel_username":  d["channel_username"],
                "message_id":        d["message_id"],
                "message_link":      d["message_link"],
                "message_date":      d["message_date"].isoformat() if d["message_date"] else None,
                "sender_id":         d["sender_id"],
                "sender_type":       d["sender_type"],
                "raw_text":          d["raw_text"],
                "normalized_text":   d["normalized_text"],
                "category":          d["category"],
                "risk_score":        d["risk_score"],
                "explanation":       d["explanation"],
                "prefilter_hints":   d["prefilter_hints"] or [],
                "is_suspicious":     d["is_suspicious"],
                "analyzed_at":       d["analyzed_at"].isoformat() if d["analyzed_at"] else None,
                "model":             d["model"],
                "wallet_addresses":  list(d["wallet_addresses"] or []),
            })

        return {"count": len(results), "reports": results}


# ─── ЗАПУСК ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  QARMAQ Ingest Server v2")
    print("  http://0.0.0.0:8000")
    print()
    print("  ПРИЁМ ДАННЫХ (POST):")
    print("  /ingest/report     — один пост от Аружан")
    print("  /ingest/links      — найденные ссылки")
    print("  /ingest            — батч-формат qarmaq_groq.py")
    print()
    print("  ЧТЕНИЕ (GET):")
    print("  /health            — проверка живости")
    print("  /channels          — список каналов")
    print("  /channel/{tg_id}/dossier  — досье по каналу/магазину")
    print("  /suspicious        — подозрительные посты (с фильтрами)")
    print("  /all               — все посты")
    print("  /wallet/{address}  — досье по кошельку")
    print("  /wallets           — все кошельки")
    print("  /graph             — граф связей")
    print("  /mentions          — топ упоминаемых")
    print("  /stats             — сводная статистика")
    print("  /legal/articles              — справочник статей РК")
    print("  /legal/by-category/{cat}     — статьи по категории")
    print("  /export/arujan     — выгрузка JSON для Аружан")
    print()
    print("  ОБНОВЛЕНИЕ (PATCH):")
    print("  /wallet/{address}/blockchain — данные от Расула (блокчейн)")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)