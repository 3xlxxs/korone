import asyncio, aiohttp, json, os, urllib.parse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

CFG = {
    "cookie": os.getenv("PUPPYSECURITY_COOKIE"),
    "webhook": os.getenv("DISCORD_WEBHOOK"),
    "base": os.getenv("BASE_URL", "https://www.pekora.zip"),
    "search": "https://www.pekora.zip/apisite/catalog/v3/search/items",
    "details": "https://www.pekora.zip/apisite/catalog/v1/catalog/items/details",
}
HDRS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "Referer": CFG["base"]+"/catalog", "Origin": CFG["base"]}
COOK = {".PUPPYSECURITY": CFG["cookie"]}
FILE = "processed.json"

processed = set()

def log(m): 
    t = datetime.now().strftime("%H:%M:%S")
    print(f"{t} | {m}")

def load_proc():
    global processed
    if os.path.exists(FILE):
        try: processed = set(json.load(open(FILE, "r")).get("ids", []))
        except: pass

def save_proc():
    json.dump({"ids": list(processed), "ts": datetime.now().isoformat()}, open(FILE, "w"), indent=2)

async def fetch_ids(sess, pages=5):
    ids, cursor = [], None
    for p in range(pages):
        params = {"category": 0, "limit": 100, "sortType": 0, "minPrice": 0, "maxPrice": 0, "currency": 3}
        if cursor: params["cursor"] = cursor
        async with sess.get(CFG["search"], params=params, headers=HDRS, cookies=COOK) as r:
            if r.status != 200: break
            data = await r.json()
            items = data.get("data", []) if isinstance(data, dict) else []
            ids.extend(i["id"] for i in items if isinstance(i.get("id"), int))
            log(f"Page {p+1}: +{len(items)} items")
            cursor = data.get("nextPageCursor")
            if not cursor: break
            await asyncio.sleep(0.1)
    return ids

async def fetch_details(sess, ids_batch):
    payload = {"items": [{"itemType": "Asset", "id": i} for i in ids_batch]}
    try:
        async with sess.post(CFG["details"], json=payload, headers=HDRS, cookies=COOK) as r:
            return (await r.json()).get("data", []) if r.status == 200 else []
    except: return []

def filter_items(items):
    return [i for i in items if i.get("isForSale") and (i.get("offsaleDeadline") or i.get("unitsAvailableForConsumption") is not None)]

async def notify(sess, item):
    name, iid = item.get("name","?"), item.get("id")
    price = f"{item.get('priceTickets')} Tickets" if item.get("priceTickets") else f"{item.get('price')} R$" if item.get("price") else "Free"
    url = f"{CFG['base']}/catalog/{iid}/{urllib.parse.quote(name, safe='')}"
    embed = {
        "title": name, "url": url, "color": 0x2b2d31,
        "fields": [
            {"name": "Price", "value": price, "inline": True},
            {"name": "Stock", "value": str(item.get("unitsAvailableForConsumption","N/A")), "inline": True},
            {"name": "Creator", "value": item.get("creatorName","?"), "inline": True},
            {"name": "Description", "value": (item.get("description","")[:200]+"...") if len(item.get("description",""))>200 else item.get("description",""), "inline": False}
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
    try:
        async with sess.post(CFG["webhook"], json={"username":"Korone Monitor","content":"@everyone","embeds":[embed]}, timeout=aiohttp.ClientTimeout(10)) as r:
            if r.status in (200,204): log(f"Notified #{iid}")
    except Exception as e: log(f"Discord: {e}")

async def run():
    log("Scraper started")
    load_proc()
    async with aiohttp.ClientSession() as sess:
        cycle = 0
        while True:
            cycle += 1
            log(f"Cycle #{cycle} @ {datetime.now().strftime('%H:%M:%S')}")
            try:
                ids = await fetch_ids(sess)
                if not ids:
                    await asyncio.sleep(60)
                    continue
                new_count = 0
                for i in range(0, len(ids), 100):
                    batch = ids[i:i+100]
                    details = await fetch_details(sess, batch)
                    for item in filter_items(details):
                        iid = item.get("id")
                        if iid not in processed:
                            await notify(sess, item)
                            processed.add(iid)
                            new_count += 1
                            await asyncio.sleep(0.3)
                    await asyncio.sleep(0.1)
                if new_count:
                    save_proc()
                    log(f"Sent {new_count} notifications")
                else:
                    log("No new items")
            except KeyboardInterrupt:
                log("Stopped by user")
                save_proc()
                break
            except Exception as e:
                log(f"Error: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    if not CFG["cookie"] or not CFG["webhook"]:
        print("❌ Set PUPPYSECURITY_COOKIE and DISCORD_WEBHOOK in .env")
        exit(1)
        
    asyncio.run(run())