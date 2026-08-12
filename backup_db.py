#!/usr/bin/env python3
"""DBを毎日バックアップ。7日分を保持"""
import sqlite3, os, time, glob, gzip, shutil

DB = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
DIR = os.path.expanduser('~/backups/db')
KEEP = 7

os.makedirs(DIR, exist_ok=True)
stamp = time.strftime('%Y%m%d_%H%M')
tmp = f'{DIR}/quizshare_{stamp}.db'

# SQLiteの正しいバックアップ方法(コピー中の破損を防ぐ)
src = sqlite3.connect(DB)
dst = sqlite3.connect(tmp)
with dst:
    src.backup(dst)
dst.close(); src.close()

# 圧縮
with open(tmp, 'rb') as f_in, gzip.open(tmp + '.gz', 'wb') as f_out:
    shutil.copyfileobj(f_in, f_out)
os.remove(tmp)

sz = os.path.getsize(tmp + '.gz') / 1e6
print(f'✅ {tmp}.gz ({sz:.1f}MB)')

# 古いものを削除
files = sorted(glob.glob(f'{DIR}/quizshare_*.db.gz'))
for f in files[:-KEEP]:
    os.remove(f); print(f'  削除: {os.path.basename(f)}')
print(f'保持: {len(files[-KEEP:])}世代')
