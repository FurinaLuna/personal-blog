"""定点复现：查明 /auth/me 与 /site/profile 返回 500 的根因。

用法：
    backend/.venv/Scripts/python.exe tools/e2e_live/probe.py
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
PORT = 8098
BASE = f"http://127.0.0.1:{PORT}"
API = BASE + "/api/v1"

WORKDIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if WORKDIR is None:
    import glob

    candidates = sorted(glob.glob(r"C:/Users/ZHANGC~1/AppData/Local/Temp/blog-e2e-*"))
    WORKDIR = Path(candidates[-1])
print(f"复用工作目录：{WORKDIR}")

DB_PATH = WORKDIR / "e2e.db"
STORAGE_DIR = WORKDIR / "storage"
LOG = WORKDIR / "probe.log"

env = os.environ.copy()
env.update({
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH.as_posix()}",
    "STORAGE_DIR": str(STORAGE_DIR),
    "APP_ENV": "testing",
    "DEBUG": "true",
    "DB_AUTO_CREATE": "false",
    "JWT_SECRET_KEY": secrets.token_hex(32),
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "NewE2ePass@2468",
    "SEED_DEMO_DATA": "true",
    "RATE_LIMIT_ENABLED": "false",
    "LOG_JSON": "false",
    "LOG_LEVEL": "DEBUG",
})

logf = LOG.open("w", encoding="utf-8")
proc = subprocess.Popen(
    [str(PY), "-m", "uvicorn", "app.main:app", "--app-dir", "src",
     "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=logf, stderr=subprocess.STDOUT,
)

try:
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/ready", timeout=3).status_code == 200:
                break
        except Exception:
            time.sleep(1)

    admin_token = None
    for pwd in ["NewE2ePass@2468", "E2eTest@13579"]:
        r = httpx.post(f"{API}/auth/login", json={"username": "admin", "password": pwd}, timeout=20)
        print(f"login({pwd}) -> {r.status_code}")
        if r.status_code == 200:
            admin_token = r.json()["access_token"]
            break

    if admin_token:
        r = httpx.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {admin_token}"}, timeout=20)
        print(f"\nGET /auth/me -> {r.status_code}\n{r.text[:800]}")
    r = httpx.get(f"{API}/site/profile", timeout=20)
    print(f"\nGET /site/profile -> {r.status_code}\n{r.text[:800]}")
    r = httpx.get(f"{API}/articles?page=1&page_size=2", timeout=20)
    print(f"\nGET /articles -> {r.status_code}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()
    logf.close()

print("\n================ 服务端日志 ================")
print(LOG.read_text(encoding="utf-8", errors="replace")[-6000:])
