from fastapi import FastAPI, HTTPException, Depends, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, uuid, json, hashlib, secrets, string
from datetime import datetime, date

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH       = os.environ.get("DB_PATH",       "finance.db")
VAPID_PUBLIC  = os.environ.get("VAPID_PUBLIC",  "BCtzPfkQarb3fX7wcFuDPgBx71iHTHG6JXELXjHlTcXVcoMqZL0hqKOIVWh6E_nhlXzrgJ7GtK5jsJ5Gu_nLVeA")
VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE", "XkiHS7bOcFWOopZK9mbqxDpGuWSiAWXxjPopLf3E17o")
VAPID_EMAIL   = os.environ.get("VAPID_EMAIL",   "mailto:z.s.e.r.g.e.i.11.24@gmail.com")
CRON_SECRET   = os.environ.get("CRON_SECRET",   "")


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


def gen_invite() -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(6))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            pin_hash TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#60a5fa',
            invite_code TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS group_members (
            user_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            PRIMARY KEY (user_id, group_id)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            type TEXT NOT NULL,
            date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS savings (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            target REAL NOT NULL,
            current REAL NOT NULL DEFAULT 0,
            color TEXT DEFAULT '#60a5fa',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            tag TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            subscription TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS categories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '📦',
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    # One-time migration: profiles -> users (safe to run repeatedly)
    try:
        if conn.execute("SELECT * FROM profiles LIMIT 1").fetchall():
            for p in conn.execute("SELECT * FROM profiles").fetchall():
                if not conn.execute("SELECT id FROM users WHERE id=?", (p["id"],)).fetchone():
                    invite = gen_invite()
                    while conn.execute("SELECT id FROM users WHERE invite_code=?", (invite,)).fetchone():
                        invite = gen_invite()
                    conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
                        (p["id"], p["name"], hash_pin("1234"), p["color"], invite, datetime.now().isoformat()))
            conn.commit()
            for table, col in [("transactions", "profile_id"), ("savings", "profile_id"),
                                ("reminders", "profile_id"), ("push_subscriptions", "profile_id")]:
                try:
                    rows = conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchall()
                    if rows and col in rows[0].keys():
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
                        conn.execute(f"UPDATE {table} SET user_id = profile_id")
                        conn.commit()
                except Exception:
                    pass
    except Exception:
        pass
    conn.close()


init_db()


# --- Models ---
class RegisterBody(BaseModel):
    name: str
    pin: str
    color: str = "#60a5fa"

class LoginBody(BaseModel):
    pin: str

class Transaction(BaseModel):
    name: str
    amount: float
    category: str
    type: str
    date: Optional[str] = None  # DD.MM, falls back to today if omitted

class Saving(BaseModel):
    name: str
    target: float
    current: float = 0
    color: str = "#60a5fa"

class SavingAdd(BaseModel):
    amount: float

class Reminder(BaseModel):
    text: str
    tag: str
    due_date: Optional[str] = None

class ReminderToggle(BaseModel):
    done: bool

class PushSubscription(BaseModel):
    subscription: dict

class JoinGroup(BaseModel):
    invite_code: str

class Category(BaseModel):
    name: str
    emoji: str = "📦"

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    emoji: Optional[str] = None

class ChangePinBody(BaseModel):
    old_pin: str
    new_pin: str


# --- Helpers ---
def _require_owner(row, user_id: str):
    if not row:
        raise HTTPException(404, "Not found")
    if row["user_id"] != user_id:
        raise HTTPException(403, "Forbidden")

def _check_cron(secret: Optional[str]):
    if CRON_SECRET and secret != CRON_SECRET:
        raise HTTPException(403, "Forbidden")


# --- Auth ---
@app.post("/api/auth/register")
def register(body: RegisterBody, db: sqlite3.Connection = Depends(get_db)):
    if len(body.pin) != 4 or not body.pin.isdigit():
        raise HTTPException(400, "PIN must be 4 digits")
    if db.execute("SELECT id FROM users WHERE pin_hash=?", (hash_pin(body.pin),)).fetchone():
        raise HTTPException(409, "PIN already taken")
    uid = str(uuid.uuid4())
    invite = gen_invite()
    while db.execute("SELECT id FROM users WHERE invite_code=?", (invite,)).fetchone():
        invite = gen_invite()
    now = datetime.now().isoformat()
    db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
               (uid, body.name, hash_pin(body.pin), body.color, invite, now))
    db.commit()
    return {"id": uid, "name": body.name, "color": body.color, "invite_code": invite}

@app.post("/api/auth/login")
def login(body: LoginBody, db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE pin_hash=?", (hash_pin(body.pin),)).fetchone()
    if not user:
        raise HTTPException(404, "User not found")
    return dict(user)

@app.get("/api/auth/me")
def get_me(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(404, "Not found")
    return dict(user)

@app.patch("/api/auth/pin")
def change_pin(body: ChangePinBody, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    if len(body.new_pin) != 4 or not body.new_pin.isdigit():
        raise HTTPException(400, "PIN must be 4 digits")
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(404, "Not found")
    if dict(user)["pin_hash"] != hash_pin(body.old_pin):
        raise HTTPException(403, "Wrong current PIN")
    if db.execute("SELECT id FROM users WHERE pin_hash=? AND id!=?", (hash_pin(body.new_pin), user_id)).fetchone():
        raise HTTPException(409, "PIN already taken")
    db.execute("UPDATE users SET pin_hash=? WHERE id=?", (hash_pin(body.new_pin), user_id))
    db.commit()
    return {"ok": True}

@app.delete("/api/auth/account")
def delete_account(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    """Delete account and all associated data."""
    if not db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
        raise HTTPException(404, "Not found")
    # Leave any groups first (clean up empty groups)
    for row in db.execute("SELECT group_id FROM group_members WHERE user_id=?", (user_id,)).fetchall():
        gid = row["group_id"]
        db.execute("DELETE FROM group_members WHERE user_id=? AND group_id=?", (user_id, gid))
        if not db.execute("SELECT 1 FROM group_members WHERE group_id=?", (gid,)).fetchone():
            db.execute("DELETE FROM groups WHERE id=?", (gid,))
    # Delete all user data
    for table in ("transactions", "savings", "reminders", "push_subscriptions", "categories"):
        db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    return {"ok": True}


# --- Groups ---
@app.post("/api/groups/join")
def join_group(body: JoinGroup, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    target = db.execute("SELECT * FROM users WHERE invite_code=?", (body.invite_code.upper(),)).fetchone()
    if not target:
        raise HTTPException(404, "Invite code not found")
    if target["id"] == user_id:
        raise HTTPException(400, "Cannot join yourself")
    my_groups = [r["group_id"] for r in
                 db.execute("SELECT group_id FROM group_members WHERE user_id=?", (user_id,)).fetchall()]
    for gid in my_groups:
        if db.execute("SELECT 1 FROM group_members WHERE user_id=? AND group_id=?", (target["id"], gid)).fetchone():
            raise HTTPException(409, "Already in a group together")
    gid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute("INSERT INTO groups VALUES (?,?,?)", (gid, "Совместный бюджет", now))
    db.execute("INSERT OR IGNORE INTO group_members VALUES (?,?)", (user_id, gid))
    db.execute("INSERT OR IGNORE INTO group_members VALUES (?,?)", (target["id"], gid))
    db.commit()
    return {"ok": True, "group_id": gid, "partner": dict(target)}

@app.get("/api/groups/my")
def my_groups(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    gids = db.execute("SELECT group_id FROM group_members WHERE user_id=?", (user_id,)).fetchall()
    result = []
    for row in gids:
        gid = row["group_id"]
        members = db.execute("""
            SELECT u.id, u.name, u.color, u.invite_code FROM users u
            JOIN group_members gm ON u.id = gm.user_id
            WHERE gm.group_id = ? AND u.id != ?
        """, (gid, user_id)).fetchall()
        result.append({"group_id": gid, "members": [dict(m) for m in members]})
    return result

@app.delete("/api/groups/{group_id}/leave")
def leave_group(group_id: str, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM group_members WHERE user_id=? AND group_id=?", (user_id, group_id))
    if not db.execute("SELECT 1 FROM group_members WHERE group_id=?", (group_id,)).fetchone():
        db.execute("DELETE FROM groups WHERE id=?", (group_id,))
    db.commit()
    return {"ok": True}

@app.get("/api/joint")
def get_joint(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    gids = db.execute("SELECT group_id FROM group_members WHERE user_id=?", (user_id,)).fetchall()
    result = []
    seen = set()
    for row in gids:
        for m in db.execute("""
            SELECT u.* FROM users u
            JOIN group_members gm ON u.id = gm.user_id
            WHERE gm.group_id = ?
        """, (row["group_id"],)).fetchall():
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            txs  = db.execute("SELECT * FROM transactions WHERE user_id=?", (m["id"],)).fetchall()
            savs = db.execute("SELECT * FROM savings WHERE user_id=?", (m["id"],)).fetchall()
            cats = {}
            for t in txs:
                if t["type"] == "expense":
                    cats[t["category"]] = cats.get(t["category"], 0) + t["amount"]
            recent = db.execute(
                "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (m["id"],)
            ).fetchall()
            result.append({
                "profile":     {"id": m["id"], "name": m["name"], "color": m["color"]},
                "income":      sum(t["amount"] for t in txs if t["type"] == "income"),
                "expense":     sum(t["amount"] for t in txs if t["type"] == "expense"),
                "balance":     sum(t["amount"] for t in txs if t["type"] == "income") -
                               sum(t["amount"] for t in txs if t["type"] == "expense"),
                "saved":       sum(s["current"] for s in savs),
                "by_category": cats,
                "recent_tx":   [dict(r) for r in recent],
            })
    return result


# --- Transactions ---
@app.get("/api/transactions")
def list_transactions(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/transactions")
def add_transaction(tx: Transaction, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    if tx.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    date_str = tx.date if tx.date else datetime.now().strftime("%d.%m")
    db.execute(
        "INSERT INTO transactions (id,user_id,name,amount,category,type,date,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, user_id, tx.name, tx.amount, tx.category, tx.type, date_str, now))
    db.commit()
    return {"id": uid, "user_id": user_id, "name": tx.name, "amount": tx.amount,
            "category": tx.category, "type": tx.type, "date": date_str, "created_at": now}

@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: str, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    _require_owner(db.execute("SELECT user_id FROM transactions WHERE id=?", (tx_id,)).fetchone(), user_id)
    db.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    db.commit()
    return {"ok": True}


# --- Savings ---
@app.get("/api/savings")
def list_savings(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM savings WHERE user_id=? ORDER BY created_at ASC", (user_id,)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/savings")
def add_saving(s: Saving, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    if s.target <= 0:
        raise HTTPException(400, "Target must be positive")
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute("INSERT INTO savings (id,user_id,name,target,current,color,created_at) VALUES (?,?,?,?,?,?,?)",
               (uid, user_id, s.name, s.target, s.current, s.color, now))
    db.commit()
    return {"id": uid, "user_id": user_id, **s.dict(), "created_at": now}

@app.patch("/api/savings/{sav_id}/add")
def add_to_saving(sav_id: str, body: SavingAdd, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM savings WHERE id=?", (sav_id,)).fetchone()
    _require_owner(row, user_id)
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    new_val = min(dict(row)["current"] + body.amount, dict(row)["target"])
    db.execute("UPDATE savings SET current=? WHERE id=?", (new_val, sav_id))
    db.commit()
    return {"current": new_val}

@app.delete("/api/savings/{sav_id}")
def delete_saving(sav_id: str, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    _require_owner(db.execute("SELECT user_id FROM savings WHERE id=?", (sav_id,)).fetchone(), user_id)
    db.execute("DELETE FROM savings WHERE id=?", (sav_id,))
    db.commit()
    return {"ok": True}


# --- Reminders ---
@app.get("/api/reminders")
def list_reminders(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM reminders WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/reminders")
def add_reminder(r: Reminder, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute("INSERT INTO reminders (id,user_id,text,tag,done,due_date,created_at) VALUES (?,?,?,?,?,?,?)",
               (uid, user_id, r.text, r.tag, 0, r.due_date, now))
    db.commit()
    return {"id": uid, "user_id": user_id, "text": r.text, "tag": r.tag,
            "done": False, "due_date": r.due_date, "created_at": now}

@app.patch("/api/reminders/{rem_id}")
def toggle_reminder(rem_id: str, body: ReminderToggle, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    _require_owner(db.execute("SELECT user_id FROM reminders WHERE id=?", (rem_id,)).fetchone(), user_id)
    db.execute("UPDATE reminders SET done=? WHERE id=?", (1 if body.done else 0, rem_id))
    db.commit()
    return {"ok": True}

@app.delete("/api/reminders/{rem_id}")
def delete_reminder(rem_id: str, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    _require_owner(db.execute("SELECT user_id FROM reminders WHERE id=?", (rem_id,)).fetchone(), user_id)
    db.execute("DELETE FROM reminders WHERE id=?", (rem_id,))
    db.commit()
    return {"ok": True}


# --- Categories ---
@app.get("/api/categories")
def list_categories(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM categories WHERE user_id=? ORDER BY created_at ASC", (user_id,)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/categories")
def add_category(cat: Category, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    if not cat.name.strip():
        raise HTTPException(400, "Name required")
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute("INSERT INTO categories VALUES (?,?,?,?,?)",
               (uid, user_id, cat.name.strip(), cat.emoji, now))
    db.commit()
    return {"id": uid, "user_id": user_id, "name": cat.name.strip(), "emoji": cat.emoji, "created_at": now}

@app.patch("/api/categories/{cat_id}")
def update_category(cat_id: str, body: CategoryUpdate, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    _require_owner(row, user_id)
    new_name  = body.name.strip() if body.name  else row["name"]
    new_emoji = body.emoji        if body.emoji else row["emoji"]
    db.execute("UPDATE categories SET name=?, emoji=? WHERE id=?", (new_name, new_emoji, cat_id))
    db.commit()
    return {"ok": True, "name": new_name, "emoji": new_emoji}

@app.delete("/api/categories/{cat_id}")
def delete_category(cat_id: str, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    _require_owner(db.execute("SELECT user_id FROM categories WHERE id=?", (cat_id,)).fetchone(), user_id)
    db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    db.commit()
    return {"ok": True}


# --- Push ---
@app.get("/api/push/vapid-public")
def get_vapid_public():
    return {"key": VAPID_PUBLIC}

@app.post("/api/push/subscribe")
def subscribe_push(body: PushSubscription, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid      = str(uuid.uuid4())
    now      = datetime.now().isoformat()
    sub_json = json.dumps(body.subscription)
    endpoint = body.subscription.get("endpoint", "")
    db.execute("DELETE FROM push_subscriptions WHERE user_id=? AND subscription LIKE ?",
               (user_id, f'%{endpoint[:50]}%'))
    db.execute("INSERT INTO push_subscriptions VALUES (?,?,?,?)", (uid, user_id, sub_json, now))
    db.commit()
    return {"ok": True}

def send_push(user_id: str, title: str, body: str, db: sqlite3.Connection):
    try:
        from pywebpush import webpush
        for row in db.execute(
            "SELECT subscription FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall():
            try:
                webpush(
                    subscription_info=json.loads(row["subscription"]),
                    data=json.dumps({"title": title, "body": body}),
                    vapid_private_key=VAPID_PRIVATE,
                    vapid_claims={"sub": VAPID_EMAIL},
                )
            except Exception as e:
                if "410" in str(e) or "404" in str(e):
                    db.execute("DELETE FROM push_subscriptions WHERE subscription=?", (row["subscription"],))
                    db.commit()
    except ImportError:
        pass

@app.post("/api/push/test")
def test_push(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    send_push(user_id, "Финансы", "Уведомления работают! 🎉", db)
    return {"ok": True}

@app.get("/api/push/check-today")
def check_today(x_cron_secret: Optional[str] = Header(None), db: sqlite3.Connection = Depends(get_db)):
    _check_cron(x_cron_secret)
    today   = date.today().isoformat()
    by_user: dict = {}
    for r in db.execute("SELECT * FROM reminders WHERE due_date=? AND done=0", (today,)).fetchall():
        by_user.setdefault(r["user_id"], []).append(r["text"])
    for uid, tasks in by_user.items():
        count = len(tasks)
        send_push(uid, f"Дела на сегодня ({count})",
                  tasks[0] if count == 1 else f"{tasks[0]} и ещё {count - 1}", db)
    return {"notified": len(by_user)}

@app.get("/api/push/remind-finances")
def remind_finances(x_cron_secret: Optional[str] = Header(None), db: sqlite3.Connection = Depends(get_db)):
    _check_cron(x_cron_secret)
    for u in db.execute("SELECT id FROM users").fetchall():
        send_push(u["id"], "Финансы 💰", "Не забудь записать расходы за сегодня!", db)
    return {"ok": True}


app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    index = os.path.join("static", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"error": "Frontend not found"}
