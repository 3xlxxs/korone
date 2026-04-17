import os
import sys
import json
import asyncio
import aiohttp
import urllib.parse
import logging
from datetime import datetime
from typing import Optional

# ─── aiogram 3.7+ imports ───────────────────────────────────────
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

# ─── Logging setup ──────────────────────────────────────────────
class SymbolFormatter(logging.Formatter):
    """Формат: [HH:MM:SS] [SYMBOL] message"""
    SYMBOLS = {
        logging.INFO: "ℹ",
        logging.DEBUG: "•",
        logging.WARNING: "⚠",
        logging.ERROR: "✗",
        logging.CRITICAL: "✖",
    }
    
    def format(self, record: logging.LogRecord) -> str:
        record.symbol = self.SYMBOLS.get(record.levelno, "•")
        record.time = datetime.now().strftime("%H:%M:%S")
        return f"[{record.time}] [{record.symbol}] {record.getMessage()}"

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(SymbolFormatter())
root_logger.addHandler(handler)
log = root_logger.info

# ─── Config ─────────────────────────────────────────────────────
CFG = {
    "token": os.getenv("TELEGRAM_BOT_TOKEN"),
    "users_file": "tg_users.json",
    "processed_file": "tg_done.json",
    "base": os.getenv("BASE_URL", "https://www.pekora.zip"),
    "cookie": os.getenv("PUPPYSECURITY_COOKIE"),
}

API = {
    "search": f"{CFG['base']}/apisite/catalog/v3/search/items",
    "details": f"{CFG['base']}/apisite/catalog/v1/catalog/items/details",
    "thumbs": f"{CFG['base']}/apisite/thumbnails/v1/assets",
}

HDR = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": CFG["base"] + "/catalog",
}
COOK = {".PUPPYSECURITY": CFG["cookie"]} if CFG["cookie"] else {}

bot: Optional[Bot] = None
dp = Dispatcher()
users: set[int] = set()
done: set[int] = set()

# ─── Persistence ────────────────────────────────────────────────
def _load(filepath: str, key: str) -> set:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get(key, []))
        except (json.JSONDecodeError, IOError) as e:
            logging.warning(f"Не удалось загрузить {filepath}: {e}")
    return set()

def _save(filepath: str, data: set, key: str) -> None:
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({key: list(data), "ts": datetime.now().isoformat()}, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logging.error(f"Не удалось сохранить {filepath}: {e}")

def load_users() -> None:
    global users
    users = _load(CFG["users_file"], "subscribers")
    log(f"Загружено {len(users)} подписчиков")

def load_done() -> None:
    global done
    done = _load(CFG["processed_file"], "ids")
    log(f"Загружено {len(done)} обработанных ID")

def save_users() -> None:
    _save(CFG["users_file"], users, "subscribers")

def save_done() -> None:
    _save(CFG["processed_file"], done, "ids")

# ─── Bot Commands ───────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    uid = m.from_user.id
    username = f"@{m.from_user.username}" if m.from_user.username else str(uid)
    
    if uid not in users:
        users.add(uid)
        save_users()
        log(f"[+] Sub: {username}")
        await m.answer("✅ Подписаны на уведомления. /stop — отписка")
    else:
        log(f"[ℹ] Уже подписан: {username}")
        await m.answer("ℹ️ Вы уже подписаны")

@dp.message(Command("stop"))
async def cmd_stop(m: types.Message):
    uid = m.from_user.id
    username = f"@{m.from_user.username}" if m.from_user.username else str(uid)
    
    if uid in users:
        users.discard(uid)
        save_users()
        log(f"[-] Unsub: {username}")
        await m.answer("❌ Отписаны")
    else:
        log(f"[ℹ] Не был подписан: {username}")
        await m.answer("ℹ️ Вы не были подписаны")

@dp.message(Command("stats"))
async def cmd_stats(m: types.Message):
    if m.from_user.id not in users:
        return
    await m.answer(f"📊 Статистика:\n• Подписчиков: {len(users)}\n• Обработано товаров: {len(done)}")

# ─── API Helpers ────────────────────────────────────────────────
async def fetch(sess: aiohttp.ClientSession, url: str, params: Optional[dict] = None, 
                json_data: Optional[dict] = None, method: str = "get") -> list:
    try:
        async with sess.request(method, url, params=params, json=json_data, headers=HDR, cookies=COOK) as r:
            if r.status == 200:
                result = await r.json()
                return result.get("data", []) if isinstance(result, dict) else []
            logging.warning(f"HTTP {r.status} для {url}")
            return []
    except asyncio.TimeoutError:
        logging.error(f"Timeout при запросе к {url}")
        return []
    except aiohttp.ClientError as e:
        logging.error(f"ClientError при запросе к {url}: {e}")
        return []
    except Exception as e:
        logging.error(f"Неизвестная ошибка при запросе к {url}: {e}")
        return []

async def get_ids(sess: aiohttp.ClientSession) -> list[int]:
    params = {
        "category": 0, "limit": 100, "sortType": 0,
        "minPrice": 0, "maxPrice": 0, "currency": 3
    }
    items = await fetch(sess, API["search"], params=params)
    ids = [i["id"] for i in items if isinstance(i.get("id"), int)]
    log(f"[•] Получено {len(ids)} ID товаров")
    return ids

async def get_thumbs(sess: aiohttp.ClientSession, ids: list[int]) -> dict[int, str]:
    thumbs: dict[int, str] = {}
    for i in range(0, len(ids), 100):
        batch = ids[i:i+100]
        params = {"assetIds": ",".join(map(str, batch)), "format": "png", "size": "420x420"}
        data = await fetch(sess, API["thumbs"], params=params)
        for t in data:
            if t.get("state") == "Completed" and t.get("imageUrl"):
                thumbs[t["targetId"]] = t["imageUrl"]
        await asyncio.sleep(0.1)
    log(f"[•] Получено {len(thumbs)} превью")
    return thumbs

async def get_details(sess: aiohttp.ClientSession, ids: list[int]) -> list[dict]:
    items: list[dict] = []
    for i in range(0, len(ids), 100):
        batch = [{"itemType": "Asset", "id": x} for x in ids[i:i+100]]
        result = await fetch(sess, API["details"], json_data={"items": batch}, method="post")
        items.extend(result)
        await asyncio.sleep(0.1)
    log(f"[•] Получено {len(items)} деталей товаров")
    return items

def filter_items(items: list[dict]) -> list[dict]:
    filtered = [
        i for i in items 
        if i.get("isForSale") and (i.get("offsaleDeadline") or i.get("unitsAvailableForConsumption") is not None)
    ]
    log(f"[•] Отфильтровано {len(filtered)} доступных товаров")
    return filtered

# ─── Notify ─────────────────────────────────────────────────────
async def notify(item: dict, thumb: Optional[str] = None) -> tuple[int, int]:
    name = item.get("name", "?")
    iid = item.get("id")
    url = f"{CFG['base']}/catalog/{iid}/{urllib.parse.quote(name, safe='')}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Открыть", url=url)]
    ])
    
    sent, failed = 0, 0
    for uid in list(users):
        try:
            if thumb:
                await bot.send_photo(
                    chat_id=uid,
                    photo=thumb,
                    caption=f"<b>{name}</b>",
                    reply_markup=kb,
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=f"<b>{name}</b>",
                    reply_markup=kb,
                )
            sent += 1
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            logging.warning(f"Rate limit для пользователя {uid}: ждать {e.retry_after}с")
            await asyncio.sleep(e.retry_after + 1)
            continue
        except TelegramAPIError as e:
            logging.warning(f"Не удалось отправить пользователю {uid}: {e}")
            users.discard(uid)
            failed += 1
        except Exception as e:
            logging.error(f"Неизвестная ошибка при отправке {uid}: {e}")
            users.discard(uid)
            failed += 1
    
    if failed:
        save_users()
        log(f"[✗] Удалено {failed} неактивных подписчиков")
    
    return sent, failed

# ─── Scraper Loop ───────────────────────────────────────────────
async def check() -> int:
    async with aiohttp.ClientSession() as sess:
        ids = await get_ids(sess)
        if not ids:
            return 0
        
        thumbs = await get_thumbs(sess, ids)
        details = await get_details(sess, ids)
        candidates = filter_items(details)
        
        new_count = 0
        for item in candidates:
            iid = item.get("id")
            if iid not in done:
                sent, failed = await notify(item, thumbs.get(iid))
                done.add(iid)
                new_count += 1
                log(f"[★] Новый товар: {item.get('name')} (отправлено: {sent}, ошибок: {failed})")
                await asyncio.sleep(0.3)
        
        if new_count:
            save_done()
            log(f"[★] Всего новых: {new_count}")
        else:
            log("[○] Нет новых товаров")
        
        return new_count

async def scraper_loop() -> None:
    load_users()
    load_done()
    log("[ℹ] Scraper запущен")
    
    while True:
        try:
            await check()
        except asyncio.CancelledError:
            log("[✖] Scraper остановлен")
            break
        except Exception as e:
            logging.error(f"[✗] Ошибка в scraper_loop: {e}")
        await asyncio.sleep(60)

# ─── Main ───────────────────────────────────────────────────────
async def main() -> None:
    global bot
    
    if not CFG["token"]:
        logging.critical("❌ Укажите TELEGRAM_BOT_TOKEN в переменных окружения")
        return
    
    # ✅ aiogram 3.7+: используем DefaultBotProperties вместо parse_mode в Bot()
    bot = Bot(
        token=CFG["token"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Запуск скрапера как фоновой задачи
    scraper_task = asyncio.create_task(scraper_loop(), name="scraper")
    
    # Обработчик закрытия
    async def on_shutdown(dp: Dispatcher):
        log("[ℹ] Завершение работы...")
        scraper_task.cancel()
        try:
            await scraper_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        log("[✖] Бот остановлен")
    
    dp.shutdown.register(on_shutdown)
    
    log("[ℹ] Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("[✖] Прервано пользователем")
