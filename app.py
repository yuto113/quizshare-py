# -*- coding: utf-8 -*-
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
        cur.execute('CREATE INDEX IF NOT EXISTS idx_feedbacks_quiz ON feedbacks(quiz_id)')

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
def normalize_answer(s: str) -> str:
    s = (s or '').strip().lower()
    # 空白を全部消す
    for ws in [' ', '\u3000', '\t', '\n']:
        s = s.replace(ws, '')
    # 句読点を消す
    for p in ['、', '。', ',', '.', '!', '?', '!', '?']:
        s = s.replace(p, '')
    return s


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
            SELECT id, name, view_only,
                   CASE WHEN admin_password_hash IS NULL THEN 0 ELSE 1 END AS has_admin
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
            cur.execute(q('''
                INSERT INTO groups (group_id_hash, name, color, admin_password_hash)
                VALUES (%s, %s, %s, %s) RETURNING id
            '''), (gid_hash, name, color, pw_hash))
            new_group_id = cur.fetchone()['id']
        else:
            new_group_id = new_id()
            cur.execute(q('''
                INSERT INTO groups (id, group_id_hash, name, color, admin_password_hash)
                VALUES (%s, %s, %s, %s, %s)
            '''), (new_group_id, gid_hash, name, color, pw_hash))

    # ログイン状態にする
    session.clear()
    session['group_id'] = group_id
    return ok(redirect='/group')


@app.route('/api/login', methods=['POST'])
def api_login():
    # グループにログインする
    if not rate_limit(f'login:{client_ip()}', 20):
        return err('ログインが多すぎるよ。少し待ってね。', 429)

    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    if not group_id:
        return err('グループIDを入力してね')

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()

    if not row:
        return err('グループIDが正しくないよ', 403)

    session.clear()
    session['group_id'] = group_id
    return ok(redirect='/group')


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

    with get_db() as conn:
        cur = make_cursor(conn)
        if USE_POSTGRES:
            cur.execute(q('''
                INSERT INTO quizzes (group_id, author_name, class_name, question, answer,
                                     answer_options, has_options, tags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at
            '''), (grp['id'], name, class_name, question, answer,
                   options_json, 1 if has_options else 0, tags_str))
            row = cur.fetchone()
            new_quiz_id = str(row['id'])
            created_at = str(row['created_at'])
        else:
            new_quiz_id = new_id()
            cur.execute(q('''
                INSERT INTO quizzes (id, group_id, author_name, class_name, question, answer,
                                     answer_options, has_options, tags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''), (new_quiz_id, grp['id'], name, class_name, question, answer,
                   options_json, 1 if has_options else 0, tags_str))
            created_at = datetime.now(timezone.utc).isoformat()

    return ok(quiz={
        'id': new_quiz_id,
        'name': name,
        'class_name': class_name,
        'question': question,
        'answer_options': json.loads(options_json) if options_json else None,
        'has_options': has_options,
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
        cur.execute(q('SELECT answer FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
        if not row:
            return err('クイズが見つからないよ', 404)
        correct_answer = row['answer']
        is_correct = normalize_answer(user_answer) == normalize_answer(correct_answer)

        if USE_POSTGRES:
            cur.execute(q('INSERT INTO attempts (quiz_id, correct, time_ms) VALUES (%s, %s, %s)'),
                        (quiz_id, 1 if is_correct else 0, time_ms))
        else:
            cur.execute(q('INSERT INTO attempts (id, quiz_id, correct, time_ms) VALUES (%s, %s, %s, %s)'),
                        (new_id(), quiz_id, 1 if is_correct else 0, time_ms))

    return ok(correct=is_correct, correct_answer=correct_answer, time_ms=time_ms)


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

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, admin_password_hash FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(group_id),))
        row = cur.fetchone()

    if not row:
        return err('グループが見つからないよ', 404)
    stored = row['admin_password_hash']
    if not stored:
        return err('このグループには管理者パスワードが設定されていないよ', 403)
    if not verify_password(password, stored):
        return err('パスワードが違うよ', 401)

    # 管理者ログイン情報をセッションに保存(1時間で自動的に切れる)
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
