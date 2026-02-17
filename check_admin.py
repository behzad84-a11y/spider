import sqlite3
import os

db_path = 'trades.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT value FROM config WHERE key='admin_id';")
    row = cur.fetchone()
    if row:
        print(f"ADMIN_ID_IN_DB: {row[0]}")
    else:
        print("ADMIN_ID_NOT_FOUND")
    conn.close()
else:
    print("DB_NOT_FOUND")
