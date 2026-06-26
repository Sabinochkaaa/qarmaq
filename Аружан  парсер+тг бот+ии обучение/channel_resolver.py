"""
channel_resolver.py
Два способа получить список каналов для мониторинга:

1. discover_from_dialogs(client) — РЕКОМЕНДУЕТСЯ, если аккаунт уже состоит
   во всех нужных каналах. Берёт список прямо из диалогов аккаунта
   (client.get_dialogs()), без всякого резолва по имени/ссылке —
   соответственно никаких проблем с "не находит по названию" в принципе
   не возникает, и приватные каналы без username тоже подхватываются.

2. resolve_from_links(client, refs) — на случай, если нужен не весь список
   диалогов, а конкретный набор ссылок. Понимает форматы:
   - "@username" / "username"
   - "https://t.me/username"
   - "https://t.me/c/<internal_id>/<msg_id>"  (приватный канал — работает
     только если аккаунт уже состоит в этом канале, т.к. без этого
     internal_id ничего не значит для Telethon)
   - "https://t.me/+<hash>" или "https://t.me/joinchat/<hash>" (инвайт-ссылка —
     если аккаунт уже в канале, Telethon резолвит её через диалоги;
     если ещё не в канале — придётся вступить вручную одним разом)
"""

import re
import logging
from telethon.tl.types import PeerChannel

logger = logging.getLogger("channel_resolver")

LINK_C_RE = re.compile(r"t\.me/c/(\d+)")
LINK_USERNAME_RE = re.compile(r"t\.me/([a-zA-Z0-9_]{5,32})(?:/\d+)?$")
LINK_INVITE_RE = re.compile(r"t\.me/(?:\+|joinchat/)([a-zA-Z0-9_-]+)")


async def discover_from_dialogs(client, folder_name: str | None = None) -> list:
    """
    Возвращает список всех каналов/супергрупп, в которых состоит аккаунт.
    Если folder_name указан — берёт только диалоги из этой папки (Telegram folder),
    если ты организовала нужные каналы в отдельную папку в приложении.
    """
    chats = []
    target_folder_id = None

    if folder_name:
        filters = await client.get_dialog_filters()
        for f in filters:
            title = getattr(f, "title", None)
            if title and title.lower() == folder_name.lower():
                target_folder_id = f.id
                break
        if target_folder_id is None:
            logger.warning(f"Папка '{folder_name}' не найдена, беру ВСЕ диалоги")

    async for dialog in client.iter_dialogs():
        if not (dialog.is_channel or dialog.is_group):
            continue
        if target_folder_id is not None:
            # фильтрация по папке требует сверки peer с filters[target_folder_id].include_peers
            # упрощённый путь — пропускаем эту проверку для каждого диалога отдельно ниже
            pass
        chats.append(dialog.entity)

    if target_folder_id is not None:
        filters = await client.get_dialog_filters()
        target_filter = next((f for f in filters if f.id == target_folder_id), None)
        if target_filter is not None:
            included_ids = set()
            for peer in target_filter.include_peers:
                cid = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "user_id", None)
                if cid:
                    included_ids.add(cid)
            chats = [c for c in chats if c.id in included_ids]

    logger.info(f"Найдено каналов/групп в диалогах: {len(chats)}")
    return chats


def _parse_ref(ref: str):
    """Возвращает (kind, value) где kind in {'username','internal_id','invite'}"""
    ref = ref.strip()

    m = LINK_C_RE.search(ref)
    if m:
        return "internal_id", int(m.group(1))

    m = LINK_INVITE_RE.search(ref)
    if m:
        return "invite", m.group(1)

    m = LINK_USERNAME_RE.search(ref)
    if m:
        return "username", m.group(1)

    # просто "@username" или "username" без ссылки
    return "username", ref.lstrip("@")


async def resolve_from_links(client, refs: list) -> list:
    """Резолвит список ссылок/юзернеймов в Entity-объекты Telethon."""
    resolved = []
    for ref in refs:
        kind, value = _parse_ref(ref)
        try:
            if kind == "internal_id":
                # т.к. это супергруппа/канал, Telegram хранит chat_id как -100<internal_id>
                entity = await client.get_entity(PeerChannel(value))
            elif kind == "invite":
                # работает только если аккаунт уже состоит в канале —
                # Telethon найдёт его через кэш диалогов по hash инвайт-ссылки
                entity = await client.get_entity(ref)
            else:
                entity = await client.get_entity(value)
            resolved.append(entity)
        except Exception as e:
            logger.error(f"Не удалось резолвить '{ref}' (распознано как {kind}={value}): {e}")
    return resolved