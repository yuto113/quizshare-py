# クイズシェア 🎯 - PythonAnywhere 無料デプロイ版

**完全無料・期限なし・クレカ不要**で使える
Python (Flask) + SQLite のWebアプリです。

---

## 🌐 なぜPythonAnywhere?

| 比較 | PythonAnywhere | Render | Railway |
|-----|--------------|--------|---------|
| 料金 | **ずっと無料** | 無料(DB30日で消える) | クレカ必要 |
| DB | SQLite永久保存 | PostgreSQL30日のみ | PostgreSQL |
| 寝る | ❌なし | 😴15分で寝る | - |
| 難しさ | ★☆☆ かんたん | ★★☆ | ★★★ |

**→ PythonAnywhereが一番かんたんで、ずっと無料で使える！**

---

## 🚀 デプロイ手順 (全部で約15分)

### ステップ1: アカウント作成

1. [https://www.pythonanywhere.com](https://www.pythonanywhere.com) にアクセス
2. 「Start running Python Online」→「Create a Beginner account」
3. ユーザー名・パスワード・メールを設定(クレカ不要!)

---

### ステップ2: ファイルをアップロード

**ダッシュボード → Files タブ** を開く

ホーム(`/home/あなたのユーザー名/`)に `quizshare-py` フォルダを作って、
以下のファイルをすべてアップロード:

```
quizshare-py/
├── app.py
├── requirements.txt
├── static/
│   ├── style.css
│   └── app.js
└── templates/
    ├── base.html
    ├── entry.html
    ├── create_group.html
    ├── group.html
    ├── answer.html
    ├── admin.html
    ├── terms.html
    ├── privacy.html
    ├── help.html
    ├── 404.html
    └── 500.html
```

> **ヒント**: zipファイルをアップロードして、Bash Consoleで展開することもできるよ↓
> ```bash
> cd ~
> unzip quizshare-flask.zip
> mv quizshare-py quizshare-py  # すでにこの名前
> ```

---

### ステップ3: ライブラリをインストール

**ダッシュボード → Consoles → Bash** を開いて入力:

```bash
pip3 install flask python-dotenv --user
```

---

### ステップ4: Webアプリを設定

1. **ダッシュボード → Web タブ** を開く
2. 「Add a new web app」をクリック
3. 「Next」→「Flask」→「Python 3.10」→「Next」
4. Pathの入力欄に以下を入力:
   ```
   /home/あなたのユーザー名/quizshare-py/app.py
   ```
5. 「Next」で完了

---

### ステップ5: WSGIファイルを書き換える

1. **Web タブ** の「WSGI configuration file」リンクをクリック
2. ファイルの中身を**全部消して**、以下を貼り付ける:

```python
import sys
import os

# プロジェクトのフォルダ(「myusername」を自分のユーザー名に変えてね!)
project_home = '/home/myusername/quizshare-py'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# ひみつの鍵を設定(下の文字列は変えてね!)
os.environ['PEPPER'] = 'ここに32文字以上のランダムな文字列'
os.environ['FLASK_SECRET_KEY'] = 'ここに別の32文字以上のランダムな文字列'
os.environ['SQLITE_PATH'] = '/home/myusername/quizshare.db'
os.environ['FLASK_DEBUG'] = '0'

from app import app as application
```

3. ランダムな文字列の作り方(BashConsoleで):
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
   2回実行して、1つ目をPEPPER、2つ目をFLASK_SECRET_KEYに使ってね

4. **「Save」ボタン**を押す

---

### ステップ6: 起動!

1. **Web タブ**に戻る
2. 緑の「**Reload**」ボタンを押す
3. `https://あなたのユーザー名.pythonanywhere.com` にアクセス!

---

## ✅ うまくいったか確認

- `https://あなたのユーザー名.pythonanywhere.com` にアクセス
- ログイン画面が出ればOK!
- 最初にグループを作成してみよう

---

## ⚠️ よくあるエラーと対処法

### エラーログの見方
Web タブ → 「Error log」リンクをクリック

### `ModuleNotFoundError: No module named 'flask'`
```bash
pip3 install flask --user
```
を実行して、Webタブで「Reload」

### `OperationalError: unable to open database file`
WSGIファイルの `SQLITE_PATH` のパスに「myusername」→自分のユーザー名に変えたか確認!

### ページが真っ白になる
- Error logを確認
- WSGIファイルの `project_home` のパスを確認

---

## 🔄 ファイルを更新したいとき

1. FilesタブでファイルをアップロードしてHire
2. Webタブで「Reload」ボタンを押すだけ!

---

## 💡 無料プランの制限

| 制限 | 内容 |
|-----|-----|
| CPU | 1日100秒まで(クイズアプリなら十分) |
| ディスク | 512MB |
| 外部アクセス | 一部サイトへのHTTPのみ(このアプリは関係なし) |
| アプリ数 | 1個まで |

---

## 🗄️ データはどこに保存される?

`/home/あなたのユーザー名/quizshare.db` というファイルに保存されるよ。
SQLiteというファイル型のデータベースで、サーバーを再起動しても消えない。

バックアップしたいときは、FilesタブからこのDBファイルをダウンロードできるよ!

---

## 🔒 セキュリティメモ

- PEPPERとFLASK_SECRET_KEYは必ず変えること(デフォルト値では危ない!)
- WSGIファイルはWebからは見えないので、ここに書いても安全
- PythonAnywhereは自動でHTTPS(鍵マーク)になるよ

---

このwebサイトはclaude.aiと共に作成しています。
