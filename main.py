from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
import sqlite3, os, uuid, json, hashlib, secrets, string
from datetime import datetime, date

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH = os.environ.get("DB_PATH", "finance.db")
VAPID_PUBLIC = "BCtzPfkQarb3fX7wcFuDPgBx71iHTHG6JXELXjHlTcXVcoMqZL0hqKOIVWh6E_nhlXzrgJ7GtK5jsJ5Gu_nLVeA"
VAPID_PRIVATE = "XkiHS7bOcFWOopZK9mbqxDpGuWSiAWXxjPopLf3E17o"
VAPID_EMAIL = "mailto:z.s.e.r.g.e.i.11.24@gmail.com"

def get_db():
    conn = sqlite3.connect(DB_PATH)
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
            color TEXT DEFAULT '#185FA5',
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
    """)
    conn.commit()
    # Migration: move profile_id -> user_id if needed
    KNOWN_PINS = {
        '2faf44f3-8be1-4d84-89de-ecbf1449eb90': '1111',  # Сергей
        'd9febcf0-7bab-405f-a235-f7cec2fa2c93': '2222',  # Дарья
    }
    try:
        old_profiles = conn.execute("SELECT * FROM profiles LIMIT 1").fetchall()
        if old_profiles:
            profiles = conn.execute("SELECT * FROM profiles").fetchall()
            for p in profiles:
                old_id = p["id"]
                name = p["name"]
                color = p["color"]
                existing = conn.execute("SELECT id FROM users WHERE id=?", (old_id,)).fetchone()
                if not existing:
                    invite = gen_invite()
                    while conn.execute("SELECT id FROM users WHERE invite_code=?", (invite,)).fetchone():
                        invite = gen_invite()
                    pin = KNOWN_PINS.get(old_id, '1234')
                    pin_hash = hash_pin(pin)
                    conn.execute(
                        "INSERT INTO users VALUES (?,?,?,?,?,?)",
                        (old_id, name, pin_hash, color, invite, datetime.now().isoformat())
                    )
            conn.commit()
            # Migrate transactions
            for table, col in [("transactions","profile_id"),("savings","profile_id"),("reminders","profile_id"),("push_subscriptions","profile_id")]:
                try:
                    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                    if rows and col in rows[0].keys():
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
                        conn.execute(f"UPDATE {table} SET user_id = {col}")
                        conn.commit()
                except:
                    pass
    except Exception as e:
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

class Saving(BaseModel):
    name: str
    target: float
    current: float = 0
    color: str = "#185FA5"

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

# --- Auth ---
@app.post("/api/auth/register")
def register(body: RegisterBody, db: sqlite3.Connection = Depends(get_db)):
    if len(body.pin) != 4 or not body.pin.isdigit():
        raise HTTPException(400, "PIN must be 4 digits")
    pin_hash = hash_pin(body.pin)
    existing = db.execute("SELECT id FROM users WHERE pin_hash=?", (pin_hash,)).fetchone()
    if existing:
        raise HTTPException(409, "PIN already taken")
    uid = str(uuid.uuid4())
    invite = gen_invite()
    while db.execute("SELECT id FROM users WHERE invite_code=?", (invite,)).fetchone():
        invite = gen_invite()
    now = datetime.now().isoformat()
    db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
               (uid, body.name, pin_hash, body.color, invite, now))
    db.commit()
    return {"id": uid, "name": body.name, "color": body.color, "invite_code": invite}

@app.post("/api/auth/login")
def login(body: LoginBody, db: sqlite3.Connection = Depends(get_db)):
    pin_hash = hash_pin(body.pin)
    user = db.execute("SELECT * FROM users WHERE pin_hash=?", (pin_hash,)).fetchone()
    if not user:
        raise HTTPException(404, "User not found")
    return dict(user)

@app.get("/api/auth/me")
def get_me(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(404, "Not found")
    return dict(user)

class ChangePinBody(BaseModel):
    old_pin: str
    new_pin: str

@app.patch("/api/auth/pin")
def change_pin(body: ChangePinBody, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    if len(body.new_pin) != 4 or not body.new_pin.isdigit():
        raise HTTPException(400, "PIN must be 4 digits")
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(404, "Not found")
    if dict(user)["pin_hash"] != hash_pin(body.old_pin):
        raise HTTPException(403, "Wrong current PIN")
    new_hash = hash_pin(body.new_pin)
    existing = db.execute("SELECT id FROM users WHERE pin_hash=? AND id!=?", (new_hash, user_id)).fetchone()
    if existing:
        raise HTTPException(409, "PIN already taken")
    db.execute("UPDATE users SET pin_hash=? WHERE id=?", (new_hash, user_id))
    db.commit()
    return {"ok": True}

# --- Groups (joint budget) ---
@app.post("/api/groups/join")
def join_group(body: JoinGroup, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    target = db.execute("SELECT * FROM users WHERE invite_code=?", (body.invite_code.upper(),)).fetchone()
    if not target:
        raise HTTPException(404, "Invite code not found")
    if target["id"] == user_id:
        raise HTTPException(400, "Cannot join yourself")
    # Check if already in a group together
    my_groups = db.execute("SELECT group_id FROM group_members WHERE user_id=?", (user_id,)).fetchall()
    my_group_ids = [r["group_id"] for r in my_groups]
    for gid in my_group_ids:
        member = db.execute("SELECT 1 FROM group_members WHERE user_id=? AND group_id=?", (target["id"], gid)).fetchone()
        if member:
            raise HTTPException(409, "Already in a group together")
    # Find or create group
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
    remaining = db.execute("SELECT COUNT(*) as cnt FROM group_members WHERE group_id=?", (group_id,)).fetchone()
    if remaining["cnt"] == 0:
        db.execute("DELETE FROM groups WHERE id=?", (group_id,))
    db.commit()
    return {"ok": True}

@app.get("/api/joint")
def get_joint(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    gids = db.execute("SELECT group_id FROM group_members WHERE user_id=?", (user_id,)).fetchall()
    result = []
    seen_users = set()
    for row in gids:
        gid = row["group_id"]
        members = db.execute("""
            SELECT u.* FROM users u
            JOIN group_members gm ON u.id = gm.user_id
            WHERE gm.group_id = ?
        """, (gid,)).fetchall()
        for m in members:
            if m["id"] in seen_users:
                continue
            seen_users.add(m["id"])
            txs = db.execute("SELECT * FROM transactions WHERE user_id=?", (m["id"],)).fetchall()
            income = sum(r["amount"] for r in txs if r["type"] == "income")
            expense = sum(r["amount"] for r in txs if r["type"] == "expense")
            cats = {}
            for r in txs:
                if r["type"] == "expense":
                    cats[r["category"]] = cats.get(r["category"], 0) + r["amount"]
            savs = db.execute("SELECT * FROM savings WHERE user_id=?", (m["id"],)).fetchall()
            saved = sum(r["current"] for r in savs)
            recent = db.execute(
                "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
                (m["id"],)
            ).fetchall()
            result.append({
                "profile": {"id": m["id"], "name": m["name"], "color": m["color"]},
                "income": income, "expense": expense,
                "balance": income - expense, "saved": saved,
                "by_category": cats, "recent_tx": [dict(r) for r in recent]
            })
    return result

# --- Transactions ---
@app.get("/api/transactions")
def list_transactions(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/transactions")
def add_transaction(tx: Transaction, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    date_str = datetime.now().strftime("%d.%m")
    db.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
               (uid, user_id, tx.name, tx.amount, tx.category, tx.type, date_str, now))
    db.commit()
    return {"id": uid, "user_id": user_id, "name": tx.name, "amount": tx.amount,
            "category": tx.category, "type": tx.type, "date": date_str, "created_at": now}

@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: str, db: sqlite3.Connection = Depends(get_db)):
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
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute("INSERT INTO savings VALUES (?,?,?,?,?,?,?)",
               (uid, user_id, s.name, s.target, s.current, s.color, now))
    db.commit()
    return {"id": uid, "user_id": user_id, **s.dict(), "created_at": now}

@app.patch("/api/savings/{sav_id}/add")
def add_to_saving(sav_id: str, body: SavingAdd, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM savings WHERE id=?", (sav_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    new_val = min(dict(row)["current"] + body.amount, dict(row)["target"])
    db.execute("UPDATE savings SET current=? WHERE id=?", (new_val, sav_id))
    db.commit()
    return {"current": new_val}

@app.delete("/api/savings/{sav_id}")
def delete_saving(sav_id: str, db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM savings WHERE id=?", (sav_id,))
    db.commit()
    return {"ok": True}

# --- Reminders ---
@app.get("/api/reminders")
def list_reminders(user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM reminders WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/reminders")
def add_reminder(r: Reminder, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO reminders (id, user_id, text, tag, done, due_date, created_at) VALUES (?,?,?,?,?,?,?)",
        (uid, user_id, r.text, r.tag, 0, r.due_date, now))
    db.commit()
    return {"id": uid, "user_id": user_id, "text": r.text, "tag": r.tag,
            "done": False, "due_date": r.due_date, "created_at": now}

@app.patch("/api/reminders/{rem_id}")
def toggle_reminder(rem_id: str, body: ReminderToggle, db: sqlite3.Connection = Depends(get_db)):
    db.execute("UPDATE reminders SET done=? WHERE id=?", (1 if body.done else 0, rem_id))
    db.commit()
    return {"ok": True}

@app.delete("/api/reminders/{rem_id}")
def delete_reminder(rem_id: str, db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM reminders WHERE id=?", (rem_id,))
    db.commit()
    return {"ok": True}

# --- Push ---
@app.get("/api/push/vapid-public")
def get_vapid_public():
    return {"key": VAPID_PUBLIC}

@app.post("/api/push/subscribe")
def subscribe_push(body: PushSubscription, user_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    sub_json = json.dumps(body.subscription)
    endpoint = body.subscription.get("endpoint", "")
    db.execute("DELETE FROM push_subscriptions WHERE user_id=? AND subscription LIKE ?",
               (user_id, f'%{endpoint[:50]}%'))
    db.execute("INSERT INTO push_subscriptions VALUES (?,?,?,?)", (uid, user_id, sub_json, now))
    db.commit()
    return {"ok": True}

def send_push(user_id: str, title: str, body: str, db: sqlite3.Connection):
    try:
        from pywebpush import webpush, WebPushException
        subs = db.execute("SELECT subscription FROM push_subscriptions WHERE user_id=?", (user_id,)).fetchall()
        for row in subs:
            try:
                sub = json.loads(row["subscription"])
                webpush(subscription_info=sub, data=json.dumps({"title": title, "body": body}),
                        vapid_private_key=VAPID_PRIVATE, vapid_claims={"sub": VAPID_EMAIL})
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
def check_today(db: sqlite3.Connection = Depends(get_db)):
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT * FROM reminders WHERE due_date=? AND done=0", (today,)
    ).fetchall()
    by_user = {}
    for r in rows:
        uid = r["user_id"]
        if uid not in by_user:
            by_user[uid] = []
        by_user[uid].append(r["text"])
    for uid, tasks in by_user.items():
        count = len(tasks)
        body = tasks[0] if count == 1 else f"{tasks[0]} и ещё {count-1}"
        send_push(uid, f"Дела на сегодня ({count})", body, db)
    return {"notified": len(by_user)}

@app.get("/api/push/remind-finances")
def remind_finances(db: sqlite3.Connection = Depends(get_db)):
    users = db.execute("SELECT id FROM users").fetchall()
    for u in users:
        send_push(u["id"], "Финансы 💰", "Не забудь записать расходы за сегодня!", db)
    return {"ok": True}

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    index = os.path.join("static", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"error": "Frontend not found"}
