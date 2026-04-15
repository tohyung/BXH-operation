"""
auto_push.py
============
Chạy sau export_leaderboard.py để tự động push leaderboard.json lên GitHub
Yêu cầu: git đã cài, đã clone repo, đã config credential

Cách dùng trong Task Scheduler:
  python export_leaderboard.py && python auto_push.py
"""

import os
import subprocess
from datetime import datetime

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = "leaderboard.json"

def run(cmd):
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, shell=True)
    if result.returncode != 0:
        print(f"  ❌ Lỗi: {result.stderr.strip()}")
    else:
        print(f"  ✓ {result.stdout.strip() or cmd}")
    return result.returncode == 0

def push():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Push leaderboard.json lên GitHub...")
    run(f'git add {JSON_FILE}')
    run(f'git commit -m "Update leaderboard {datetime.now().strftime("%Y-%m-%d %H:%M")}"')
    run('git push')
    print("✅ Done!")

if __name__ == "__main__":
    push()
