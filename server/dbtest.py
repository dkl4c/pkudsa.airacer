import sqlite3
import bcrypt
from config import DB_PATH

with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, password_hash FROM teams").fetchall()
    if not rows:
        print("DB 里没有任何队伍！")
    else:
        for row in rows:
            ok = bcrypt.checkpw(b"pass01", row["password_hash"].encode())
            print(
                f"{row['id']}  hash前缀={row['password_hash'][:20]}  verify(pass01)={ok}")
