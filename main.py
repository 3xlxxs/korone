import os
try:
    import json, asyncio, aiohttp, urllib.parse
    from datetime import datetime
    from aiogram import Bot, Dispatcher, types
    from aiogram.filters import Command
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
except:
    os.system('pip install "aiogram>=3.0.0" "aiohttp>=3.9.0" "python-dotenv>=1.0.0"')

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
HDR = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "Referer": CFG["base"]+"/catalog"}
COOK = {".PUPPYSECURITY": CFG["cookie"]} if CFG["cookie"] else {}

bot = Bot(token=CFG["token"])
dp = Dispatcher()
users = set()
done = set()

def load(f, default=set()):
    if os.path.exists(f):
        try: return set(json.load(open(f, "r")).get("ids" if "done" in f else "subscribers", []))
        except: pass
    return default

def save(f, data, key):
    json.dump({key: list(data), "ts": datetime.now().isoformat()}, open(f, "w"), indent=2)

def log(m): print(f"{datetime.now().strftime('%H:%M:%S')} | {m}")

# ─── Bot Commands ───────────────────────────────────────────────
@dp.message(Command("start"))
async def start(m: types.Message):
    uid = m.from_user.id
    if uid not in users:
        users.add(uid); save(CFG["users_file"], users, "subscribers")
        log(f"[+] Sub: @{m.from_user.username or uid}")
        await m.answer("✅ Подписаны на уведомления. /stop — отписка")
    else:
        await m.answer("ℹ️ Вы уже подписаны")

@dp.message(Command("stop"))
async def stop(m: types.Message):
    uid = m.from_user.id
    if uid in users:
        users.discard(uid); save(CFG["users_file"], users, "subscribers")
        log(f"[-] Unsub: @{m.from_user.username or uid}")
        await m.answer("❌ Отписаны")
    else:
        await m.answer("ℹ️ Вы не были подписаны")

# ─── API Helpers ────────────────────────────────────────────────
async def fetch(sess, url, params=None, json_data=None, method="get"):
    try:
        async with sess.request(method, url, params=params, json=json_data, headers=HDR, cookies=COOK) as r:
            return (await r.json()).get("data", []) if r.status == 200 else []
    except: return []

async def get_ids(sess):
    items = await fetch(sess, API["search"], {"category":0,"limit":100,"sortType":0,"minPrice":0,"maxPrice":0,"currency":3})
    return [i["id"] for i in items if isinstance(i.get("id"), int)]

async def get_thumbs(sess, ids):
    thumbs = {}
    for i in range(0, len(ids), 100):
        batch = ids[i:i+100]
        data = await fetch(sess, API["thumbs"], {"assetIds":",".join(map(str,batch)),"format":"png","size":"420x420"})
        for t in data:
            if t.get("state")=="Completed" and t.get("imageUrl"): thumbs[t["targetId"]] = t["imageUrl"]
        await asyncio.sleep(0.1)
    return thumbs

async def get_details(sess, ids):
    items = []
    for i in range(0, len(ids), 100):
        batch = [{"itemType":"Asset","id":x} for x in ids[i:i+100]]
        items.extend(await fetch(sess, API["details"], json_data={"items":batch}, method="post"))
        await asyncio.sleep(0.1)
    return items

def filter_items(items):
    return [i for i in items if i.get("isForSale") and (i.get("offsaleDeadline") or i.get("unitsAvailableForConsumption") is not None)]

# ─── Notify ─────────────────────────────────────────────────────
async def notify(item, thumb=None):
    name, iid = item.get("name", "?"), item.get("id")
    
    url = f"{CFG['base']}/catalog/{iid}/{urllib.parse.quote(name, safe='')}"
    
    # 🔘 Кнопка
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒", url=url)]
    ])
    
    # 📤 Отправка подписчикам
    sent, failed = 0, 0
    for uid in list(users):
        try:
            if thumb:
                await bot.send_photo(
                    uid, 
                    thumb, 
                    caption=f"<b>{name}</b>", 
                    reply_markup=kb, 
                    parse_mode="HTML"
                )
            else:
                # Fallback без изображения
                await bot.send_message(
                    uid, 
                    f"<b>{name}</b>", 
                    reply_markup=kb, 
                    parse_mode="HTML"
                )
            sent += 1
            await asyncio.sleep(0.05)  # ⚠️ защита от лимитов
        except Exception:
            users.discard(uid)  # ❌ удаляем неактивных
            failed += 1
    
    save(CFG["users_file"], users, "subscribers")
    return sent, failed

# ─── Scraper Loop ───────────────────────────────────────────────
async def check():
    async with aiohttp.ClientSession() as sess:
        ids = await get_ids(sess)
        if not ids: return 0
        thumbs = await get_thumbs(sess, ids)
        details = await get_details(sess, ids)
        new = 0
        for item in filter_items(details):
            iid = item.get("id")
            if iid not in done:
                await notify(item, thumbs.get(iid))
                done.add(iid); new += 1
                await asyncio.sleep(0.3)
        if new: save(CFG["processed_file"], done, "ids"); log(f"★ {new} новых")
        else: log("○ Нет новых")

async def scraper_loop():
    users.update(load(CFG["users_file"], set())); done.update(load(CFG["processed_file"], set()))
    log("🚀 Scraper started")
    while True:
        try: await check()
        except Exception as e: log(f"✗ {e}")
        await asyncio.sleep(60)

# ─── Main ───────────────────────────────────────────────────────
async def main():
    if not CFG["token"]:
        print("❌ Set TELEGRAM_BOT_TOKEN")
        return

    # Запускаем скрапер как фоновую задачу (не блокирует цикл событий)
    asyncio.create_task(scraper_loop())

    # Запускаем бота (блокирует выполнение до Ctrl+C / SIGTERM)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
