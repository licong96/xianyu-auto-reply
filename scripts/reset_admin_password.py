# -*- coding: utf-8 -*-
"""重置 admin 密码为 admin123"""
import sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")

from common.utils.security import get_password_hash
import pymysql
import os

host = os.getenv("MYSQL_HOST", "127.0.0.1")
port = int(os.getenv("MYSQL_PORT", "3306"))
user = os.getenv("MYSQL_USER", "root")
password = os.getenv("MYSQL_PASSWORD", "")
db = os.getenv("MYSQL_DATABASE", "xianyu_data")

h = get_password_hash("admin123")
conn = pymysql.connect(host=host, port=port, user=user, password=password, database=db)
cur = conn.cursor()
cur.execute("SELECT id, username, status, role FROM xy_users WHERE username='admin' OR role='ADMIN'")
rows = cur.fetchall()
print("当前管理员账号:", rows)
if not rows:
    print("未找到 admin 用户!")
    sys.exit(1)
uid = rows[0][0]
cur.execute("UPDATE xy_users SET password_hash=%s, login_fail_count=0, login_locked_until=NULL, status='ACTIVE', updated_at=NOW() WHERE id=%s", (h, uid))
conn.commit()
print(f"已将用户 id={uid} 的密码重置为 admin123")

# 验证
cur.execute("SELECT password_hash FROM xy_users WHERE id=%s", (uid,))
stored = cur.fetchone()[0]
from common.utils.security import verify_password
print("验证通过:" , verify_password("admin123", stored))
cur.close()
conn.close()
