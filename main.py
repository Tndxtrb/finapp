from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, uuid, json
from datetime import datetime, date
from pywebpush import webpush, WebPushException

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL DEFAULT '#60a5fa',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            type TEXT NOT NULL,
            date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS savings (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            name TEXT NOT NULL,
            target REAL NOT NULL,
            current REAL NOT NULL DEFAULT 0,
            color TEXT DEFAULT '#185FA5',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            text TEXT NOT NULL,
            tag TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            subscription TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    try:
        conn.execute("ALTER TABLE reminders ADD COLUMN due_date TEXT")
        conn.commit()
    except:
        pass
    conn.close()

init_db()

class ProfileCreate(BaseModel):
    name: str
    color: str = "#60a5fa"

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

# --- Profiles ---
@app.get("/api/profiles")
def list_profiles(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM profiles ORDER BY created_at ASC").fetchall()
    return [dict(r) for r in rows]

@app.post("/api/profiles")
def create_profile(p: ProfileCreate, db: sqlite3.Connection = Depends(get_db)):
    existing = db.execute("SELECT * FROM profiles WHERE name=?", (p.name,)).fetchone()
    if existing:
        return dict(existing)
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute("INSERT INTO profiles VALUES (?,?,?,?)", (uid, p.name, p.color, now))
    db.commit()
    return {"id": uid, "name": p.name, "color": p.color, "created_at": now}

@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str, db: sqlite3.Connection = Depends(get_db)):
    for table in ["transactions", "savings", "reminders", "push_subscriptions"]:
        db.execute(f"DELETE FROM {table} WHERE profile_id=?", (profile_id,))
    db.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    db.commit()
    return {"ok": True}

# --- Transactions ---
@app.get("/api/transactions")
def list_transactions(profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM transactions WHERE profile_id=? ORDER BY created_at DESC", (profile_id,)
    ).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/transactions")
def add_transaction(tx: Transaction, profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    date_str = datetime.now().strftime("%d.%m")
    db.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
               (uid, profile_id, tx.name, tx.amount, tx.category, tx.type, date_str, now))
    db.commit()
    return {"id": uid, "profile_id": profile_id, "name": tx.name, "amount": tx.amount,
            "category": tx.category, "type": tx.type, "date": date_str, "created_at": now}

@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: str, db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    db.commit()
    return {"ok": True}

# --- Savings ---
@app.get("/api/savings")
def list_savings(profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM savings WHERE profile_id=? ORDER BY created_at ASC", (profile_id,)
    ).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/savings")
def add_saving(s: Saving, profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute("INSERT INTO savings VALUES (?,?,?,?,?,?,?)",
               (uid, profile_id, s.name, s.target, s.current, s.color, now))
    db.commit()
    return {"id": uid, "profile_id": profile_id, **s.dict(), "created_at": now}

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
def list_reminders(profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM reminders WHERE profile_id=? ORDER BY created_at DESC", (profile_id,)
    ).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/reminders")
def add_reminder(r: Reminder, profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO reminders (id, profile_id, text, tag, done, due_date, created_at) VALUES (?,?,?,?,?,?,?)",
        (uid, profile_id, r.text, r.tag, 0, r.due_date, now))
    db.commit()
    return {"id": uid, "profile_id": profile_id, "text": r.text, "tag": r.tag,
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
def subscribe_push(body: PushSubscription, profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    uid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    sub_json = json.dumps(body.subscription)
    endpoint = body.subscription.get("endpoint", "")
    db.execute("DELETE FROM push_subscriptions WHERE profile_id=? AND subscription LIKE ?",
               (profile_id, f'%{endpoint[:50]}%'))
    db.execute("INSERT INTO push_subscriptions VALUES (?,?,?,?)", (uid, profile_id, sub_json, now))
    db.commit()
    return {"ok": True}

def send_push(profile_id: str, title: str, body: str, db: sqlite3.Connection):
    subs = db.execute(
        "SELECT subscription FROM push_subscriptions WHERE profile_id=?", (profile_id,)
    ).fetchall()
    for row in subs:
        try:
            sub = json.loads(row["subscription"])
            webpush(
                subscription_info=sub,
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": VAPID_EMAIL}
            )
        except WebPushException as e:
            if "410" in str(e) or "404" in str(e):
                db.execute("DELETE FROM push_subscriptions WHERE subscription=?", (row["subscription"],))
                db.commit()

@app.post("/api/push/test")
def test_push(profile_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    send_push(profile_id, "Финансы", "Уведомления работают! 🎉", db)
    return {"ok": True}

@app.get("/api/push/check-today")
def check_today(db: sqlite3.Connection = Depends(get_db)):
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT r.*, p.name as pname FROM reminders r JOIN profiles p ON r.profile_id=p.id WHERE r.due_date=? AND r.done=0",
        (today,)
    ).fetchall()
    by_profile = {}
    for r in rows:
        pid = r["profile_id"]
        if pid not in by_profile:
            by_profile[pid] = []
        by_profile[pid].append(r["text"])
    for pid, tasks in by_profile.items():
        count = len(tasks)
        body = tasks[0] if count == 1 else f"{tasks[0]} и ещё {count-1}"
        send_push(pid, f"Дела на сегодня ({count})", body, db)
    return {"notified": len(by_profile)}

@app.get("/api/push/remind-finances")
def remind_finances(db: sqlite3.Connection = Depends(get_db)):
    profiles = db.execute("SELECT id FROM profiles").fetchall()
    for p in profiles:
        send_push(p["id"], "Финансы 💰", "Не забудь записать расходы за сегодня!", db)
    return {"ok": True}

# --- Joint ---
@app.get("/api/joint")
def get_joint(db: sqlite3.Connection = Depends(get_db)):
    profiles = [dict(r) for r in db.execute("SELECT * FROM profiles ORDER BY created_at ASC").fetchall()]
    result = []
    for p in profiles:
        txs = db.execute("SELECT * FROM transactions WHERE profile_id=?", (p["id"],)).fetchall()
        income = sum(r["amount"] for r in txs if r["type"] == "income")
        expense = sum(r["amount"] for r in txs if r["type"] == "expense")
        cats = {}
        for r in txs:
            if r["type"] == "expense":
                cats[r["category"]] = cats.get(r["category"], 0) + r["amount"]
        savs = db.execute("SELECT * FROM savings WHERE profile_id=?", (p["id"],)).fetchall()
        saved = sum(r["current"] for r in savs)
        recent = db.execute(
            "SELECT * FROM transactions WHERE profile_id=? ORDER BY created_at DESC LIMIT 5",
            (p["id"],)
        ).fetchall()
        result.append({
            "profile": p, "income": income, "expense": expense,
            "balance": income - expense, "saved": saved,
            "by_category": cats, "recent_tx": [dict(r) for r in recent]
        })
    return result

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    index = os.path.join("static", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"error": "Frontend not found"}