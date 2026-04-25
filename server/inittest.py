import sqlite3
import bcrypt
import datetime
from config import DB_PATH
from db.models import init_db

init_db(DB_PATH)

now = datetime.datetime.now().isoformat()
teams = [
    ("team_01", "Alpha 队", "pass01"),
    ("team_02", "Beta 队",  "pass02"),
    ("team_03", "Gamma 队", "pass03"),
    ("team_04", "Delta 队", "pass04"),
]
with sqlite3.connect(DB_PATH) as conn:
    for tid, name, pwd in teams:
        hashed = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT OR REPLACE INTO teams (id, name, password_hash, created_at) VALUES (?,?,?,?)",
            (tid, name, hashed, now)
        )
count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
print(f"插入完成，teams 表现有 {count} 条记录。")
