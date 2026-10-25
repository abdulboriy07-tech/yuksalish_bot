import aiosqlite
from datetime import datetime
from config import DB_PATH, PRIVATE_CHAT_ID


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, col_def: str):
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        cols = [row[1] for row in await cursor.fetchall()]
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                phone TEXT DEFAULT '',
                pending_tariff_id INTEGER DEFAULT 1,
                pending_amount INTEGER DEFAULT 690000,
                joined_at TEXT
            )
        """)
        await _ensure_column(db, "users", "phone", "TEXT DEFAULT ''")
        await _ensure_column(db, "users", "pending_tariff_id", "INTEGER DEFAULT 1")
        await _ensure_column(db, "users", "pending_amount", "INTEGER DEFAULT 690000")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tariffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                description TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tariff_id INTEGER,
                amount INTEGER NOT NULL,
                receipt_file_id TEXT NOT NULL,
                receipt_type TEXT DEFAULT 'photo',
                customer_name TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)
        await _ensure_column(db, "orders", "receipt_type", "TEXT DEFAULT 'photo'")
        await _ensure_column(db, "orders", "customer_name", "TEXT DEFAULT ''")
        await _ensure_column(db, "orders", "phone", "TEXT DEFAULT ''")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Ensure the FOCUS 21 base tariff (690,000 so'm) and SUPER40 promo tariff (483,000 so'm, -30%) exist
        await db.execute("DELETE FROM tariffs")
        await db.execute(
            "INSERT INTO tariffs (id, name, price, description) VALUES (?, ?, ?, ?)",
            (
                1,
                "🎯 FOCUS 21 — 21 kunlik to'liq dastur",
                690000,
                "10 ta video dars, 1 ta Offline secret dars, kurator nazorati, yopiq Telegram guruh va maxsus sovg'alar to'plami (Sumka, Daftar, Do'ppi)."
            )
        )
        await db.execute(
            "INSERT INTO tariffs (id, name, price, description) VALUES (?, ?, ?, ?)",
            (
                2,
                "🎯 FOCUS 21 — To'liq dastur (Promokod: SUPER40 -30%)",
                483000,
                "SUPER40 promokodi bilan 30% chegirma asosida to'liq 21 kunlik dastur."
            )
        )
        await db.execute(
            "INSERT INTO tariffs (id, name, price, description) VALUES (?, ?, ?, ?)",
            (
                3,
                "📌 FOCUS 21 — Joyni band qilish (100 000 so'm)",
                100000,
                "100 000 so'm to'lov qilib joyni va narxni 1-noyabrgacha band qilish (qolgan to'lov startgacha to'lanadi)."
            )
        )

        # Ensure default private channel ID (-1004496262658) is always seeded in settings
        default_chat_id = PRIVATE_CHAT_ID or "-1004496262658"
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('private_chat_id', ?)",
            (default_chat_id,)
        )
        await db.execute(
            "UPDATE settings SET value = ? WHERE key = 'private_chat_id' AND (value IS NULL OR TRIM(value) = '')",
            (default_chat_id,)
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('private_chat_title', 'Focus Club | Kutuv zali')"
        )
        await db.commit()


async def add_user(
    user_id: int,
    full_name: str,
    username: str | None,
    phone: str | None = None,
    pending_tariff_id: int | None = None,
    pending_amount: int | None = None
):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        t_id = pending_tariff_id if pending_tariff_id is not None else 1
        amt = pending_amount if pending_amount is not None else 690000
        if phone and pending_tariff_id is not None:
            await db.execute(
                """
                INSERT INTO users (user_id, full_name, username, phone, pending_tariff_id, pending_amount, joined_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    username = excluded.username,
                    phone = excluded.phone,
                    pending_tariff_id = excluded.pending_tariff_id,
                    pending_amount = excluded.pending_amount
                """,
                (user_id, full_name, username or "", phone.strip(), t_id, amt, now)
            )
        elif phone:
            await db.execute(
                """
                INSERT INTO users (user_id, full_name, username, phone, joined_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    username = excluded.username,
                    phone = excluded.phone
                """,
                (user_id, full_name, username or "", phone.strip(), now)
            )
        else:
            await db.execute(
                """
                INSERT INTO users (user_id, full_name, username, joined_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    username = excluded.username
                """,
                (user_id, full_name, username or "", now)
            )
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            return await cursor.fetchall()


async def get_tariffs():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tariffs ORDER BY id ASC") as cursor:
            return await cursor.fetchall()


async def get_tariff(tariff_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tariffs WHERE id = ?", (tariff_id,)) as cursor:
            return await cursor.fetchone()


async def create_order(
    user_id: int,
    tariff_id: int | None,
    amount: int,
    receipt_file_id: str,
    receipt_type: str = "photo",
    customer_name: str = "",
    phone: str = ""
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await db.execute(
            """
            INSERT INTO orders (user_id, tariff_id, amount, receipt_file_id, receipt_type, customer_name, phone, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, tariff_id, amount, receipt_file_id, receipt_type, customer_name, phone, now)
        )
        await db.commit()
        return cursor.lastrowid


async def get_order(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT o.*, t.name as tariff_name, u.full_name, u.username, u.phone as user_phone
            FROM orders o
            LEFT JOIN tariffs t ON o.tariff_id = t.id
            LEFT JOIN users u ON o.user_id = u.user_id
            WHERE o.id = ?
            """,
            (order_id,)
        ) as cursor:
            return await cursor.fetchone()


async def update_order_status(order_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        await db.commit()


async def get_user_orders(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT o.*, t.name as tariff_name
            FROM orders o
            LEFT JOIN tariffs t ON o.tariff_id = t.id
            WHERE o.user_id = ?
            ORDER BY o.id DESC LIMIT 10
            """,
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()


async def get_pending_orders():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT o.*, t.name as tariff_name, u.full_name, u.username, u.phone as user_phone
            FROM orders o
            LEFT JOIN tariffs t ON o.tariff_id = t.id
            LEFT JOIN users u ON o.user_id = u.user_id
            WHERE o.status = 'pending'
            ORDER BY o.id DESC
            """
        ) as cursor:
            return await cursor.fetchall()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM orders") as cur:
            total_orders = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'") as cur:
            pending_orders = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM orders WHERE status = 'approved'") as cur:
            row = await cur.fetchone()
            approved_orders = row[0]
            total_revenue = row[1]

        return {
            "total_users": total_users,
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "approved_orders": approved_orders,
            "total_revenue": total_revenue
        }


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value))
        )
        await db.commit()


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] is not None else default
