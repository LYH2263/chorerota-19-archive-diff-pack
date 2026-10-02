from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY, name TEXT, active INT, data_quality TEXT);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, title TEXT, weight INT, data_quality TEXT);
CREATE TABLE IF NOT EXISTS weeks(id INTEGER PRIMARY KEY, label TEXT, status TEXT, pack_id INT);
CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY AUTOINCREMENT, week_id INT, day INT, task_id INT, member_id INT);
CREATE TABLE IF NOT EXISTS swap_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, week_id INT, a_day INT, a_task INT, b_day INT, b_task INT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS week_packs(id INTEGER PRIMARY KEY AUTOINCREMENT, week_id INT, version INT, sealed_at TEXT, cell_count INT, checksum TEXT, body TEXT);
"""

def _ensure_columns(c):
    """老库补列：CREATE IF NOT EXISTS 不会给已有 weeks 表加 pack_id。"""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(weeks)")}
    if "pack_id" not in cols:
        c.execute("ALTER TABLE weeks ADD COLUMN pack_id INT")
    c.commit()

def init_db():
    c = connect()
    c.executescript(SCHEMA)
    _ensure_columns(c)
    if c.execute("SELECT COUNT(*) c FROM members").fetchone()["c"] == 0:
        c.executemany("INSERT INTO members(name,active,data_quality) VALUES (?,?,?)", [
            ("阿明", 1, "clean"), ("小雨", 1, "clean"), ("爷爷", 1, "clean"),
            ("幽灵成员", 0, "dirty"),
        ])
        c.executemany("INSERT INTO tasks(title,weight,data_quality) VALUES (?,?,?)", [
            ("洗碗", 1, "clean"), ("倒垃圾", 1, "clean"), ("扫地", 2, "clean"),
            ("负权重任务", -1, "dirty"),
        ])
        c.execute("INSERT INTO weeks(label,status) VALUES ('第12周','draft')")
        c.execute("INSERT INTO settings(key,value) VALUES ('household','绿纸之家')")
        c.commit()
    c.close()
