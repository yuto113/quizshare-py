# ============================================================
# pythonanywhere_wsgi.py
#
# PythonAnywhere の「WSGI configuration file」に
# このファイルの中身をそのままコピー&ペーストしてね。
#
# 「myusername」のところは、自分のPythonAnywhereのユーザー名に置き換えてね！
# ============================================================

import sys
import os

# ---- 1. プロジェクトのフォルダをPythonに教える ----
# ここのパスは「自分のユーザー名」に書き換えてね
project_home = '/home/myusername/quizshare-py'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# ---- 2. 環境変数を設定する ----
# PythonAnywhereには .env ファイルを読む機能がないので、
# ここに直接書いてしまうのが一番かんたんだよ

# ひみつの鍵(32文字以上のランダムな文字列に変えてね)
# 作り方: python -c "import secrets; print(secrets.token_hex(32))"
os.environ['PEPPER'] = 'ここに32文字以上のランダムな文字列を入れてね'
os.environ['FLASK_SECRET_KEY'] = 'ここに別の32文字以上のランダムな文字列を入れてね'

# SQLiteのファイルを保存する場所
# PythonAnywhereのホームフォルダに置くと消えないよ
os.environ['SQLITE_PATH'] = '/home/myusername/quizshare.db'

# 本番モード(0のままでOK)
os.environ['FLASK_DEBUG'] = '0'

# ---- 3. Flaskアプリを読み込む ----
from app import app as application
