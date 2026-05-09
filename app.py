import site as _site
import sys
sys.path.insert(0, _site.getusersitepackages())
# -*- coding: utf-8 -*-
# ====================================================================
# 暗号化ヘルパー(問題文・答え・グループ名・作者名・タグなどを暗号化)
# ====================================================================
from cryptography.fernet import Fernet, InvalidToken

_fernet_instance = None
def get_fernet():
    global _fernet_instance
    if _fernet_instance is None:
        key = os.environ.get('ENCRYPTION_KEY', '')
        if not key:
            raise RuntimeError('ENCRYPTION_KEY が設定されていないよ(WSGIファイルを確認)')
        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet_instance

def encrypt_text(text):
    if not text:
        return text
    if isinstance(text, str):
        text = text.encode('utf-8')
    return get_fernet().encrypt(text).decode('ascii')

def decrypt_text(text):
    if not text:
        return text
    if isinstance(text, bytes):
        text = text.decode('ascii')
    try:
        return get_fernet().decrypt(text.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return text

# ====================================================================
# クイズシェア (Flask版)
# ぜんぶのサーバーのしごとがこのファイルにまとまっているよ。
# ====================================================================

import os
import json
import time
import hmac
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, abort, g,
)

# 本番で .env がなくても大丈夫なように try
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ====================================================================
# 暗号化ヘルパー
# ====================================================================
from cryptography.fernet import Fernet, InvalidToken

_fernet = None
def get_fernet():
    global _fernet
    if _fernet is None:
        key = os.environ.get('ENCRYPTION_KEY', '')
        if not key:
            raise RuntimeError('ENCRYPTION_KEY が設定されていないよ')
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet

def enc(text):
    # テキストを暗号化して返す
    if not text:
        return text
    try:
        return get_fernet().encrypt(str(text).encode('utf-8')).decode('ascii')
    except Exception:
        return text

def dec(text):
    # 暗号化テキストを復号して返す
    if not text:
        return text
    try:
        return get_fernet().decrypt(str(text).encode('ascii')).decode('utf-8')
    except Exception:
        return text

# ====================================================================
# 1. Flaskアプリを作る
# ====================================================================
# Flask は「サーバー」を作るための道具箱。app がその本体。
app = Flask(__name__)

# セッション(ログイン状態を覚えておく入れ物)の暗号化キー
# これがないと他の人になりすまされちゃう。
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-' + secrets.token_hex(16))

# セッションのクッキーを安全にする設定
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # JavaScriptから読めないようにする
    SESSION_COOKIE_SAMESITE='Lax',  # 他のサイトから送られないようにする
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_DEBUG', '0') != '1',  # HTTPSの時だけクッキーを送る(本番用)
    MAX_CONTENT_LENGTH=256 * 1024,  # 大きすぎるデータ(256KB超)は拒否
)


# ====================================================================
# 2. ひみつの鍵(ペッパー)
# グループIDをぐちゃぐちゃに変換して保存するときに使うよ。
# これがないと、もしDBが盗まれたらIDがバレちゃう。
# ====================================================================
PEPPER = os.environ.get('PEPPER', '')
if len(PEPPER) < 16:
    print('\n⚠️  けいこく: PEPPER が短すぎるよ。本番では32文字以上にしてね。\n')
    if not PEPPER:
        PEPPER = 'fallback-insecure-pepper-DO-NOT-USE-IN-PROD'


def hash_group_id(group_id: str) -> str:
    # HMAC-SHA256 = ペッパーと合体させてぐちゃぐちゃにする方法
    # 同じIDは必ず同じ文字列になるから、DB検索に使える
    return hmac.new(PEPPER.encode(), str(group_id).encode(), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    # パスワードをぐちゃぐちゃに変換して保存するための関数
    # scrypt = パスワード保存で安全と言われている方法
    # salt(ソルト) = パスワードごとにランダムな味付けをする小さなデータ
    salt = secrets.token_hex(16)
    n, r, p = 16384, 8, 1
    dk = hashlib.scrypt(password.encode(), salt=salt.encode(), n=n, r=r, p=p, dklen=64)
    return f'scrypt${n}${r}${p}${salt}${dk.hex()}'


def verify_password(password: str, stored: str) -> bool:
    # 入力されたパスワードが、保存されているハッシュと合ってるかチェック
    try:
        parts = stored.split('$')
        if len(parts) != 6 or parts[0] != 'scrypt':
            return False
        _, n, r, p, salt, expected = parts
        dk = hashlib.scrypt(
            password.encode(), salt=salt.encode(),
            n=int(n), r=int(r), p=int(p), dklen=64,
        )
        # タイミング攻撃(こたえる時間で推測する攻撃)をふせぐ比較方法
        return hmac.compare_digest(dk.hex(), expected)
    except Exception:
        return False


# ====================================================================
# 3. データベースに接続する
# Railway(本番) → PostgreSQL を使う
# ローカル(家のパソコン) → SQLite(ただのファイル)を使う
# ====================================================================
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
USE_POSTGRES = DATABASE_URL.startswith('postgres')

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    # postgres:// を postgresql:// に修正(古い形式対応)
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

    # SSLが必要か判定(Railwayは必要)
    need_ssl = 'railway' in DATABASE_URL or 'rlwy.net' in DATABASE_URL or 'sslmode' not in DATABASE_URL

    # 接続プール: 毎回つなぐのは遅いから、つながった状態を何個か保持しとく
    _pool = psycopg2.pool.SimpleConnectionPool(
        1, 10, DATABASE_URL,
        sslmode='require' if need_ssl else 'prefer',
    )

    @contextmanager
    def get_db():
        # with構文でDBを使うと、終わったら自動で返却される
        conn = _pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _pool.putconn(conn)

    def make_cursor(conn):
        # 結果を「列の名前でアクセスできる辞書」として取れるカーソルを作る
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    PH = '%s'  # プレースホルダ(値の入る場所)のマーク
else:
    # SQLite(小さいファイルベースのDB)を使う
    SQLITE_PATH = os.environ.get('SQLITE_PATH', 'quizshare.db')

    @contextmanager
    def get_db():
        conn = sqlite3.connect(SQLITE_PATH)
        # 列名でアクセスできるように設定
        conn.row_factory = sqlite3.Row
        # 外部キー制約(他のテーブルとのつながり)を有効にする
        conn.execute('PRAGMA foreign_keys = ON')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def make_cursor(conn):
        return conn.cursor()

    PH = '?'


def q(sql: str) -> str:
    """SQL文の %s を、DBの種類にあわせた記号にぜんぶ置きかえる"""
    # ※DBによって値の指定記号が違うから、揃えてあげる
    return sql.replace('%s', PH)


def init_db():
    """DBの表(テーブル)を作る。はじめて動かしたときだけ新しく作られる。"""
    with get_db() as conn:
        cur = make_cursor(conn)

        if USE_POSTGRES:
            # UUID(世界で一つだけのID)を作る機能をONにする
            cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
            id_default = 'UUID PRIMARY KEY DEFAULT gen_random_uuid()'
            time_default = 'TIMESTAMPTZ NOT NULL DEFAULT NOW()'
            tags_type = 'TEXT NOT NULL DEFAULT \'\''  # カンマ区切り文字列にして互換性を保つ
            options_type = 'TEXT'
        else:
            id_default = 'TEXT PRIMARY KEY'
            time_default = 'TEXT NOT NULL DEFAULT (datetime(\'now\'))'
            tags_type = 'TEXT NOT NULL DEFAULT \'\''
            options_type = 'TEXT'

        # グループの表
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS groups (
                id {id_default},
                group_id_hash TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '#E85A8A',
                admin_password_hash TEXT,
                view_only INTEGER NOT NULL DEFAULT 0,
                created_at {time_default}
            )
        ''')

        # クイズの表
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS quizzes (
                id {id_default},
                group_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                class_name TEXT NOT NULL DEFAULT '',
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                answer_options {options_type},
                has_options INTEGER NOT NULL DEFAULT 0,
                tags {tags_type},
                created_at {time_default}
            )
        ''')

        # 挑戦した記録の表(タイム・正誤)
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS attempts (
                id {id_default},
                quiz_id TEXT NOT NULL,
                correct INTEGER NOT NULL,
                time_ms INTEGER NOT NULL,
                created_at {time_default}
            )
        ''')

        # 感想の表(難易度・コメント)
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS feedbacks (
                id {id_default},
                quiz_id TEXT NOT NULL,
                difficulty INTEGER NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                created_at {time_default}
            )
        ''')

        # 速く検索するための「しおり(インデックス)」
        cur.execute('CREATE INDEX IF NOT EXISTS idx_quizzes_group ON quizzes(group_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_attempts_quiz ON attempts(quiz_id)')

        # クイズ編集パスワード・複数正解・画像テーブル追加
        if USE_POSTGRES:
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS edit_password_hash TEXT")
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS answers TEXT")
        else:
            try:
                cur.execute("ALTER TABLE quizzes ADD COLUMN edit_password_hash TEXT")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE quizzes ADD COLUMN answers TEXT")
            except Exception:
                pass

        # 画像テーブル
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS quiz_images (
                id {id_default},
                quiz_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                created_at {time_default}
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_images_quiz ON quiz_images(quiz_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_feedbacks_quiz ON feedbacks(quiz_id)')

        # グループパスワード列を追加
        if USE_POSTGRES:
            cur.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS group_password_hash TEXT")
        else:
            try:
                cur.execute("ALTER TABLE groups ADD COLUMN group_password_hash TEXT")
            except Exception:
                pass

        # ランキングの表
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS rankings (
                id {id_default},
                quiz_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                time_ms INTEGER NOT NULL,
                created_at {time_default}
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_rankings_quiz ON rankings(quiz_id)')
        if USE_POSTGRES:
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS explanation TEXT DEFAULT ''")
        else:
            try:
                cur.execute("ALTER TABLE quizzes ADD COLUMN explanation TEXT DEFAULT ''")
            except Exception:
                pass
        cur.execute(f'''CREATE TABLE IF NOT EXISTS quiz_sets (
                id {id_default},
                group_id TEXT NOT NULL,
                name TEXT NOT NULL,
                quiz_ids TEXT NOT NULL,
                created_at {time_default}
            )''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_sets_group ON quiz_sets(group_id)')
        if USE_POSTGRES:
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS hint TEXT DEFAULT ''")
        else:
            try:
                cur.execute("ALTER TABLE quizzes ADD COLUMN hint TEXT DEFAULT ''")
            except Exception:
                pass

    print('✓ データベースの準備ができたよ')


def new_id() -> str:
    """SQLite用の新しいID(UUIDみたいなランダム文字列)を作る"""
    return secrets.token_hex(16)


# ====================================================================
# 4. セッション管理のための小さな関数たち
# ====================================================================
def current_group():
    """いまログインしているグループの情報を返す。ログインしてなかったらNone。"""
    gid = session.get('group_id')
    if not gid:
        return None
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT * FROM groups WHERE group_id_hash = %s'), (hash_group_id(gid),))
        row = cur.fetchone()
        return dict(row) if row else None


def require_group():
    """グループにログインしてないとダメなページで使う。なかったら401エラー。"""
    grp = current_group()
    if not grp:
        return None
    g.group = grp
    g.group_id_raw = session.get('group_id')
    return grp


def admin_logged_in_for(group_uuid: str) -> bool:
    """管理者としてログインしてるかチェック。1時間で期限切れ。"""
    info = session.get('admin')
    if not info:
        return False
    if info.get('group_uuid') != str(group_uuid):
        return False
    if info.get('expires_at', 0) < time.time():
        session.pop('admin', None)
        return False
    return True


# ====================================================================
# 5. かんたんな「レート制限」(いたずら防止)
# ====================================================================
# 短い時間にアクセスしすぎたらブロックする仕組み
_rate_buckets = {}


def rate_limit(key: str, max_per_minute: int = 60) -> bool:
    """制限を超えてなかったらTrueを返す"""
    minute = int(time.time() // 60)
    k = f'{key}:{minute}'
    _rate_buckets[k] = _rate_buckets.get(k, 0) + 1
    # たまった古い記録を掃除
    if len(_rate_buckets) > 5000:
        for old_key in list(_rate_buckets.keys()):
            old_min = int(old_key.split(':')[-1])
            if old_min < minute - 1:
                _rate_buckets.pop(old_key, None)
    return _rate_buckets[k] <= max_per_minute


def client_ip() -> str:
    # プロキシ(中継サーバー)の先のIPを取る
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'unknown'


# ====================================================================
# 6. 文字列を比べる関数(答え合わせで使う)
# 大文字/小文字・空白・句読点のちがいは許してあげる
# ====================================================================
# 漢字→ひらがな変換（pykakasiを一度だけ初期化して使い回す）
try:
    import pykakasi as _pykakasi
    _kakasi = _pykakasi.kakasi()
except Exception:
    _kakasi = None  # インストールされていない場合はスキップ

def normalize_answer(s: str) -> str:
    import unicodedata
    s = (s or '').strip()
    # NFKC正規化（全角数字・英字→半角、ローマ字統一など）
    s = unicodedata.normalize('NFKC', s)
    # 小文字に統一（英語の大文字小文字を区別しない）
    s = s.lower()
    # 空白を全部消す
    for ws in [' ', '\u3000', '\t', '\n', '\u00a0']:
        s = s.replace(ws, '')
    # 句読点・記号を消す
    for p in ['、','。',',','.','!','?','！','？','・','〜','～','…',
              '「','」','『','』','（','）','(',')',
              '【','】','〈','〉','《','》','／','\\','-','ー','―']:
        s = s.replace(p, '')
    # カタカナ→ひらがなに統一（ウ→う など）
    result = []
    for c in s:
        if '\u30a1' <= c <= '\u30f6':  # カタカナ範囲
            result.append(chr(ord(c) - 0x60))  # ひらがなに変換
        else:
            result.append(c)
    s = ''.join(result)
    # 漢字→ひらがなに変換（pykakasi使用）
    # # 「海」→「うみ」のように変換することで漢字・ひらがな両方正解にできる
    if _kakasi:
        try:
            converted = _kakasi.convert(s)
            s = ''.join(
                item['hira'] if item['hira'] else item['orig']
                for item in converted
            )
        except Exception:
            pass  # 変換失敗時はそのまま
    return s


def check_answer(user_answer, quiz_row):
    # quiz_rowの答えは復号済みのものを期待する
    # 複数正解に対応した答え合わせ
    # answersカラムがあればそちらを優先、なければanswerを使う
    answers_json = quiz_row.get('answers')
    if answers_json:
        try:
            answers = json.loads(answers_json)
        except Exception:
            answers = [quiz_row['answer']]
    else:
        answers = [quiz_row['answer']]
    ua = normalize_answer(user_answer)
    return any(normalize_answer(a) == ua for a in answers)

# ====================================================================
# 7. JSONで結果を返すヘルパー
# ====================================================================
def ok(**data):
    # 「成功したよ」のレスポンス
    return jsonify({'ok': True, **data})


def err(message: str, status: int = 400):
    # 「失敗したよ」のレスポンス
    return jsonify({'ok': False, 'error': message}), status


# ====================================================================
# 8. HTMLページのルーティング(どのURLで何を表示するか)
# ====================================================================

@app.route('/')
def page_home():
    # トップページ。ログイン済みならグループへ、まだならエントリー画面へ。
    if current_group():
        return redirect(url_for('page_group'))
    return render_template('entry.html')


@app.route('/create')
def page_create():
    # 新しいグループを作るページ
    return render_template('create_group.html')


@app.route('/group')
def page_group():
    # グループに入ったあとのメイン画面(クイズ一覧)
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('group.html', group=grp, group_id=session.get('group_id'))


@app.route('/answer/<quiz_id>')
def page_answer(quiz_id):
    # クイズに答えるページ
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    # クイズが本当にそのグループのものかチェック
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT id, author_name, class_name, question, answer_options, has_options, tags, created_at
            FROM quizzes WHERE id = %s AND group_id = %s
        '''), (quiz_id, grp['id']))
        quiz = cur.fetchone()
    if not quiz:
        return redirect(url_for('page_group'))
    quiz = dict(quiz)
    # JSONっぽく解釈(SQLiteはJSONをTEXTで保存してる)
    opts = quiz.get('answer_options')
    if isinstance(opts, str) and opts:
        try:
            quiz['answer_options'] = json.loads(opts)
        except Exception:
            quiz['answer_options'] = None
    quiz['tags'] = [t for t in (quiz.get('tags') or '').split(',') if t]
    return render_template('answer.html', quiz=quiz, group=grp, group_id=session.get('group_id'))


@app.route('/<group_id>/setting/')
def page_admin_entry(group_id):
    # 管理者画面の入口(URLを直接打ってアクセス)
    # まず、そのグループが本当にあるかをチェック
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT id, name, view_only
            FROM groups WHERE group_id_hash = %s
        '''), (hash_group_id(group_id),))
        row = cur.fetchone()
    group_info = dict(row) if row else None
    return render_template(
        'admin.html',
        group_id=group_id,
        group_info=group_info,
        logged_in=bool(group_info) and admin_logged_in_for(group_info['id']),
    )


@app.route('/terms')
def page_terms():
    # 利用規約ページ
    return render_template('terms.html')


@app.route('/privacy')
def page_privacy():
    # プライバシーポリシーページ
    return render_template('privacy.html')


@app.route('/help')
def page_help():
    # ヘルプページ
    return render_template('help.html')


# ====================================================================
# 9. API(JavaScriptから呼ばれる、画面をリロードせずに動かすやつ)
# ====================================================================

@app.route('/api/session', methods=['GET'])
def api_session():
    # いまの自分の状態を返す
    grp = current_group()
    if not grp:
        return ok(logged_in=False)
    return ok(
        logged_in=True,
        group={
            'id': str(grp['id']),
            'name': grp['name'],
            'color': grp['color'],
            'view_only': bool(grp['view_only']),
            'has_admin': bool(grp.get('admin_password_hash')),
        },
        group_id=session.get('group_id'),
    )


@app.route('/api/groups', methods=['POST'])
def api_create_group():
    # 新しいグループを作る
    if not rate_limit(f'create:{client_ip()}', 10):
        return err('リクエストが多すぎるよ。少し待ってね。', 429)

    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    name = (data.get('name') or '').strip()
    color = data.get('color') or '#E85A8A'
    admin_password = data.get('admin_password') or ''
    agreed = bool(data.get('agreed_terms'))
    not_illegal = bool(data.get('confirm_not_illegal'))

    # 利用規約の同意チェック(同意してない人は作らせない)
    if not agreed:
        return err('利用規約への同意が必要です', 400)
    if not not_illegal:
        return err('法律や他人の権利を守ることへの確認が必要です', 400)

    # 入力のチェック
    import re
    if not re.match(r'^[a-zA-Z0-9_-]{6,32}$', group_id):
        return err('グループIDは半角英数字・ハイフン・アンダースコアで6〜32文字にしてね')
    if not name or len(name) > 50:
        return err('グループ名は1〜50文字で入力してね')
    if not re.match(r'^#[0-9A-Fa-f]{6}$', color):
        return err('色の形式が正しくないよ')

    pw_hash = None
    if admin_password:
        if len(admin_password) < 6 or len(admin_password) > 100:
            return err('管理者パスワードは6〜100文字で入力してね')
        pw_hash = hash_password(admin_password)

    gid_hash = hash_group_id(group_id)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (gid_hash,))
        if cur.fetchone():
            return err('このグループIDはもう使われているよ。違うIDにしてね。', 409)

        if USE_POSTGRES:
            gp_hash = None
            group_password = data.get('group_password') or ''
            if group_password:
                if len(group_password) < 4:
                    pass
                else:
                    gp_hash = hash_password(group_password)
            cur.execute(q('''
                INSERT INTO groups (group_id_hash, name, color, admin_password_hash, group_password_hash)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            '''), (gid_hash, name, color, pw_hash, gp_hash))
            new_group_id = cur.fetchone()['id']
        else:
            gp_hash = None
            group_password = data.get('group_password') or ''
            if group_password and len(group_password) >= 4:
                gp_hash = hash_password(group_password)
            new_group_id = new_id()
            cur.execute(q('''
                INSERT INTO groups (id, group_id_hash, name, color, admin_password_hash, group_password_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
            '''), (new_group_id, gid_hash, name, color, pw_hash, gp_hash))

    # ログイン状態にする
    session.clear()
    session['group_id'] = group_id
    return ok(redirect='/group')


@app.route('/api/login', methods=['POST'])
def api_login():
    if not rate_limit(f'login:{client_ip()}', 20):
        return err('ログインが多すぎるよ。少し待ってね。', 429)
    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    group_password = data.get('group_password') or ''
    if not group_id:
        return err('グループIDを入力してね')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, group_password_hash FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループIDが正しくないよ', 403)
    row = dict(row)
    stored_gp = row.get('group_password_hash')
    if stored_gp:
        if not group_password:
            return err('パスワードが必要だよ', 401)
        if not verify_password(group_password, stored_gp):
            return err('パスワードが違うよ', 401)
    session.clear()
    session['group_id'] = group_id
    return ok(redirect='/group')


@app.route('/api/groups/check', methods=['POST'])
def api_check_group():
    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    if not group_id:
        return err('グループIDを入力してね')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, group_password_hash FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループIDが正しくないよ', 403)
    row = dict(row)
    return ok(has_password=bool(row.get('group_password_hash')))


@app.route('/api/logout', methods=['POST'])
def api_logout():
    # ログアウトする(セッションを消す)
    session.clear()
    return ok(redirect='/')


@app.route('/api/group', methods=['PATCH'])
def api_update_group():
    # グループ名や色を変える
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    data = request.get_json(silent=True) or {}
    updates = []
    values = []

    name = data.get('name')
    color = data.get('color')

    if name is not None:
        name = name.strip()
        if not name or len(name) > 50:
            return err('グループ名は1〜50文字で入力してね')
        updates.append('name = %s')
        values.append(name)

    if color is not None:
        import re
        if not re.match(r'^#[0-9A-Fa-f]{6}$', color):
            return err('色の形式が正しくないよ')
        updates.append('color = %s')
        values.append(color)

    if not updates:
        return ok()

    values.append(grp['id'])
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q(f'UPDATE groups SET {", ".join(updates)} WHERE id = %s'), values)

    return ok()


@app.route('/api/quizzes', methods=['GET'])
def api_list_quizzes():
    # クイズの一覧を、統計ふきんきにしてかえす
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT q.id, q.author_name AS name, q.class_name, q.question,
                   q.answer_options, q.has_options, q.tags, q.created_at,
                   CASE WHEN q.edit_password_hash IS NOT NULL THEN 1 ELSE 0 END AS has_edit_pw,
                   (SELECT COUNT(*) FROM attempts WHERE quiz_id = q.id) AS attempts,
                   (SELECT COUNT(*) FROM attempts WHERE quiz_id = q.id AND correct = 1) AS corrects,
                   (SELECT COALESCE(AVG(difficulty), 0) FROM feedbacks WHERE quiz_id = q.id) AS avg_difficulty,
                   (SELECT COUNT(*) FROM feedbacks WHERE quiz_id = q.id) AS feedback_count
            FROM quizzes q
            WHERE q.group_id = %s
            ORDER BY q.created_at DESC
        '''), (grp['id'],))
        rows = cur.fetchall()

    quizzes = []
    for r in rows:
        row = dict(r)
        # 選択肢を配列に戻す
        opts = row.get('answer_options')
        if isinstance(opts, str) and opts:
            try:
                row['answer_options'] = json.loads(opts)
            except Exception:
                row['answer_options'] = None
        row['tags'] = [t for t in (row.get('tags') or '').split(',') if t]
        row['id'] = str(row['id'])
        row['attempts'] = int(row.get('attempts') or 0)
        row['corrects'] = int(row.get('corrects') or 0)
        row['avg_difficulty'] = float(row.get('avg_difficulty') or 0)
        row['feedback_count'] = int(row.get('feedback_count') or 0)
        row['has_options'] = bool(row.get('has_options'))
        row['has_edit_pw'] = bool(row.get('has_edit_pw'))
        row['created_at'] = str(row['created_at'])
        quizzes.append(row)

    return ok(quizzes=quizzes, view_only=bool(grp['view_only']))


@app.route('/api/quizzes', methods=['POST'])
def api_create_quiz():
    # 新しいクイズを追加する
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    class_name = (data.get('class_name') or '').strip()
    question = (data.get('question') or '').strip()
    answer = (data.get('answer') or '').strip()
    options = data.get('answer_options')
    tags = data.get('tags') or []

    # 違法行為がないかの最終確認
    if not data.get('confirm_not_illegal'):
        return err('法律・他人の権利を守ったクイズであることの確認が必要だよ', 400)

    if not name or len(name) > 30:
        return err('名前は1〜30文字で入力してね')
    if not question or len(question) > 500:
        return err('問題は1〜500文字で入力してね')
    if not answer or len(answer) > 500:
        return err('答えは1〜500文字で入力してね')

    has_options = False
    options_json = None
    if isinstance(options, list) and len(options) >= 2:
        if len(options) > 6:
            return err('選択肢は2〜6個までだよ')
        cleaned = [str(o).strip() for o in options]
        cleaned = [o for o in cleaned if 0 < len(o) <= 100]
        if len(cleaned) < 2:
            return err('選択肢は2つ以上の有効な項目が必要だよ')
        if not any(normalize_answer(o) == normalize_answer(answer) for o in cleaned):
            return err('正解が選択肢の中に入ってないよ')
        has_options = True
        options_json = json.dumps(cleaned, ensure_ascii=False)

    # タグをきれいにする
    if not isinstance(tags, list):
        tags = []
    clean_tags = []
    for t in tags:
        t = str(t).strip()
        if 0 < len(t) <= 20 and t not in clean_tags:
            clean_tags.append(t)
    clean_tags = clean_tags[:10]
    tags_str = ','.join(clean_tags)

    # 複数正解の処理
    data_answers = data.get('answers') or []
    if isinstance(data_answers, list) and len(data_answers) > 0:
        clean_answers = [str(a).strip() for a in data_answers if str(a).strip()]
        if answer not in clean_answers:
            clean_answers.insert(0, answer)
        answers_json_str = json.dumps(clean_answers, ensure_ascii=False)
    else:
        answers_json_str = json.dumps([answer], ensure_ascii=False)

    # 編集パスワード
    edit_pw = data.get('edit_password') or ''
    edit_pw_hash = hash_password(edit_pw) if edit_pw else None
    explanation = (data.get('explanation') or '').strip()[:1000]
    hint = (data.get('hint') or '').strip()[:300]

    with get_db() as conn:
        cur = make_cursor(conn)
        if USE_POSTGRES:
            cur.execute(q('''
                INSERT INTO quizzes (group_id, author_name, class_name, question, answer,
                                     answer_options, has_options, tags, answers, edit_password_hash, explanation, hint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at
            '''), (grp['id'], name, class_name, question, answer,
                   options_json, 1 if has_options else 0, tags_str,
                   answers_json_str, edit_pw_hash, explanation, hint))
            row = cur.fetchone()
            new_quiz_id = str(row['id'])
            created_at = str(row['created_at'])
        else:
            new_quiz_id = new_id()
            cur.execute(q('''
                INSERT INTO quizzes (id, group_id, author_name, class_name, question, answer,
                                     answer_options, has_options, tags, answers, edit_password_hash, explanation, hint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''), (new_quiz_id, grp['id'], name, class_name, question, answer,
                   options_json, 1 if has_options else 0, tags_str,
                   answers_json_str, edit_pw_hash, explanation, hint))
            created_at = datetime.now(timezone.utc).isoformat()

    return ok(quiz={
        'id': new_quiz_id,
        'name': name,
        'class_name': class_name,
        'question': question,
        'answer_options': json.loads(options_json) if options_json else None,
        'has_options': has_options,
        'has_edit_pw': bool(edit_pw_hash),
        'tags': clean_tags,
        'created_at': created_at,
        'attempts': 0, 'corrects': 0,
        'avg_difficulty': 0, 'feedback_count': 0,
    })


@app.route('/api/quizzes/<quiz_id>', methods=['DELETE'])
def api_delete_quiz(quiz_id):
    # クイズを消す
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    with get_db() as conn:
        cur = make_cursor(conn)
        # 他のグループのクイズは消せないようにする
        cur.execute(q('DELETE FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if cur.rowcount == 0:
            return err('そのクイズは見つからないよ', 404)
        # クイズを消したら、それに関する挑戦記録と感想もきれいに消す
        cur.execute(q('DELETE FROM attempts WHERE quiz_id = %s'), (quiz_id,))
        cur.execute(q('DELETE FROM feedbacks WHERE quiz_id = %s'), (quiz_id,))
    return ok()


@app.route('/api/quizzes/<quiz_id>/answer', methods=['POST'])
def api_answer_quiz(quiz_id):
    # クイズの答え合わせをする(サーバーで判定するから、答えがブラウザに漏れない)
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    data = request.get_json(silent=True) or {}
    user_answer = str(data.get('user_answer', ''))[:500]
    time_ms = int(data.get('time_ms') or 0)
    # タイムは0〜2時間まで
    time_ms = max(0, min(7200000, time_ms))

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT answer, answers, explanation, hint FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
        if not row:
            return err('クイズが見つからないよ', 404)
        row_dict = dict(row)
        correct_answer = dec(row_dict.get('answer') or '')
        answers_raw = row_dict.get('answers')
        if answers_raw:
            try:
                ans_list = json.loads(answers_raw)
                row_dict['answers'] = json.dumps([dec(a) for a in ans_list], ensure_ascii=False)
            except Exception:
                pass
        row_dict['answer'] = correct_answer
        is_correct = check_answer(user_answer, row_dict)

        if USE_POSTGRES:
            cur.execute(q('INSERT INTO attempts (quiz_id, correct, time_ms) VALUES (%s, %s, %s)'),
                        (quiz_id, 1 if is_correct else 0, time_ms))
        else:
            cur.execute(q('INSERT INTO attempts (id, quiz_id, correct, time_ms) VALUES (%s, %s, %s, %s)'),
                        (new_id(), quiz_id, 1 if is_correct else 0, time_ms))

    return ok(correct=is_correct, correct_answer=correct_answer, time_ms=time_ms, explanation=dec(row_dict.get('explanation') or ''), hint=dec(row_dict.get('hint') or ''))


@app.route('/api/quizzes/<quiz_id>/feedback', methods=['POST'])
def api_feedback(quiz_id):
    # 感想と難易度を送る
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    data = request.get_json(silent=True) or {}
    try:
        difficulty = int(data.get('difficulty'))
    except Exception:
        return err('難易度は1〜5の整数で指定してね')
    if difficulty < 1 or difficulty > 5:
        return err('難易度は1〜5で指定してね')

    comment = str(data.get('comment') or '').strip()
    if len(comment) > 500:
        return err('感想は500文字以内で入力してね')

    if not data.get('confirm_not_illegal'):
        return err('感想が他人をきずつける内容でないことの確認が必要だよ', 400)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)

        if USE_POSTGRES:
            cur.execute(q('INSERT INTO feedbacks (quiz_id, difficulty, comment) VALUES (%s, %s, %s)'),
                        (quiz_id, difficulty, comment))
        else:
            cur.execute(q('INSERT INTO feedbacks (id, quiz_id, difficulty, comment) VALUES (%s, %s, %s, %s)'),
                        (new_id(), quiz_id, difficulty, comment))

    return ok()


@app.route('/api/quizzes/<quiz_id>/stats', methods=['GET'])
def api_stats(quiz_id):
    # 統計情報(挑戦数、正解率、難易度など)を返す
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)

        cur.execute(q('''
            SELECT
              (SELECT COUNT(*)               FROM attempts WHERE quiz_id = %s) AS attempts,
              (SELECT COUNT(*)               FROM attempts WHERE quiz_id = %s AND correct = 1) AS corrects,
              (SELECT COALESCE(AVG(time_ms),0) FROM attempts WHERE quiz_id = %s) AS avg_time_ms,
              (SELECT COALESCE(MIN(time_ms),0) FROM attempts WHERE quiz_id = %s AND correct = 1) AS fastest_ms,
              (SELECT COALESCE(AVG(difficulty),0) FROM feedbacks WHERE quiz_id = %s) AS avg_difficulty,
              (SELECT COUNT(*)               FROM feedbacks WHERE quiz_id = %s) AS feedback_count
        '''), (quiz_id, quiz_id, quiz_id, quiz_id, quiz_id, quiz_id))
        s = dict(cur.fetchone())

        cur.execute(q('''
            SELECT difficulty, comment, created_at FROM feedbacks
            WHERE quiz_id = %s AND comment <> ''
            ORDER BY created_at DESC LIMIT 10
        '''), (quiz_id,))
        recent = [dict(r) for r in cur.fetchall()]
        for r in recent:
            r['created_at'] = str(r['created_at'])

    return ok(
        stats={
            'attempts': int(s['attempts']),
            'corrects': int(s['corrects']),
            'avg_time_ms': int(s['avg_time_ms']),
            'fastest_ms': int(s['fastest_ms']),
            'avg_difficulty': float(s['avg_difficulty']),
            'feedback_count': int(s['feedback_count']),
        },
        recent_feedbacks=recent,
    )


# ====================================================================
# 10. 管理者(あなた)用API
# ====================================================================

@app.route('/api/admin/login/<group_id>', methods=['POST'])
def api_admin_login(group_id):
    # 管理者のパスワードチェック
    if not rate_limit(f'adminlogin:{client_ip()}', 10):
        return err('ログイン試行が多すぎるよ。少し待ってね。', 429)

    data = request.get_json(silent=True) or {}
    password = data.get('password') or ''
    if not password:
        return err('パスワードを入力してね')

    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_password:
        return err('管理者パスワードが設定されていないよ', 403)
    if not hmac.compare_digest(password, admin_password):
        return err('パスワードが違うよ', 401)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(group_id),))
        row = cur.fetchone()

    if not row:
        return err('グループが見つからないよ', 404)

    session['admin'] = {
        'group_uuid': str(row['id']),
        'expires_at': time.time() + 3600,
    }
    return ok()


@app.route('/api/admin/logout/<group_id>', methods=['POST'])
def api_admin_logout(group_id):
    # 管理者としてのログアウト(通常のグループログインはそのまま)
    session.pop('admin', None)
    return ok()


@app.route('/api/admin/view-only/<group_id>', methods=['POST'])
def api_admin_view_only(group_id):
    # 閲覧のみモードのON/OFFを切り替える
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループが見つからないよ', 404)
    if not admin_logged_in_for(row['id']):
        return err('管理者としてログインしてね', 401)

    data = request.get_json(silent=True) or {}
    view_only = 1 if data.get('view_only') else 0

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('UPDATE groups SET view_only = %s WHERE id = %s'), (view_only, row['id']))

    return ok(view_only=bool(view_only))


@app.route('/api/admin/delete-group/<group_id>', methods=['POST'])
def api_admin_delete_group(group_id):
    # グループを完全に削除する
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループが見つからないよ', 404)
    group_uuid = row['id']
    if not admin_logged_in_for(group_uuid):
        return err('管理者としてログインしてね', 401)

    data = request.get_json(silent=True) or {}
    if (data.get('confirm_group_id') or '').strip() != group_id:
        return err('確認用のグループIDが一致しないよ', 400)

    with get_db() as conn:
        cur = make_cursor(conn)
        # 関係ある情報を全部消す(クイズ→挑戦・感想は外部キーで連鎖削除)
        cur.execute(q('DELETE FROM attempts WHERE quiz_id IN (SELECT id FROM quizzes WHERE group_id = %s)'), (group_uuid,))
        cur.execute(q('DELETE FROM feedbacks WHERE quiz_id IN (SELECT id FROM quizzes WHERE group_id = %s)'), (group_uuid,))
        cur.execute(q('DELETE FROM quizzes WHERE group_id = %s'), (group_uuid,))
        cur.execute(q('DELETE FROM groups WHERE id = %s'), (group_uuid,))

    # ログイン情報もクリア
    session.clear()
    return ok()



@app.route("/api/admin/quizzes/<group_id>", methods=["GET"])
def api_admin_quizzes(group_id):
    # 管理者がクイズ一覧を取得する
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("SELECT id FROM groups WHERE group_id_hash = %s"), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err("グループが見つからないよ", 404)
    if not admin_logged_in_for(row["id"]):
        return err("管理者としてログインしてね", 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("SELECT id, author_name, question, created_at FROM quizzes WHERE group_id = %s ORDER BY created_at DESC"), (row["id"],))
        quizzes = [dict(r) for r in cur.fetchall()]
    for q2 in quizzes:
        q2["id"]          = str(q2["id"])
        q2["created_at"]  = str(q2["created_at"])
        # # 暗号化されたフィールドを復号して返す
        q2["author_name"] = dec(q2.get("author_name") or "")
        q2["question"]    = dec(q2.get("question") or "")
    return ok(quizzes=quizzes)

@app.route("/api/admin/quizzes/<group_id>/<quiz_id>", methods=["DELETE"])
def api_admin_delete_quiz(group_id, quiz_id):
    # 管理者がクイズを削除する
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("SELECT id FROM groups WHERE group_id_hash = %s"), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err("グループが見つからないよ", 404)
    if not admin_logged_in_for(row["id"]):
        return err("管理者としてログインしてね", 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("DELETE FROM attempts WHERE quiz_id = %s"), (quiz_id,))
        cur.execute(q("DELETE FROM feedbacks WHERE quiz_id = %s"), (quiz_id,))
        cur.execute(q("DELETE FROM quizzes WHERE id = %s AND group_id = %s"), (quiz_id, row["id"]))
    return ok()

@app.route('/api/quizzes/<quiz_id>/ranking', methods=['POST'])
def api_register_ranking(quiz_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    display_name = (data.get('display_name') or '').strip()
    time_ms = int(data.get('time_ms') or 0)
    if not display_name or len(display_name) > 20:
        return err('表示名は1〜20文字で入力してね')
    if time_ms <= 0:
        return err('タイムが正しくないよ')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)
        if USE_POSTGRES:
            cur.execute(q('INSERT INTO rankings (quiz_id, display_name, time_ms) VALUES (%s, %s, %s)'),
                        (quiz_id, display_name, time_ms))
        else:
            cur.execute(q('INSERT INTO rankings (id, quiz_id, display_name, time_ms) VALUES (%s, %s, %s, %s)'),
                        (new_id(), quiz_id, display_name, time_ms))
    return ok()


@app.route('/api/group/rankings', methods=['GET'])
def api_group_rankings():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT q.id, q.question, q.author_name,
                   (SELECT COUNT(*) FROM rankings WHERE quiz_id = q.id) AS ranking_count
            FROM quizzes q WHERE q.group_id = %s
            ORDER BY q.created_at DESC
        '''), (grp['id'],))
        quizzes = [dict(r) for r in cur.fetchall()]
    for q2 in quizzes:
        q2['id'] = str(q2['id'])
        q2['ranking_count'] = int(q2.get('ranking_count') or 0)
    return ok(quizzes=quizzes)


@app.route('/api/quizzes/<quiz_id>/rankings', methods=['GET'])
def api_quiz_rankings(quiz_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)
        cur.execute(q('''
            SELECT display_name, time_ms, created_at
            FROM rankings WHERE quiz_id = %s
            ORDER BY time_ms ASC LIMIT 20
        '''), (quiz_id,))
        rankings = [dict(r) for r in cur.fetchall()]
    for r in rankings:
        r['created_at'] = str(r['created_at'])
    return ok(rankings=rankings)


@app.route('/ranking')
def page_ranking():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('ranking.html', group=grp, group_id=session.get('group_id'))


@app.route('/api/admin/enter/<group_id>', methods=['POST'])
def api_admin_enter(group_id):
    # 管理者がグループに入る(パスワードなしで入れる)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループが見つからないよ', 404)
    if not admin_logged_in_for(row['id']):
        return err('管理者としてログインしてね', 401)
    session['group_id'] = group_id
    return ok(redirect='/group')


@app.route('/api/quizzes/<quiz_id>', methods=['GET'])
def api_get_quiz(quiz_id):
    # クイズ1件の詳細を取得(編集用)
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''SELECT id, author_name, class_name, question, answer,
                          answer_options, has_options, tags, answers,
                          CASE WHEN edit_password_hash IS NOT NULL THEN 1 ELSE 0 END AS has_edit_pw
                   FROM quizzes WHERE id = %s AND group_id = %s'''),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    quiz = dict(row)
    opts = quiz.get('answer_options')
    if isinstance(opts, str) and opts:
        try:
            quiz['answer_options'] = json.loads(opts)
        except Exception:
            quiz['answer_options'] = None
    quiz['tags'] = [t for t in (quiz.get('tags') or '').split(',') if t]
    ans = quiz.get('answers')
    if isinstance(ans, str) and ans:
        try:
            quiz['answers'] = json.loads(ans)
        except Exception:
            quiz['answers'] = [quiz['answer']]
    else:
        quiz['answers'] = [quiz['answer']]
    quiz['id'] = str(quiz['id'])
    quiz['has_options'] = bool(quiz.get('has_options'))
    quiz['has_edit_pw'] = bool(quiz.get('has_edit_pw'))
    return ok(quiz=quiz)


@app.route('/api/quizzes/<quiz_id>/edit', methods=['POST'])
def api_edit_quiz(quiz_id):
    # クイズ編集: パスワードが設定されてないクイズは編集できない
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    data = request.get_json(silent=True) or {}

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT edit_password_hash FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    row = dict(row)
    stored = row.get('edit_password_hash')
    if not stored:
        return err('このクイズは編集できないよ', 403)

    pw = data.get('edit_password') or ''
    if not verify_password(pw, stored):
        return err('編集パスワードが違うよ', 401)

    question = (data.get('question') or '').strip()
    answer = (data.get('answer') or '').strip()
    if not question or len(question) > 500:
        return err('問題は1〜500文字で入力してね')
    if not answer:
        return err('答えを入力してね')

    data_answers = data.get('answers') or []
    if isinstance(data_answers, list) and data_answers:
        clean_answers = [str(a).strip() for a in data_answers if str(a).strip()]
        answers_json_str = json.dumps(clean_answers, ensure_ascii=False)
    else:
        answers_json_str = json.dumps([answer], ensure_ascii=False)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('UPDATE quizzes SET question=%s, answer=%s, answers=%s WHERE id=%s AND group_id=%s'),
                    (question, answer, answers_json_str, quiz_id, grp['id']))
    return ok()


@app.route('/api/quizzes/<quiz_id>/upload', methods=['POST'])
def api_upload_image(quiz_id):
    # 画像をアップロードする(3枚まで)
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)
        cur.execute(q('SELECT COUNT(*) as cnt FROM quiz_images WHERE quiz_id = %s'), (quiz_id,))
        cnt = dict(cur.fetchone())['cnt']
        if int(cnt) >= 3:
            return err('画像は3枚までだよ', 400)

    if 'file' not in request.files:
        return err('ファイルがないよ', 400)
    f = request.files['file']
    if not f.filename:
        return err('ファイル名がないよ', 400)
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in {'png', 'jpg', 'jpeg', 'gif', 'webp'}:
        return err('png/jpg/gif/webpのみアップロードできるよ', 400)
    if len(f.read()) > 5 * 1024 * 1024:
        return err('ファイルは5MB以下にしてね', 400)
    f.seek(0)

    upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = new_id() + '.' + ext
    f.save(os.path.join(upload_dir, filename))

    with get_db() as conn:
        cur = make_cursor(conn)
        if USE_POSTGRES:
            cur.execute(q('INSERT INTO quiz_images (quiz_id, filename) VALUES (%s, %s)'),
                        (quiz_id, filename))
        else:
            cur.execute(q('INSERT INTO quiz_images (id, quiz_id, filename) VALUES (%s, %s, %s)'),
                        (new_id(), quiz_id, filename))
    return ok(filename=filename, url='/static/uploads/' + filename)


@app.route('/api/quizzes/<quiz_id>/images', methods=['GET'])
def api_get_images(quiz_id):
    # クイズの画像一覧を返す
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT filename FROM quiz_images WHERE quiz_id = %s ORDER BY created_at'),
                    (quiz_id,))
        images = [{'filename': r['filename'] if hasattr(r, '__getitem__') else r[0],
                   'url': '/static/uploads/' + (r['filename'] if hasattr(r, '__getitem__') else r[0])}
                  for r in cur.fetchall()]
    return ok(images=images)


@app.route('/api/quizzes/random', methods=['GET'])
def api_random_quiz():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import random
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM quizzes WHERE group_id = %s'), (grp['id'],))
        ids = [str(dict(r)['id']) for r in cur.fetchall()]
    if not ids:
        return err('クイズがないよ', 404)
    return ok(quiz_id=random.choice(ids))


@app.route('/api/sets', methods=['GET'])
def api_list_sets():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, name, quiz_ids, created_at FROM quiz_sets WHERE group_id = %s ORDER BY created_at DESC'), (grp['id'],))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r['id'] = str(r['id'])
        r['quiz_ids'] = (r.get('quiz_ids') or '').split(',') if r.get('quiz_ids') else []
        r['count'] = len(r['quiz_ids'])
        r['created_at'] = str(r['created_at'])
    return ok(sets=rows)


@app.route('/api/sets', methods=['POST'])
def api_create_set():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('閲覧モードでは作れないよ', 403)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    quiz_ids = data.get('quiz_ids') or []
    if not name or len(name) > 50:
        return err('セット名を1〜50文字で入力してね')
    if not isinstance(quiz_ids, list) or len(quiz_ids) < 1:
        return err('クイズを1つ以上選んでね')
    quiz_ids_str = ','.join([str(x) for x in quiz_ids])
    with get_db() as conn:
        cur = make_cursor(conn)
        if USE_POSTGRES:
            cur.execute(q('INSERT INTO quiz_sets (group_id, name, quiz_ids) VALUES (%s, %s, %s)'),
                        (grp['id'], name, quiz_ids_str))
        else:
            cur.execute(q('INSERT INTO quiz_sets (id, group_id, name, quiz_ids) VALUES (%s, %s, %s, %s)'),
                        (new_id(), grp['id'], name, quiz_ids_str))
    return ok()


@app.route('/api/sets/<set_id>', methods=['DELETE'])
def api_delete_set(set_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('DELETE FROM quiz_sets WHERE id = %s AND group_id = %s'), (set_id, grp['id']))
    return ok()


@app.route('/sets')
def page_sets():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('sets.html', group=grp, group_id=session.get('group_id'))


@app.route('/api/quizzes/<quiz_id>/hint', methods=['GET'])
def api_get_hint(quiz_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT hint FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    return ok(hint=dec(dict(row).get('hint') or ''))


@app.route('/api/group/stats', methods=['GET'])
def api_group_stats():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT COUNT(*) AS c FROM quizzes WHERE group_id = %s'), (grp['id'],))
        total_quizzes = int(dict(cur.fetchone())['c'])
        cur.execute(q('''SELECT COUNT(*) AS c FROM attempts WHERE quiz_id IN
                        (SELECT id FROM quizzes WHERE group_id = %s)'''), (grp['id'],))
        total_attempts = int(dict(cur.fetchone())['c'])
        cur.execute(q('''SELECT COUNT(*) AS c FROM attempts WHERE correct = 1 AND quiz_id IN
                        (SELECT id FROM quizzes WHERE group_id = %s)'''), (grp['id'],))
        total_corrects = int(dict(cur.fetchone())['c'])
        cur.execute(q('''SELECT q.id, q.question, q.author_name AS name,
                        (SELECT COUNT(*) FROM attempts WHERE quiz_id = q.id) AS attempts
                        FROM quizzes q WHERE q.group_id = %s
                        ORDER BY attempts DESC LIMIT 5'''), (grp['id'],))
        popular = [dict(r) for r in cur.fetchall()]
    for p in popular:
        p['id'] = str(p['id'])
        p['attempts'] = int(p['attempts'])
    return ok(total_quizzes=total_quizzes, total_attempts=total_attempts,
              total_corrects=total_corrects, popular=popular)


@app.route('/groupstats')
def page_group_stats():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('group_stats.html', group=grp, group_id=session.get('group_id'))

# ====================================================================
# 11. ヘルスチェック(Railwayが「サーバー動いてる?」って確認するため)
# ====================================================================
@app.route('/api/health')
def api_health():
    return ok()


# ====================================================================
# 12. エラーハンドラ
# ====================================================================
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return err('見つからないよ', 404)
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return err('サーバーエラーが起きたよ', 500)
    return render_template('500.html'), 500


# ====================================================================
# 13. テンプレートで使える便利な関数(Jinjaで{{ }} の中で使える)
# ====================================================================
@app.context_processor
def inject_globals():
    # 全HTMLで session と current_group が使えるようにする
    grp = current_group()
    return {
        'current_group': grp,
        'current_group_id': session.get('group_id'),
    }


# ====================================================================
# 14. サーバー起動!
# ====================================================================
# Flaskのデフォルト開発サーバーの起動(本番は gunicorn が呼び出す)
init_db()


# ===== フィードバック機能 =====

@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    # フィードバックを送信するページ
    import datetime, pytz, sqlite3 as _sq
    msg = None
    if request.method == "POST":
        star     = request.form.get("star_rating", "3")
        category = request.form.get("category", "感想")
        message  = request.form.get("message", "").strip()
        # メッセージが空なら送信しない
        if not message:
            msg = "error"
        else:
            # 日本時間で今の時刻を取得
            jst = pytz.timezone("Asia/Tokyo")
            now = datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")
            # DBに直接つないで保存する
            db_path = os.environ.get("SQLITE_PATH", "/home/yuto113/quizshare.db")
            conn = _sq.connect(db_path)
            conn.execute(
                "INSERT INTO feedback (created_at, star_rating, category, message) VALUES (?, ?, ?, ?)",
                (now, int(star), category, message)
            )
            conn.commit()
            conn.close()
            msg = "ok"
    return render_template("feedback.html", msg=msg)

@app.route("/feedback/list")
def feedback_list():
    # 管理者だけが見られるフィードバック一覧ページ
    import sqlite3 as _sq
    admin_pw  = request.args.get("pw", "")
    correct_pw = os.environ.get("ADMIN_PASSWORD", "")
    if admin_pw != correct_pw:
        # パスワードが違ったら403エラー
        return "管理者パスワードが違います", 403
    # DBに直接つないで一覧を取得する
    db_path = os.environ.get("SQLITE_PATH", "/home/yuto113/quizshare.db")
    conn = _sq.connect(db_path)
    rows = conn.execute(
        "SELECT id, created_at, star_rating, category, message FROM feedback ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("feedback_list.html", feedbacks=rows)


# ===== 引用ライブラリ =====

@app.route('/library')
def page_library():
    # 引用ライブラリページ（ログイン不要で閲覧可）
    grp = current_group()
    return render_template('library.html', logged_in=(grp is not None))

@app.route('/api/library/data')
def api_library_data():
    # 学年・教科・問題一覧を返すAPI
    import sqlite3 as _sq
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    conn = _sq.connect(db_path)
    grade   = request.args.get('grade', '')
    subject = request.args.get('subject', '')

    if grade and subject:
        # 問題一覧を返す
        rows = conn.execute(
            "SELECT id, question, answer, explanation FROM library_quizzes WHERE grade=? AND subject=? ORDER BY id",
            (grade, subject)
        ).fetchall()
        quizzes = [{'id': r[0], 'question': r[1], 'answer': r[2], 'explanation': r[3] or ''} for r in rows]
        conn.close()
        return ok(quizzes=quizzes)

    # 学年一覧
    grades = [r[0] for r in conn.execute(
        "SELECT DISTINCT grade FROM library_quizzes ORDER BY grade"
    ).fetchall()]

    # 教科一覧（学年指定時）
    subjects = []
    if grade:
        subjects = [r[0] for r in conn.execute(
            "SELECT DISTINCT subject FROM library_quizzes WHERE grade=? ORDER BY subject",
            (grade,)
        ).fetchall()]

    conn.close()
    return ok(grades=grades, subjects=subjects)

@app.route('/api/library/import', methods=['POST'])
def api_library_import():
    # ライブラリの問題をグループに引用するAPI（平文で保存→decのフォールバックで表示OK）
    import sqlite3 as _sq, datetime, pytz
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    data   = request.get_json(silent=True) or {}
    lib_id = data.get('quiz_id')
    if not lib_id:
        return err('quiz_idが必要です')

    # ライブラリから問題を取得
    _db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    lib_conn = _sq.connect(_db_path)
    row = lib_conn.execute(
        "SELECT grade, subject, question, answer, explanation FROM library_quizzes WHERE id=?",
        (lib_id,)
    ).fetchone()
    lib_conn.close()

    if not row:
        return err('問題が見つかりません')

    grade, subject, question, answer, explanation = row
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')

    # グループのクイズとして追加
    # # ライブラリの問題は教育公開データなので平文で保存する（decのフォールバックで正常表示される）
    with get_db() as conn:
        cur = make_cursor(conn)
        quiz_id     = new_id()
        group_db_id = grp.get('id', '')
        tag_str     = f'{grade} {subject}'
        try:
            cur.execute(
                q('INSERT INTO quizzes (id,group_id,author_name,class_name,question,answer,explanation,tags,has_options,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
                (quiz_id, group_db_id, '引用ライブラリ', '',
                 question, answer, explanation or '', tag_str, 0, now)
            )
            conn.commit()
        except Exception as e:
            return err(f'追加失敗: {str(e)}')

    return ok(message='引用しました')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
