# ====================================================================
# qz_common.py: アプリ全体で使う共通部品
# (暗号化・パスワード・DB接続・セッション・レート制限・採点・ok/err)
# app.pyから引っ越してきた。中身は1文字も変えていない。
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

from flask import request, jsonify, session, g

# 暗号化(enc/dec)
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
            time_default = 'TEXT NOT NULL DEFAULT (datetime(\'now\',\'localtime\'))'
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
    import unicodedata, re
    s = (s or '').strip()
    s = unicodedata.normalize('NFKC', s)
    s = s.lower()
    # 空白除去
    s = s.replace(' ', '').replace('\t', '').replace('\n', '').replace('\xa0', '')
    # 句読点除去
    for p in ['、','。',',','.','!','?','！','？','・','～','〜','…',
              '「','」','『','』','（','）','(',')',
              '【','】','〈','〉','《','》','／','\\','-','ー','―',
              '=','＝',':','：',';','；','~','#','@','*','_','→','←']:
        s = s.replace(p, '')
    # 接頭辞除去
    s = re.sub(r'^(約|第|全|総|計|合計)', '', s)
    # 語尾正規化
    s = re.sub(r'(こと|もの|ため|ので|わけ|はず)$', '', s)
    s = re.sub(r'(など|等|以上|以下|以内|前後|程度|くらい|ほど)$', '', s)
    s = re.sub(r'(します|しました|しています|いたします)$', 'する', s)
    s = re.sub(r'(です|でした|であります|でございます)$', 'だ', s)
    s = re.sub(r'(ます|ました|ません)$', '', s)
    s = re.sub(r'(ください|なさい)$', '', s)
    s = re.sub(r'(である|であった|だった)$', 'だ', s)
    s = re.sub(r'(といわれる|とよばれる|とされる|とされている|といわれている)$', '', s)
    s = re.sub(r'(という|ともいう|ともよばれる)$', '', s)
    s = re.sub(r'(すること|されること)$', 'する', s)
    # 漢数字→算用数字
    for k, v in [('十','10'),('百','100'),('千','1000'),('万','10000'),
                 ('零','0'),('〇','0'),('一','1'),('二','2'),('三','3'),
                 ('四','4'),('五','5'),('六','6'),('七','7'),('八','8'),('九','9')]:
        s = s.replace(k, v)
    # カタカナ→ひらがな
    result = []
    for c in s:
        if '\u30a1' <= c <= '\u30f6':
            result.append(chr(ord(c) - 0x60))
        else:
            result.append(c)
    s = ''.join(result)
    # 漢字→ひらがな（pykakasi）
    if _kakasi:
        try:
            converted = _kakasi.convert(s)
            s = ''.join(
                item['hira'] if item['hira'] else item['orig']
                for item in converted
            )
        except Exception:
            pass
    return s

# 類義語辞書
SYNONYMS = {
    "ひとつ":["1","いち"],
    "ふたつ":["2","に"],
    "みっつ":["3","さん"],
    "うえ":["上","かみ"],
    "した":["下","しも"],
    "むかし":["昔","以前"],
    "いま":["現在","今"],
    "たいよう":["日","お日様","サン","太陽"],
    "つき":["月","お月様"],
    "ほし":["星"],
    "やま":["山"],
    "かわ":["川","河"],
    "うみ":["海"],
    "そら":["空"],
    "いぬ":["犬","わんこ"],
    "ねこ":["猫","にゃんこ"],
    "さかな":["魚"],
    "とり":["鳥"],
    "あか":["赤","レッド","紅"],
    "あお":["青","ブルー","藍"],
    "きいろ":["黄色","イエロー","黄"],
    "みどり":["緑","グリーン"],
    "しろ":["白","ホワイト"],
    "くろ":["黒","ブラック"],
    "ごはん":["米","飯","めし"],
    "みず":["水"],
    "にほん":["日本","japan","nippon"],
    "とうきょう":["東京","tokyo"],
    "おおさか":["大阪","osaka"],
    "かんすう":["関数","function"],
    "へんすう":["変数","variable"],
    "はいれつ":["配列","array"],
    "ちきゅう":["地球","アース"],
    "おんど":["温度","気温"],
    "こたえ":["答え","回答","解答"],
    "がっこう":["学校"],
    "せんせい":["先生","教師","教員"],
}

def edit_distance(s1, s2):
    if len(s1) > 20 or len(s2) > 20:
        return abs(len(s1) - len(s2))
    m, n = len(s1), len(s2)
    dp = [[0]*(n+1) for _ in range(m+1)]
    for i in range(m+1): dp[i][0] = i
    for j in range(n+1): dp[0][j] = j
    for i in range(1, m+1):
        for j in range(1, n+1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])
    return dp[m][n]

def is_synonym(a, b):
    if a == b:
        return True
    for key, syns in SYNONYMS.items():
        group = [key] + [normalize_answer(s) for s in syns]
        if a in group and b in group:
            return True
    return False

def smart_check(ua, correct):
    if ua == correct:
        return True, 'exact'
    if is_synonym(ua, correct):
        return True, 'synonym'
    threshold = 1 if len(correct) <= 4 else 2
    if len(correct) >= 2 and edit_distance(ua, correct) <= threshold:
        return True, 'typo'
    if len(correct) >= 4 and correct in ua:
        return True, 'partial'
    if len(ua) >= 4 and ua in correct and len(ua) >= len(correct) * 0.7:
        return True, 'partial'
    return False, 'wrong'

def check_answer(user_answer, quiz_row):
    answers_json = quiz_row.get('answers')
    if answers_json:
        try:
            answers = json.loads(answers_json)
        except Exception:
            answers = [quiz_row['answer']]
    else:
        answers = [quiz_row['answer']]
    ua = normalize_answer(user_answer)
    has_options = bool(quiz_row.get('has_options'))
    for a in answers:
        na = normalize_answer(a)
        if has_options:
            # 選択問題は完全一致のみ
            if ua == na:
                return True
        else:
            ok_flag, reason = smart_check(ua, na)
            if ok_flag:
                return True
    return False

# ====================================================================
# 7. JSONで結果を返すヘルパー
# ====================================================================
def ok(**data):
    # 「成功したよ」のレスポンス
    return jsonify({'ok': True, **data})


def err(message: str, status: int = 400):
    # 「失敗したよ」のレスポンス
    return jsonify({'ok': False, 'error': message}), status


