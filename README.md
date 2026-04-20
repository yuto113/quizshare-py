# クイズシェア 🎯 (Flask / Python版)

グループ内でクイズを出し合い、**タイム計測・採点・難易度評価・感想投稿・統計表示** ができる
Webアプリです。Python + Flask + HTML + 最小限のJavaScript で作られています。

---

## ✨ 主な機能

- グループを作って合言葉(ID)で仲間を招待
- クイズ投稿(記述式 / 選択式 / タグ付き)
- タイム計測つきの採点
- 感想(★1〜5 + コメント)と統計表示
- 管理者モード `/<ID>/setting/` で閲覧のみ切替・グループ削除
- 利用規約・プライバシーポリシー・ヘルプページ内蔵
- 違法投稿・名誉毀損などに対する<strong>同意チェックボックス</strong>を各所に配置し、
  管理者が責任を負わない設計

---

## 📁 ファイル構成

```
quizshare-py/
├── app.py                # Flask本体。ルーティング・API・DB・認証
├── requirements.txt      # Python依存ライブラリ
├── Procfile              # Railway用の起動コマンド
├── runtime.txt           # Pythonのバージョン
├── railway.json          # Railway設定
├── .env.example          # 環境変数のサンプル
├── .gitignore
├── README.md
├── static/
│   ├── style.css         # 共通スタイル
│   └── app.js            # 共通の通信・トースト・モーダル処理
└── templates/
    ├── base.html         # 全ページ共通のひな形(ヘッダー=左チーム名、右ナビ+ログアウト)
    ├── entry.html        # トップ(グループログイン)
    ├── create_group.html # 新しいグループ作成
    ├── group.html        # グループ画面(クイズ一覧+追加)
    ├── answer.html       # クイズ回答(タイマー+結果+感想+統計)
    ├── admin.html        # 管理者画面(/<ID>/setting/)
    ├── terms.html        # 利用規約
    ├── privacy.html      # プライバシーポリシー
    ├── help.html         # ヘルプ
    ├── 404.html
    └── 500.html
```

---

## 💻 ローカル開発

### 1. 依存ライブラリをインストール

```bash
cd quizshare-py
python -m venv .venv
source .venv/bin/activate        # Windows は .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 環境変数を設定

```bash
cp .env.example .env
# .env をエディタで開いて PEPPER と FLASK_SECRET_KEY を書き換える
python -c "import secrets; print(secrets.token_hex(32))"  # でランダム文字列が作れる
```

### 3. 起動

```bash
python app.py
```

→ `http://localhost:5000` にアクセス。

データベースは `DATABASE_URL` が空なら自動で **SQLite**(ファイルベース、設定不要)になります。

---

## 🚀 Railwayへのデプロイ

### 1. GitHubにpush

```bash
git init
git add .
git commit -m "initial commit (Flask版)"
git branch -M main
git remote add origin https://github.com/<your-name>/quizshare-py.git
git push -u origin main
```

### 2. Railwayでプロジェクト作成

1. [Railway](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. リポジトリを選択 → 自動で Python/Flask プロジェクトとしてビルド開始
3. ビルドに `requirements.txt`、起動に `Procfile` が使われます

### 3. PostgreSQLを追加

同じプロジェクトで **+ New** → **Database** → **Add PostgreSQL**
→ `DATABASE_URL` が自動で注入されるので設定不要です。

### 4. 環境変数を設定

Webサービスの **Variables** タブで以下を追加:

| 変数名              | 値                                | 説明 |
|---------------------|----------------------------------|------|
| `PEPPER`            | 32文字以上のランダム文字列        | グループIDやパスワードの暗号化に使用 |
| `FLASK_SECRET_KEY`  | 別の32文字以上ランダム文字列      | セッションクッキー暗号化用 |
| `FLASK_DEBUG`       | `0`                              | 本番モード |

ランダム文字列の生成:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

⚠️ **PEPPER は一度決めたら絶対に変更しないこと**
(変更すると既存のグループにアクセスできなくなります)

### 5. ドメインを発行

**Settings** → **Networking** → **Generate Domain** で公開URLが取得できます。

以降、`main` ブランチへの push で自動再デプロイされます。

---

## 🔐 セキュリティ設計

| 脅威 | 対策 |
|------|------|
| DBダンプからグループID漏洩 | `HMAC-SHA256(PEPPER, id)` でハッシュ化して保存 |
| 管理者パスワード漏洩 | `scrypt` + ランダムsalt(N=16384)でハッシュ化 |
| 答えのクライアント漏洩 | 答え合わせはサーバー側。ブラウザには答えが渡らない |
| 他グループへの侵入 | すべてのAPIでセッションのグループIDを検証 |
| SQLインジェクション | プレースホルダ(`%s` / `?`)のみ使用、文字列連結禁止 |
| 総当たり攻撃 | IP別に1分単位のレート制限(作成10/分、管理者ログイン10/分) |
| セッション乗っ取り | HttpOnly・SameSite=Lax・本番はSecureフラグ付き |
| 管理者の誤削除 | グループIDの再入力 + 責任確認チェックの2段階確認 |

---

## 📝 設計のポイント

### コーディングルール(ユーザー指定)に従った設計

1. ✅ **基本Python** — ロジック・認証・DB操作・採点はすべて `app.py` 内
2. ✅ **デザインはHTML** — レイアウトは Jinja2 テンプレート、見た目は CSS
3. ✅ **JSは必要な時だけ** — すべてのJSブロックの先頭に `#` で理由をコメント
4. ✅ **新中1にもわかる日本語コメント** — 専門用語はひらがな + たとえ話で説明
5. ✅ **一部リロードで動く** — すべてのフォーム送信を `fetch` で行い、ページ全体はリロードしない
6. ✅ **法令遵守の同意チェック** — グループ作成・クイズ投稿・感想投稿・グループ削除の
    4箇所に「違法ではないこと」「責任の所在」のチェックボックスを配置

### ヘッダーデザイン

- **左端**: チーム名(ログイン前は「クイズシェア」、ログイン後はグループ名)
- **右端**: クイズ一覧 / ヘルプ / 利用規約 / プライバシー / ログアウト

### データベースの自動選択

- `DATABASE_URL` が設定されていれば → **PostgreSQL**(本番)
- 空なら → **SQLite**(ローカル)
- 両方で動くよう SQL は `%s` プレースホルダで書き、実行時に `?` へ変換

---

## 📜 ライセンス

MIT

---

このwebサイトはclaude.aiと共に作成しています。
