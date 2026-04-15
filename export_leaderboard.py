"""
export_leaderboard.py
=====================
Chạy lúc 0h hàng ngày để kéo data từ database và xuất leaderboard.json
Setup: Windows Task Scheduler chạy file này mỗi ngày lúc 00:00

Yêu cầu: pip install pymysql pymongo requests
"""

import json
import os
import sys
from datetime import datetime

# ============================================================
# CẤU HÌNH - CHỈNH SỬA PHẦN NÀY
# ============================================================
CONFIG = {
    # MySQL (chứa redisdb + tb_user)
    "mysql_host": "10.3.2.40",
    "mysql_port": 3306,
    "mysql_user": "service",
    "mysql_password": "06V^kI8hpu8E",
    "mysql_db": "config",          # database chứa bảng redisdb

    # MongoDB (fallback nếu redisdb không đủ coin)
    "mongo_uri": "mongodb://service:M3rWBGv%26nw@10.3.2.40:27017/?authSource=btc_log",
    "mongo_db": "btc_log",

    # Lọc
    "email_suffix": "@stu.ptit.edu.vn",

    # Output
    "output_json": "leaderboard.json",   # file sẽ commit lên GitHub

    # Giá coin (fallback nếu Binance không kết nối được)
    "fallback_prices": {
        "BTC": 85000, "ETH": 3200, "BNB": 600,
        "SOL": 150, "XRP": 0.5, "ADA": 0.45,
        "DOGE": 0.08, "AVAX": 30, "MATIC": 0.7,
        "USDT": 1.0, "USDC": 1.0, "BUSD": 1.0,
    },
}
# ============================================================


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_mysql():
    import pymysql
    return pymysql.connect(
        host=CONFIG["mysql_host"],
        port=CONFIG["mysql_port"],
        user=CONFIG["mysql_user"],
        password=CONFIG["mysql_password"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def get_coin_prices() -> dict:
    """Lấy giá coin từ Binance"""
    import urllib.request
    prices = dict(CONFIG["fallback_prices"])
    coins = [c for c in prices if c not in ("USDT", "USDC", "BUSD")]
    symbols = [f"{c}USDT" for c in coins]
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbols=" + \
              json.dumps(symbols).replace(" ", "")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        for item in data:
            coin = item["symbol"].replace("USDT", "")
            prices[coin] = float(item["price"])
        log(f"Giá Binance: " + " | ".join(f"{k}={v:,.0f}" for k, v in prices.items() if k not in ("USDT","USDC","BUSD")))
    except Exception as e:
        log(f"⚠ Binance timeout ({e}), dùng giá fallback")
    return prices


def get_eligible_accounts() -> dict:
    """Lấy tất cả account có đuôi email @stu.ptit.edu.vn từ MySQL web.tb_user"""
    suf = CONFIG["email_suffix"]
    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT account_id, account FROM web.tb_user WHERE RIGHT(account, %s) = %s",
                [len(suf), suf]
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    accounts = {}
    for r in rows:
        aid = int(r["account_id"])
        email = r["account"]
        # Trích tên: hungttb24tc042@stu.ptit.edu.vn -> lấy trước "b24"
        local = email.split("@")[0]  # hungttb24tc042
        idx = local.lower().find("b24")
        name = local[:idx] if idx > 0 else local
        accounts[aid] = {"email": email, "name": name}

    log(f"Tìm thấy {len(accounts)} account @stu.ptit.edu.vn")
    return accounts


def get_balances_from_redisdb(account_ids: list, prices: dict) -> dict:
    """
    Đọc số dư từ bảng config.redisdb
    Key format: bank:{COIN}:{account_id} → rvalue = số dư (đơn vị nhỏ nhất)
    Cần xác định divisor: BTC thường *10^8, USDT *10^6 hoặc *10^2 tùy hệ thống
    """
    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(account_ids))
            # Lấy tất cả bank:{COIN}:{account_id}
            cur.execute(f"""
                SELECT rkey, rvalue
                FROM config.redisdb
                WHERE rkey REGEXP '^bank:[A-Z]+:[0-9]+$'
                  AND CAST(SUBSTRING_INDEX(rkey, ':', -1) AS UNSIGNED) IN ({placeholders})
            """, account_ids)
            rows = cur.fetchall()
    finally:
        conn.close()

    # Parse: bank:BTC:12345 → coin=BTC, aid=12345, val=rvalue
    balances = {}  # aid → {coin: amount}
    for r in rows:
        parts = r["rkey"].split(":")
        if len(parts) != 3:
            continue
        coin = parts[1]
        try:
            aid = int(parts[2])
            raw_val = float(r["rvalue"] or 0)
        except:
            continue

        # Tự động detect đơn vị dựa trên magnitude
        # BTC thường lưu satoshi (10^8), USDT thường lưu cent (10^2) hoặc raw
        # Thử detect: nếu giá trị > 10^9 và coin là BTC → chia 10^8
        if raw_val > 1e9 and coin in ("BTC", "ETH", "BNB"):
            amount = raw_val / 1e8
        elif raw_val > 1e8 and coin in ("USDT", "USDC", "BUSD", "ADA", "XRP", "DOGE", "MATIC"):
            amount = raw_val / 1e6
        else:
            amount = raw_val  # dùng nguyên

        if aid not in balances:
            balances[aid] = {}
        if coin not in balances[aid]:
            balances[aid][coin] = 0
        balances[aid][coin] += amount

    log(f"Đọc được số dư từ redisdb: {len(balances)} account có data")
    return balances


def get_balances_from_mongo(account_ids: list) -> dict:
    """Fallback: đọc từ MongoDB account_balance (số dư mới nhất)"""
    from pymongo import MongoClient
    client = MongoClient(CONFIG["mongo_uri"])
    col = client[CONFIG["mongo_db"]]["account_balance"]

    pipeline = [
        {"$match": {"accountId": {"$in": account_ids}}},
        {"$sort": {"date": -1}},
        {"$group": {
            "_id": {"accountId": "$accountId", "coinName": "$coinName"},
            "latestBalance": {"$first": "$resultNumber"},
        }}
    ]
    raw = list(col.aggregate(pipeline))

    balances = {}
    for r in raw:
        aid = int(r["_id"]["accountId"])
        coin = r["_id"]["coinName"]
        bal = float(str(r["latestBalance"]))
        if aid not in balances:
            balances[aid] = {}
        balances[aid][coin] = bal

    log(f"Fallback MongoDB: {len(balances)} account có data")
    return balances


def calculate_equity(balances: dict, prices: dict) -> dict:
    """Tính equity = tổng tất cả coin * giá → USDT"""
    equity = {}
    for aid, coins in balances.items():
        total = 0.0
        detail = {}
        for coin, amount in coins.items():
            price = prices.get(coin, 0)
            usdt_val = amount * price
            total += usdt_val
            if amount > 0:
                detail[coin] = round(amount, 6)
        equity[aid] = {
            "equity": round(total, 2),
            "coins": detail,
        }
    return equity


def build_leaderboard():
    log("=" * 50)
    log("Bắt đầu export leaderboard...")
    log(f"Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 50)

    # 1. Lấy danh sách account hợp lệ
    accounts = get_eligible_accounts()
    if not accounts:
        log("⚠ Không có account nào!")
        return

    account_ids = list(accounts.keys())

    # 2. Lấy giá coin
    prices = get_coin_prices()

    # 3. Lấy số dư từ redisdb
    balances = get_balances_from_redisdb(account_ids, prices)

    # 4. Fallback sang MongoDB cho account không có trong redisdb
    missing = [aid for aid in account_ids if aid not in balances]
    if missing:
        log(f"Fallback MongoDB cho {len(missing)} account thiếu data...")
        mongo_balances = get_balances_from_mongo(missing)
        balances.update(mongo_balances)

    # 5. Tính equity
    equity_map = calculate_equity(balances, prices)

    # 6. Build leaderboard — chỉ account có trong danh sách hợp lệ
    rows = []
    for aid, info in equity_map.items():
        if aid not in accounts:
            continue
        acc = accounts[aid]
        rows.append({
            "accountId": str(aid),
            "name": acc["name"],
            "email": acc["email"],
            "equity": info["equity"],
            "coins": info["coins"],
        })

    # Sắp xếp theo equity giảm dần
    rows.sort(key=lambda x: x["equity"], reverse=True)

    # Thêm rank
    for i, row in enumerate(rows):
        row["rank"] = i + 1

    # 7. Xuất JSON
    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_date": datetime.now().strftime("%Y-%m-%d"),
        "total_participants": len(rows),
        "leaderboard": rows,
        "prices_used": {k: v for k, v in prices.items() if k not in ("USDC", "BUSD")},
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG["output_json"])
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log(f"✅ Xuất xong: {out_path}")
    log(f"   Tổng: {len(rows)} trader")
    if rows:
        log(f"   #1: {rows[0]['name']} — {rows[0]['equity']:,.2f} USDT")

    return out_path


if __name__ == "__main__":
    build_leaderboard()
