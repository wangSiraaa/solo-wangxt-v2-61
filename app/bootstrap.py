"""建表并写入离线虚构数据：python -m app.bootstrap"""
from .seed import init_db

if __name__ == "__main__":
    init_db()
    print("database initialized with offline fictional seed data")
