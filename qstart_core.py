# -*- coding: utf-8 -*-
"""
Qstart Core — 設定 / プロジェクト / プライバシー / 管理者
QZERO by Qzero会社
"""
import sqlite3, os, time, json, uuid, hashlib
from functools import wraps
from flask import Blueprint, request, jsonify, session, render_template

DB_PATH = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
qstart_core = Blueprint('qstart_core', __name__, template_folder='templates')

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')

def nid():
    return uuid.uuid4().hex[:16]


# ========== DB初期化 ==========
def init_qstart_core_db():
    conn = db(); c = conn.cursor()

    # --- 設定(GDPR対応: 履歴保存と学習利用を分離) ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_settings (
        user_id TEXT PRIMARY KEY,
        lang TEXT DEFAULT 'ja',
        theme TEXT DEFAULT 'light',
        timezone TEXT DEFAULT 'Asia/Tokyo',
        font_size TEXT DEFAULT 'md',
        default_model TEXT DEFAULT 'equi',
        default_effort TEXT DEFAULT 'mid',
        enable_upload INTEGER DEFAULT 1,
        enable_search INTEGER DEFAULT 1,
        show_thinking INTEGER DEFAULT 1,
        streaming INTEGER DEFAULT 1,
        save_history INTEGER DEFAULT 1,
        allow_training INTEGER DEFAULT 0,
        reduce_motion INTEGER DEFAULT 0,
        high_contrast INTEGER DEFAULT 0,
        notify_email INTEGER DEFAULT 1,
        notify_news INTEGER DEFAULT 1,
        updated_at TEXT
    )''')

    # --- プロジェクト ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_projects (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        name TEXT NOT NULL, description TEXT DEFAULT '',
        instructions TEXT DEFAULT '',
        icon TEXT DEFAULT '📁', color TEXT DEFAULT '#b8860b',
        archived INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_qsproj_user ON qstart_projects(user_id)')

    # --- プロジェクト内のファイル(ナレッジ) ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_project_files (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT,
        file_name TEXT, content TEXT, size INTEGER DEFAULT 0, created_at TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_qspf_proj ON qstart_project_files(project_id)')

    # --- チャット↔プロジェクト ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_chats (
        chat_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        project_id TEXT, title TEXT DEFAULT '',
        model TEXT, created_at TEXT, updated_at TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_qschat_user ON qstart_chats(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_qschat_proj ON qstart_chats(project_id)')

    # --- 全体お知らせ ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, body TEXT,
        level TEXT DEFAULT 'info',
        lang TEXT DEFAULT 'ja',
        target TEXT DEFAULT 'all',
        active INTEGER DEFAULT 1,
        starts_at TEXT, ends_at TEXT,
        created_by TEXT, created_at TEXT
    )''')

    # --- お知らせ既読 ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_announce_reads (
        ann_id INTEGER, user_id TEXT, read_at TEXT,
        PRIMARY KEY (ann_id, user_id)
    )''')

    # --- モデル制御(ON/OFF・メンテ) ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_model_flags (
        model TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1,
        maintenance INTEGER DEFAULT 0, note TEXT DEFAULT '',
        min_role TEXT DEFAULT 'user', updated_at TEXT
    )''')
    for m, en in [('pure',0), ('equi',1), ('zin',1), ('apex',0)]:
        c.execute('INSERT OR IGNORE INTO qstart_model_flags(model,enabled,updated_at) VALUES(?,?,?)', (m, en, now()))

    # --- ユーザー状態(凍結・権限・上限個別設定) ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_user_flags (
        user_id TEXT PRIMARY KEY,
        role TEXT DEFAULT 'user',
        status TEXT DEFAULT 'active',
        window_tokens INTEGER,
        monthly_tokens INTEGER,
        note TEXT DEFAULT '',
        updated_by TEXT, updated_at TEXT
    )''')

    # --- 管理操作ログ(監査。世界展開では必須) ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_admin_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id TEXT, action TEXT, target TEXT,
        detail TEXT, ip_hash TEXT, created_at TEXT
    )''')

    # --- 通報・フィードバック ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT, chat_id TEXT, category TEXT,
        body TEXT, status TEXT DEFAULT 'open',
        handled_by TEXT, created_at TEXT
    )''')

    # --- 機能フラグ(段階リリース用) ---
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_feature_flags (
        key TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0,
        rollout_pct INTEGER DEFAULT 0, note TEXT DEFAULT '', updated_at TEXT
    )''')
    for k in ['projects', 'voice', 'artifacts', 'register_open']:
        c.execute('INSERT OR IGNORE INTO qstart_feature_flags(key,enabled,updated_at) VALUES(?,0,?)', (k, now()))

    conn.commit(); conn.close()

init_qstart_core_db()


# ========== 認証ヘルパー ==========
def cur_uid():
    """今ログインしているQstartユーザーID"""
    return session.get('qstart_user') or session.get('qstart_user_id')

def require_login(f):
    @wraps(f)
    def w(*a, **kw):
        if not cur_uid():
            return jsonify({'ok': False, 'error': 'login_required'}), 401
        return f(*a, **kw)
    return w

# Qstart管理者として許可する社員ID(初期はyutoのみ)
QSTART_ADMIN_STAFF = ['yuto']

def qstart_role(uid=None):
    """Qstart独自の権限。
    ・許可リストの社員IDは admin
    ・qstart_user_flags.role でも指定可(世界展開後はこちらで管理)
    ※社員adminでも許可リストに無ければ管理者にはならない
    """
    uid = uid or cur_uid()

    # ① Qstartアカウントの権限(DBで管理。世界展開後はこちらが主)
    if uid:
        conn = db()
        r = conn.execute('SELECT role,status FROM qstart_user_flags WHERE user_id=?', (uid,)).fetchone()
        conn.close()
        if r:
            st = (r['status'] or 'active')
            if st != 'active':
                return 'user'          # 凍結中は権限を失う
            if r['role'] in ('admin', 'moderator'):
                return r['role']

    # ② 社員IDの許可リスト(管理者ページ用)
    sid = session.get('staff_id')
    if sid and sid in QSTART_ADMIN_STAFF:
        return 'admin'
    # Qstartに社員IDでログインしている場合も許可
    if uid and uid in QSTART_ADMIN_STAFF and session.get('qstart_staff'):
        return 'admin'

    return 'user' if uid else 'guest'

def require_admin(f):
    @wraps(f)
    def w(*a, **kw):
        if qstart_role() != 'admin':
            return jsonify({'ok': False, 'error': 'forbidden'}), 403
        return f(*a, **kw)
    return w

def alog(action, target='', detail=''):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    ih = hashlib.sha256(ip.encode()).hexdigest()[:16]
    conn = db()
    conn.execute('INSERT INTO qstart_admin_log(admin_id,action,target,detail,ip_hash,created_at) VALUES(?,?,?,?,?,?)',
                 (cur_uid() or session.get('staff_id',''), action, target, detail, ih, now()))
    conn.commit(); conn.close()


# ========== 設定 API ==========
SETTING_KEYS = ['lang','theme','timezone','font_size','default_model','default_effort',
                'enable_upload','enable_search','show_thinking','streaming',
                'save_history','allow_training','reduce_motion','high_contrast',
                'notify_email','notify_news']
INT_KEYS = {'enable_upload','enable_search','show_thinking','streaming','save_history',
            'allow_training','reduce_motion','high_contrast','notify_email','notify_news'}

@qstart_core.route('/qstart/api/v1/settings', methods=['GET'])
def get_settings():
    uid = cur_uid()
    if not uid:
        return jsonify({'ok': True, 'guest': True, 'settings': {}})
    conn = db()
    r = conn.execute('SELECT * FROM qstart_settings WHERE user_id=?', (uid,)).fetchone()
    if not r:
        conn.execute('INSERT INTO qstart_settings(user_id,updated_at) VALUES(?,?)', (uid, now()))
        conn.commit()
        r = conn.execute('SELECT * FROM qstart_settings WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return jsonify({'ok': True, 'settings': {k: r[k] for k in SETTING_KEYS}, 'role': qstart_role(uid)})

@qstart_core.route('/qstart/api/v1/settings', methods=['POST'])
@require_login
def save_settings():
    uid = cur_uid()
    data = request.get_json(silent=True) or {}
    sets, vals = [], []
    for k in SETTING_KEYS:
        if k in data:
            v = data[k]
            if k in INT_KEYS:
                v = 1 if v in (1, '1', True, 'true') else 0
            sets.append(f'{k}=?'); vals.append(v)
    if not sets:
        return jsonify({'ok': False, 'error': 'no_fields'}), 400
    conn = db()
    conn.execute('INSERT OR IGNORE INTO qstart_settings(user_id,updated_at) VALUES(?,?)', (uid, now()))
    vals += [now(), uid]
    conn.execute(f'UPDATE qstart_settings SET {",".join(sets)}, updated_at=? WHERE user_id=?', vals)
    conn.commit(); conn.close()
    return jsonify({'ok': True})


# ========== プロジェクト API ==========
@qstart_core.route('/qstart/api/v1/projects', methods=['GET'])
@require_login
def list_projects():
    uid = cur_uid()
    conn = db()
    rows = conn.execute('''SELECT p.*, (SELECT COUNT(*) FROM qstart_chats c WHERE c.project_id=p.id) chats
                           FROM qstart_projects p WHERE p.user_id=? AND p.archived=0
                           ORDER BY p.updated_at DESC''', (uid,)).fetchall()
    conn.close()
    return jsonify({'ok': True, 'projects': [dict(r) for r in rows]})

@qstart_core.route('/qstart/api/v1/projects', methods=['POST'])
@require_login
def create_project():
    uid = cur_uid()
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name_required'}), 400
    if len(name) > 80:
        return jsonify({'ok': False, 'error': 'name_too_long'}), 400
    conn = db()
    n = conn.execute('SELECT COUNT(*) FROM qstart_projects WHERE user_id=? AND archived=0', (uid,)).fetchone()[0]
    if n >= 50:
        conn.close(); return jsonify({'ok': False, 'error': 'limit_reached'}), 400
    pid = nid()
    conn.execute('''INSERT INTO qstart_projects(id,user_id,name,description,instructions,icon,color,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                 (pid, uid, name, (d.get('description') or '')[:1000],
                  (d.get('instructions') or '')[:8000],
                  d.get('icon') or '📁', d.get('color') or '#b8860b', now(), now()))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'id': pid})

@qstart_core.route('/qstart/api/v1/projects/<pid>', methods=['GET'])
@require_login
def get_project(pid):
    uid = cur_uid()
    conn = db()
    p = conn.execute('SELECT * FROM qstart_projects WHERE id=? AND user_id=?', (pid, uid)).fetchone()
    if not p:
        conn.close(); return jsonify({'ok': False, 'error': 'not_found'}), 404
    chats = conn.execute('SELECT chat_id,title,updated_at FROM qstart_chats WHERE project_id=? ORDER BY updated_at DESC', (pid,)).fetchall()
    files = conn.execute('SELECT id,file_name,size,created_at FROM qstart_project_files WHERE project_id=?', (pid,)).fetchall()
    conn.close()
    return jsonify({'ok': True, 'project': dict(p),
                    'chats': [dict(r) for r in chats], 'files': [dict(r) for r in files]})

@qstart_core.route('/qstart/api/v1/projects/<pid>', methods=['PATCH'])
@require_login
def update_project(pid):
    uid = cur_uid()
    d = request.get_json(silent=True) or {}
    allowed = ['name','description','instructions','icon','color','archived']
    sets, vals = [], []
    for k in allowed:
        if k in d:
            sets.append(f'{k}=?'); vals.append(d[k])
    if not sets:
        return jsonify({'ok': False, 'error': 'no_fields'}), 400
    vals += [now(), pid, uid]
    conn = db()
    cur = conn.execute(f'UPDATE qstart_projects SET {",".join(sets)}, updated_at=? WHERE id=? AND user_id=?', vals)
    conn.commit(); n = cur.rowcount; conn.close()
    return jsonify({'ok': n > 0})

@qstart_core.route('/qstart/api/v1/projects/<pid>', methods=['DELETE'])
@require_login
def delete_project(pid):
    uid = cur_uid()
    conn = db()
    conn.execute('UPDATE qstart_chats SET project_id=NULL WHERE project_id=? AND user_id=?', (pid, uid))
    conn.execute('DELETE FROM qstart_project_files WHERE project_id=? AND user_id=?', (pid, uid))
    cur = conn.execute('DELETE FROM qstart_projects WHERE id=? AND user_id=?', (pid, uid))
    conn.commit(); n = cur.rowcount; conn.close()
    return jsonify({'ok': n > 0})

@qstart_core.route('/qstart/api/v1/projects/<pid>/chats', methods=['POST'])
@require_login
def attach_chat(pid):
    uid = cur_uid()
    d = request.get_json(silent=True) or {}
    cid = (d.get('chat_id') or '').strip()
    if not cid:
        return jsonify({'ok': False, 'error': 'chat_id_required'}), 400
    conn = db()
    conn.execute('''INSERT INTO qstart_chats(chat_id,user_id,project_id,title,created_at,updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET project_id=excluded.project_id, updated_at=excluded.updated_at''',
                 (cid, uid, pid, (d.get('title') or '')[:200], now(), now()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


# ========== プライバシー(GDPR) ==========
@qstart_core.route('/qstart/api/v1/privacy/export', methods=['POST'])
@require_login
def privacy_export():
    uid = cur_uid()
    conn = db()
    out = {'exported_at': now(), 'user_id': uid}
    for t, col in [('qstart_users','user_id'), ('qstart_settings','user_id'),
                   ('qstart_projects','user_id'), ('qstart_chats','user_id'),
                   ('qstart_api_usage','user_id'), ('qstart_api_keys','user_id')]:
        try:
            rows = conn.execute(f'SELECT * FROM {t} WHERE {col}=?', (uid,)).fetchall()
            out[t] = [{k: r[k] for k in r.keys() if k != 'password_hash'} for r in rows]
        except Exception:
            out[t] = []
    conn.close()
    return jsonify({'ok': True, 'data': out})

@qstart_core.route('/qstart/api/v1/privacy/delete', methods=['POST'])
@require_login
def privacy_delete():
    uid = cur_uid()
    d = request.get_json(silent=True) or {}
    if d.get('confirm') != 'DELETE':
        return jsonify({'ok': False, 'error': 'confirm_required'}), 400
    scope = d.get('scope', 'history')
    conn = db()
    conn.execute('DELETE FROM qstart_chats WHERE user_id=?', (uid,))
    if scope == 'all':
        for t in ['qstart_projects','qstart_project_files','qstart_settings',
                  'qstart_api_keys','qstart_user_flags']:
            try: conn.execute(f'DELETE FROM {t} WHERE user_id=?', (uid,))
            except Exception: pass
        conn.execute('DELETE FROM qstart_users WHERE user_id=?', (uid,))
    conn.commit(); conn.close()
    if scope == 'all':
        session.pop('qstart_user', None)
    return jsonify({'ok': True, 'scope': scope})


# ========== お知らせ(ユーザー側) ==========
@qstart_core.route('/qstart/api/v1/announcements')
def get_announcements():
    """ログイン中: 未読のみバナー対象 / ゲスト: 最新1件のみ"""
    uid = cur_uid()
    conn = db()
    rows = conn.execute("""SELECT * FROM qstart_announcements
        WHERE active=1
          AND (starts_at IS NULL OR starts_at<=?)
          AND (ends_at IS NULL OR ends_at>=?)
        ORDER BY id DESC LIMIT 30""", (now(), now())).fetchall()

    read = set()
    if uid:
        read = {r[0] for r in conn.execute(
            'SELECT ann_id FROM qstart_announce_reads WHERE user_id=?', (uid,))}
    conn.close()

    items = []
    for r in rows:
        d = dict(r)
        d['unread'] = (d['id'] not in read)
        items.append(d)

    if uid:
        # ログイン中: 未読を全部バナー対象に
        banner = [x for x in items if x['unread']]
    else:
        # ゲスト: 最新1件だけ
        banner = items[:1]

    return jsonify({'ok': True, 'logged_in': bool(uid),
                    'announcements': items, 'banner': banner})


@qstart_core.route('/qstart/api/v1/announcements/<int:aid>/read', methods=['POST'])
@require_login
def announce_read(aid):
    conn = db()
    conn.execute('INSERT OR IGNORE INTO qstart_announce_reads(ann_id,user_id,read_at) VALUES(?,?,?)',
                 (aid, cur_uid(), now()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


# ========== 管理者 API ==========
# ========== 凍結・権限チェック ==========
def user_status(uid):
    if not uid: return 'active'
    conn = db()
    r = conn.execute('SELECT status FROM qstart_user_flags WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return (r['status'] if r else 'active') or 'active'

def is_suspended(uid):
    return user_status(uid) in ('suspended', 'banned')

def require_moderator(f):
    @wraps(f)
    def w(*a, **kw):
        if qstart_role() not in ('admin', 'moderator'):
            return jsonify({'ok': False, 'error': 'forbidden'}), 403
        return f(*a, **kw)
    return w

@qstart_core.route('/qstart/api/v1/status')
def my_status():
    uid = cur_uid()
    st = user_status(uid)
    return jsonify({'ok': True, 'status': st, 'suspended': st != 'active'})


# ========== 自分の情報 ==========
@qstart_core.route('/qstart/api/v1/me')
def whoami():
    uid = cur_uid()
    if not uid:
        return jsonify({'ok': True, 'guest': True, 'role': 'guest'})
    conn = db()
    u = conn.execute('SELECT user_id,nickname,email,lang,created_at FROM qstart_users WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    is_staff = bool(session.get('qstart_staff'))
    nick = session.get('qstart_nick') or (u['nickname'] if u else uid)
    email = (u['email'] if u else '') or ''
    if email and '@' in email:
        nm, dm = email.split('@', 1)
        email = (nm[:2] + '***' if len(nm) > 2 else '***') + '@' + dm
    return jsonify({'ok': True, 'guest': False, 'user_id': uid,
                    'nickname': nick, 'email': email,
                    'plan': 'スタッフ' if is_staff else 'Free',
                    'role': qstart_role(uid),
                    'status': user_status(uid),
                    'is_staff': is_staff,
                    'created_at': (u['created_at'] if u else None)})


# ========== モデル一覧(誰でも読める) ==========
@qstart_core.route('/qstart/api/v1/models')
def public_models():
    conn = db()
    rows = conn.execute('SELECT model,enabled,maintenance,note,min_role FROM qstart_model_flags').fetchall()
    conn.close()
    return jsonify({'ok': True, 'models': [dict(r) for r in rows]})


# ========== 機能フラグの判定 ==========
def feature_for(key, uid=None):
    """段階公開。rollout_pct%のユーザーにだけ有効"""
    conn = db()
    r = conn.execute('SELECT enabled,rollout_pct FROM qstart_feature_flags WHERE key=?', (key,)).fetchone()
    conn.close()
    if not r or not r['enabled']:
        return False
    pct = r['rollout_pct'] if r['rollout_pct'] is not None else 100
    if pct >= 100: return True
    if pct <= 0: return False
    uid = uid or cur_uid() or 'guest'
    h = int(hashlib.md5((key + ':' + uid).encode()).hexdigest()[:8], 16)
    return (h % 100) < pct

@qstart_core.route('/qstart/api/v1/features')
def my_features():
    conn = db()
    keys = [r['key'] for r in conn.execute('SELECT key FROM qstart_feature_flags')]
    conn.close()
    return jsonify({'ok': True, 'features': {k: feature_for(k) for k in keys}})


# ========== 通報 ==========
@qstart_core.route('/qstart/api/v1/report', methods=['POST'])
def create_report():
    uid = cur_uid() or 'guest'
    d = request.get_json(silent=True) or {}
    body = (d.get('body') or '')[:2000]
    if not body.strip():
        return jsonify({'ok': False, 'error': 'body_required'}), 400
    conn = db()
    n24 = conn.execute("""SELECT COUNT(*) FROM qstart_reports
        WHERE user_id=? AND created_at > datetime('now','-1 day','localtime')""", (uid,)).fetchone()[0]
    if n24 >= 20:
        conn.close()
        return jsonify({'ok': False, 'error': 'too_many'}), 429
    conn.execute("""INSERT INTO qstart_reports(user_id,chat_id,category,body,status,created_at)
                    VALUES(?,?,?,?,'open',?)""",
                 (uid, (d.get('chat_id') or '')[:64],
                  (d.get('category') or 'other')[:40], body, now()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@qstart_core.route('/qstart/api/v1/admin/reports/<int:rid>', methods=['PATCH'])
@require_moderator
def admin_report_update(rid):
    d = request.get_json(silent=True) or {}
    st = d.get('status', 'closed')
    conn = db()
    conn.execute('UPDATE qstart_reports SET status=?, handled_by=? WHERE id=?',
                 (st, cur_uid() or session.get('staff_id',''), rid))
    conn.commit(); conn.close()
    alog('report_update', str(rid), st)
    return jsonify({'ok': True})


# ========== メール・登録の管理 ==========
@qstart_core.route('/qstart/api/v1/admin/mail', methods=['GET','POST'])
@require_admin
def admin_mail():
    import qstart_mail as qm
    conn = db()
    if request.method == 'GET':
        logs = conn.execute("""SELECT id,kind,status,provider,error,created_at
            FROM qstart_mail_log ORDER BY id DESC LIMIT 100""").fetchall()
        daily = conn.execute("""SELECT date(created_at) d, COUNT(*) n,
            SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) sent
            FROM qstart_mail_log WHERE created_at > datetime('now','-14 day','localtime')
            GROUP BY d ORDER BY d DESC""").fetchall()
        signups = conn.execute("""SELECT date, count FROM qstart_signup_quota
            ORDER BY date DESC LIMIT 14""").fetchall()
        f = conn.execute("SELECT enabled FROM qstart_feature_flags WHERE key='signup_open'").fetchone()
        conn.close()
        return jsonify({'ok': True,
            'signup_open': bool(f and f['enabled']),
            'sent_today': qm.mail_sent_today(),
            'signups_today': qm.signups_today(),
            'mail_limit': qm.DAILY_MAIL_LIMIT,
            'signup_limit': qm.DAILY_SIGNUP_LIMIT,
            'has_key': bool(os.environ.get('RESEND_API_KEY')),
            'logs': [dict(r) for r in logs],
            'daily': [dict(r) for r in daily],
            'signups': [dict(r) for r in signups]})

    d = request.get_json(silent=True) or {}
    if 'signup_open' in d:
        conn.execute("""INSERT INTO qstart_feature_flags(key,enabled,updated_at)
            VALUES('signup_open',?,?) ON CONFLICT(key) DO UPDATE SET
            enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (1 if d['signup_open'] else 0, now()))
        conn.commit()
        alog('signup_toggle', '', str(d['signup_open']))
    conn.close()
    return jsonify({'ok': True})


@qstart_core.route('/qstart/api/v1/admin/mail/test', methods=['POST'])
@require_admin
def admin_mail_test():
    import qstart_mail as qm
    d = request.get_json(silent=True) or {}
    to = (d.get('to') or '').strip()
    if not to or '@' not in to:
        return jsonify({'ok': False, 'error': 'bad_email'}), 400
    ok, res = qm.send_verify_code(to, '000000', d.get('lang', 'ja'))
    alog('mail_test', to.split('@')[-1], str(res))
    return jsonify({'ok': ok, 'result': str(res)})


# ========== 登録設定(公開) ==========
@qstart_core.route('/qstart/api/v1/signup-config')
def signup_config_public():
    import qstart_signup as qs
    return jsonify(dict({'ok': True}, **qs.public_config()))


# ========== 登録管理(管理者) ==========
@qstart_core.route('/qstart/api/v1/admin/signup', methods=['GET','POST'])
@require_admin
def admin_signup():
    import qstart_signup as qs, qstart_mail as qm
    if request.method == 'GET':
        conn = db()
        uses = conn.execute("""SELECT u.code, u.user_id, u.used_at, i.note
            FROM qstart_invite_uses u LEFT JOIN qstart_invites i ON i.code=u.code
            ORDER BY u.id DESC LIMIT 100""").fetchall()
        conn.close()
        return jsonify({'ok': True,
            'config': qs.get_config(),
            'invites': qs.list_invites(),
            'uses': [dict(r) for r in uses],
            'signups_today': qm.signups_today(),
            'has_turnstile': bool(os.environ.get('TURNSTILE_SECRET_KEY')),
            'has_resend': bool(os.environ.get('RESEND_API_KEY'))})
    d = request.get_json(silent=True) or {}
    kw = {}
    for k in ['mode','require_turnstile','require_email','daily_limit']:
        if k in d:
            kw[k] = int(d[k]) if k != 'mode' else d[k]
    if kw:
        qs.set_config(**kw)
        alog('signup_config', '', json.dumps(kw, ensure_ascii=False))
    return jsonify({'ok': True})


@qstart_core.route('/qstart/api/v1/admin/invites', methods=['POST'])
@require_admin
def admin_invite_create():
    import qstart_signup as qs
    d = request.get_json(silent=True) or {}
    n_make = max(1, min(int(d.get('count', 1)), 50))
    made = []
    for _ in range(n_make):
        c = qs.new_invite(note=(d.get('note') or '')[:200],
                          max_uses=int(d.get('max_uses', 1)),
                          expires_at=d.get('expires_at') or None,
                          by=cur_uid() or session.get('staff_id','admin'),
                          code=(d.get('code') or '').strip().upper() or None,
                          min_age=int(d.get('min_age', 13)),
                          grant_role=d.get('grant_role') or None,
                          bonus_window=int(d.get('bonus_window', 0) or 0),
                          bonus_monthly=int(d.get('bonus_monthly', 0) or 0),
                          bonus_stock=int(d.get('bonus_stock', 0) or 0))
        if c: made.append(c)
    alog('invite_create', ','.join(made[:5]), f'{len(made)}件')
    return jsonify({'ok': bool(made), 'codes': made})


@qstart_core.route('/qstart/api/v1/admin/invites/<code>', methods=['PATCH','DELETE'])
@require_admin
def admin_invite_edit(code):
    conn = db()
    if request.method == 'DELETE':
        conn.execute('DELETE FROM qstart_invites WHERE code=?', (code,))
        act = 'invite_delete'
    else:
        d = request.get_json(silent=True) or {}
        conn.execute('UPDATE qstart_invites SET active=? WHERE code=?',
                     (1 if d.get('active') else 0, code))
        act = 'invite_toggle'
    conn.commit(); conn.close()
    alog(act, code)
    return jsonify({'ok': True})


@qstart_core.route('/qstart/api/v1/admin/stats')
@require_admin
def admin_stats():
    conn = db(); s = {}
    s['users_total'] = conn.execute('SELECT COUNT(*) FROM qstart_users').fetchone()[0]
    s['users_today'] = conn.execute("SELECT COUNT(*) FROM qstart_users WHERE date(created_at)=date('now')").fetchone()[0]
    s['projects'] = conn.execute('SELECT COUNT(*) FROM qstart_projects').fetchone()[0]
    s['chats'] = conn.execute('SELECT COUNT(*) FROM qstart_chats').fetchone()[0]
    s['tokens_24h'] = conn.execute("SELECT COALESCE(SUM(tokens_used),0) FROM qstart_api_usage WHERE timestamp>=strftime('%s','now','-1 day')").fetchone()[0]
    s['tokens_total'] = conn.execute('SELECT COALESCE(SUM(tokens_used),0) FROM qstart_api_usage').fetchone()[0]
    s['by_model'] = [dict(r) for r in conn.execute(
        "SELECT model, COUNT(*) n, COALESCE(SUM(tokens_used),0) tokens FROM qstart_api_usage GROUP BY model ORDER BY tokens DESC")]
    s['by_lang'] = [dict(r) for r in conn.execute(
        "SELECT COALESCE(lang,'ja') lang, COUNT(*) n FROM qstart_users GROUP BY lang ORDER BY n DESC")]
    s['daily'] = [dict(r) for r in conn.execute(
        """SELECT date(timestamp,'unixepoch') d, COALESCE(SUM(tokens_used),0) tokens, COUNT(DISTINCT user_id) users
           FROM qstart_api_usage WHERE timestamp>=strftime('%s','now','-14 day')
           GROUP BY d ORDER BY d""")]
    s['reports_open'] = conn.execute("SELECT COUNT(*) FROM qstart_reports WHERE status='open'").fetchone()[0]
    conn.close()
    return jsonify({'ok': True, 'stats': s})

@qstart_core.route('/qstart/api/v1/admin/users')
@require_admin
def admin_users():
    qs = (request.args.get('q') or '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per = 30
    conn = db()
    where, vals = '', []
    if qs:
        where = 'WHERE u.user_id LIKE ? OR u.nickname LIKE ? OR u.email LIKE ?'
        vals = [f'%{qs}%'] * 3
    total = conn.execute(f'SELECT COUNT(*) FROM qstart_users u {where}', vals).fetchone()[0]
    rows = conn.execute(f'''SELECT u.user_id, u.nickname, u.email, u.lang, u.created_at,
        COALESCE(f.role,'user') role, COALESCE(f.status,'active') status,
        (SELECT COALESCE(SUM(tokens_used),0) FROM qstart_api_usage a WHERE a.user_id=u.user_id) tokens
        FROM qstart_users u LEFT JOIN qstart_user_flags f ON f.user_id=u.user_id
        {where} ORDER BY u.created_at DESC LIMIT ? OFFSET ?''',
        vals + [per, (page-1)*per]).fetchall()
    conn.close()
    return jsonify({'ok': True, 'total': total, 'page': page, 'per': per,
                    'users': [dict(r) for r in rows]})

@qstart_core.route('/qstart/api/v1/admin/users/<uid>', methods=['PATCH'])
@require_admin
def admin_user_update(uid):
    d = request.get_json(silent=True) or {}
    conn = db()
    conn.execute('INSERT OR IGNORE INTO qstart_user_flags(user_id,updated_at) VALUES(?,?)', (uid, now()))
    sets, vals = [], []
    for k in ['role','status','window_tokens','monthly_tokens','note']:
        if k in d:
            sets.append(f'{k}=?'); vals.append(d[k])
    if sets:
        vals += [cur_uid() or 'admin', now(), uid]
        conn.execute(f'UPDATE qstart_user_flags SET {",".join(sets)}, updated_by=?, updated_at=? WHERE user_id=?', vals)
        conn.commit()
    conn.close()
    alog('user_update', uid, json.dumps(d, ensure_ascii=False))
    return jsonify({'ok': True})

@qstart_core.route('/qstart/api/v1/admin/announce', methods=['GET','POST'])
@require_admin
def admin_announce():
    conn = db()
    if request.method == 'GET':
        rows = conn.execute('SELECT * FROM qstart_announcements ORDER BY id DESC LIMIT 50').fetchall()
        conn.close()
        return jsonify({'ok': True, 'announcements': [dict(r) for r in rows]})
    d = request.get_json(silent=True) or {}
    conn.execute('''INSERT INTO qstart_announcements(title,body,level,lang,target,starts_at,ends_at,created_by,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                 (d.get('title',''), d.get('body',''), d.get('level','info'),
                  d.get('lang','ja'), d.get('target','all'),
                  d.get('starts_at'), d.get('ends_at'), cur_uid() or 'admin', now()))
    conn.commit(); conn.close()
    alog('announce_create', '', d.get('title',''))
    return jsonify({'ok': True})

@qstart_core.route('/qstart/api/v1/admin/announce/<int:aid>', methods=['PATCH','DELETE'])
@require_admin
def admin_announce_edit(aid):
    conn = db()
    if request.method == 'DELETE':
        conn.execute('DELETE FROM qstart_announcements WHERE id=?', (aid,))
    else:
        d = request.get_json(silent=True) or {}
        if 'active' in d:
            conn.execute('UPDATE qstart_announcements SET active=? WHERE id=?', (1 if d['active'] else 0, aid))
    conn.commit(); conn.close()
    alog('announce_edit', str(aid))
    return jsonify({'ok': True})

@qstart_core.route('/qstart/api/v1/admin/models', methods=['GET','POST'])
@require_admin
def admin_models():
    conn = db()
    if request.method == 'GET':
        rows = conn.execute('SELECT * FROM qstart_model_flags').fetchall()
        conn.close()
        return jsonify({'ok': True, 'models': [dict(r) for r in rows]})
    d = request.get_json(silent=True) or {}
    conn.execute('''INSERT INTO qstart_model_flags(model,enabled,maintenance,note,min_role,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(model) DO UPDATE SET
                    enabled=excluded.enabled, maintenance=excluded.maintenance,
                    note=excluded.note, min_role=excluded.min_role, updated_at=excluded.updated_at''',
                 (d.get('model'), 1 if d.get('enabled') else 0, 1 if d.get('maintenance') else 0,
                  d.get('note',''), d.get('min_role','user'), now()))
    conn.commit(); conn.close()
    alog('model_flag', d.get('model',''), json.dumps(d, ensure_ascii=False))
    return jsonify({'ok': True})

@qstart_core.route('/qstart/api/v1/admin/features', methods=['GET','POST'])
@require_admin
def admin_features():
    conn = db()
    if request.method == 'GET':
        rows = conn.execute('SELECT * FROM qstart_feature_flags').fetchall()
        conn.close()
        return jsonify({'ok': True, 'features': [dict(r) for r in rows]})
    d = request.get_json(silent=True) or {}
    conn.execute('''INSERT INTO qstart_feature_flags(key,enabled,rollout_pct,note,updated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                    enabled=excluded.enabled, rollout_pct=excluded.rollout_pct, updated_at=excluded.updated_at''',
                 (d.get('key'), 1 if d.get('enabled') else 0, int(d.get('rollout_pct', 0)), d.get('note',''), now()))
    conn.commit(); conn.close()
    alog('feature_flag', d.get('key',''), json.dumps(d, ensure_ascii=False))
    return jsonify({'ok': True})

@qstart_core.route('/qstart/api/v1/admin/reports')
@require_moderator
def admin_reports():
    conn = db()
    rows = conn.execute("SELECT * FROM qstart_reports ORDER BY (status='open') DESC, id DESC LIMIT 100").fetchall()
    conn.close()
    return jsonify({'ok': True, 'reports': [dict(r) for r in rows]})

@qstart_core.route('/qstart/api/v1/admin/logs')
@require_admin
def admin_logs():
    conn = db()
    rows = conn.execute('SELECT * FROM qstart_admin_log ORDER BY id DESC LIMIT 200').fetchall()
    conn.close()
    return jsonify({'ok': True, 'logs': [dict(r) for r in rows]})


# ========== トークン残高(恒久ボーナス + 消費型ストック) ==========
def get_quota(uid):
    """このユーザーの枠。A方式: 通常枠→恒久ボーナス を先に使い、
    それでも足りないときだけ消費型ストックから引く"""
    conn = db()
    q = conn.execute('SELECT window_bonus,monthly_bonus FROM qstart_user_quota WHERE user_id=?', (uid,)).fetchone()
    s = conn.execute('SELECT tokens FROM qstart_user_stock WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return {
        'window_bonus': (q['window_bonus'] if q else 0) or 0,
        'monthly_bonus': (q['monthly_bonus'] if q else 0) or 0,
        'stock': (s['tokens'] if s else 0) or 0,
    }

def add_quota(uid, window=0, monthly=0, by=''):
    """持続性アリ: 恒久的に枠を増やす"""
    conn = db()
    conn.execute('INSERT OR IGNORE INTO qstart_user_quota(user_id,updated_at) VALUES(?,?)', (uid, now()))
    conn.execute("""UPDATE qstart_user_quota SET window_bonus=window_bonus+?,
                    monthly_bonus=monthly_bonus+?, granted_by=?, updated_at=? WHERE user_id=?""",
                 (window, monthly, by, now(), uid))
    conn.commit(); conn.close()

def add_stock(uid, tokens):
    """持続性ナシ: 使い切りのストックを足す"""
    conn = db()
    conn.execute('INSERT OR IGNORE INTO qstart_user_stock(user_id,tokens,updated_at) VALUES(?,0,?)', (uid, now()))
    conn.execute('UPDATE qstart_user_stock SET tokens=tokens+?, updated_at=? WHERE user_id=?',
                 (tokens, now(), uid))
    conn.commit(); conn.close()

def consume_stock(uid, tokens):
    """ストックから引く。引けた分を返す"""
    conn = db()
    r = conn.execute('SELECT tokens FROM qstart_user_stock WHERE user_id=?', (uid,)).fetchone()
    have = (r['tokens'] if r else 0) or 0
    use = min(have, tokens)
    if use > 0:
        conn.execute('UPDATE qstart_user_stock SET tokens=tokens-?, updated_at=? WHERE user_id=?',
                     (use, now(), uid))
        conn.commit()
    conn.close()
    return use

@qstart_core.route('/qstart/api/v1/balance')
@require_login
def balance():
    uid = cur_uid()
    q = get_quota(uid)
    return jsonify({'ok': True, 'quota': q})


# ========== 料金・購入 ==========
def feature_on(key):
    conn = db()
    r = conn.execute('SELECT enabled FROM qstart_feature_flags WHERE key=?', (key,)).fetchone()
    conn.close()
    return bool(r and r['enabled'])

FX_SOURCES = [
    'https://open.er-api.com/v6/latest/USD',
    'https://api.frankfurter.app/latest?from=USD',
]

def refresh_fx(force=False):
    """為替レートを自動更新(1日1回)。失敗しても既存レートを使い続ける"""
    import urllib.request, json as _j
    conn = db()
    if not force:
        r = conn.execute("""SELECT COUNT(*) FROM qstart_fx
            WHERE updated_at > datetime('now','-20 hours','localtime')""").fetchone()[0]
        if r > 0:
            conn.close(); return {'skipped': True}
    conn.close()

    rates = None
    for url in FX_SOURCES:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Qstart/1.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _j.loads(resp.read().decode())
            rates = data.get('rates') or data.get('conversion_rates')
            if rates:
                break
        except Exception:
            continue
    if not rates:
        return {'ok': False, 'error': 'fetch_failed'}

    conn = db()
    updated = 0
    for row in conn.execute('SELECT currency FROM qstart_fx').fetchall():
        c = row['currency']
        if c == 'USD':
            conn.execute("UPDATE qstart_fx SET rate=1.0, updated_at=? WHERE currency='USD'", (now(),))
            updated += 1
        elif c in rates:
            conn.execute('UPDATE qstart_fx SET rate=?, updated_at=? WHERE currency=?',
                         (float(rates[c]), now(), c))
            updated += 1
    conn.commit(); conn.close()
    return {'ok': True, 'updated': updated}


@qstart_core.route('/qstart/api/v1/admin/fx/refresh', methods=['POST'])
@require_admin
def admin_fx_refresh():
    r = refresh_fx(force=True)
    alog('fx_refresh', '', str(r))
    return jsonify(dict({'ok': True}, **r))


@qstart_core.route('/qstart/api/v1/pricing')
def pricing():
    """USD基準の価格 + 各国通貨への換算レート"""
    try:
        refresh_fx()
    except Exception:
        pass
    conn = db()
    p = conn.execute('SELECT * FROM qstart_pricing WHERE id=1').fetchone()
    fx = conn.execute('SELECT * FROM qstart_fx ORDER BY currency').fetchall()
    conn.close()
    return jsonify({
        'ok': True,
        'sale_on': feature_on('purchase') and bool(p['enabled']),
        'base_currency': 'USD',
        'usd_per_100_tokens': p['usd_per_100_tokens'],
        'min_tokens': p['min_tokens'],
        'fx': [dict(r) for r in fx],
    })

@qstart_core.route('/qstart/api/v1/purchase', methods=['POST'])
@require_login
def purchase():
    """購入リクエスト。価格はUSDで確定し、表示通貨は参考値"""
    uid = cur_uid()
    d = request.get_json(silent=True) or {}
    try:
        tokens = int(d.get('tokens', 0))
    except Exception:
        return jsonify({'ok': False, 'error': 'bad_tokens'}), 400

    conn = db()
    p = conn.execute('SELECT * FROM qstart_pricing WHERE id=1').fetchone()
    if not feature_on('purchase') or not p['enabled']:
        conn.close()
        return jsonify({'ok': False, 'error': 'sale_off'}), 403
    if tokens < (p['min_tokens'] or 10000):
        conn.close()
        return jsonify({'ok': False, 'error': 'too_few', 'min': p['min_tokens']}), 400

    usd = round(tokens / 100.0 * p['usd_per_100_tokens'], 4)
    disp = (d.get('currency') or 'USD').upper()
    fx = conn.execute('SELECT * FROM qstart_fx WHERE currency=?', (disp,)).fetchone()
    local = round(usd * fx['rate'], fx['decimals']) if fx else usd

    conn.execute("""INSERT INTO qstart_purchases(user_id,tokens,amount,currency,status,note,created_at)
                    VALUES(?,?,?, 'USD','pending',?,?)""",
                 (uid, tokens, usd, f'display={disp} {local}', now()))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'tokens': tokens, 'usd': usd,
                    'display_currency': disp, 'display_amount': local,
                    'message': '決済システムは準備中です'})


# ========== 料金設定(管理者) ==========
@qstart_core.route('/qstart/api/v1/admin/pricing', methods=['GET','POST'])
@require_admin
def admin_pricing():
    conn = db()
    if request.method == 'GET':
        p = conn.execute('SELECT * FROM qstart_pricing WHERE id=1').fetchone()
        fx = conn.execute('SELECT * FROM qstart_fx ORDER BY currency').fetchall()
        buys = conn.execute('SELECT * FROM qstart_purchases ORDER BY id DESC LIMIT 50').fetchall()
        conn.close()
        return jsonify({'ok': True, 'pricing': dict(p),
                        'sale_on': feature_on('purchase'),
                        'fx': [dict(r) for r in fx],
                        'purchases': [dict(r) for r in buys]})
    d = request.get_json(silent=True) or {}
    if 'usd_per_100_tokens' in d or 'enabled' in d or 'min_tokens' in d:
        p = conn.execute('SELECT * FROM qstart_pricing WHERE id=1').fetchone()
        conn.execute("""UPDATE qstart_pricing SET usd_per_100_tokens=?, min_tokens=?,
                        enabled=?, updated_at=? WHERE id=1""",
                     (float(d.get('usd_per_100_tokens', p['usd_per_100_tokens'])),
                      int(d.get('min_tokens', p['min_tokens'])),
                      1 if d.get('enabled') else 0, now()))
    if d.get('fx'):
        for f in d['fx']:
            conn.execute('UPDATE qstart_fx SET rate=?, updated_at=? WHERE currency=?',
                         (float(f['rate']), now(), f['currency'].upper()))
    conn.commit(); conn.close()
    alog('pricing_update', 'USD', json.dumps(d, ensure_ascii=False)[:400])
    return jsonify({'ok': True})


# ========== 優待コード(管理者) ==========
@qstart_core.route('/qstart/api/v1/admin/promo', methods=['GET'])
@require_admin
def admin_promo_list():
    conn = db()
    rows = conn.execute("""SELECT code,type,value,description,max_uses,used_count,
        expires_at,active,created_at,created_by,COALESCE(persistent,0) persistent
        FROM promo_codes ORDER BY created_at DESC""").fetchall()
    conn.close()
    return jsonify({'ok': True, 'codes': [dict(r) for r in rows]})

@qstart_core.route('/qstart/api/v1/admin/promo', methods=['POST'])
@require_admin
def admin_promo_create():
    import secrets as _sec
    d = request.get_json(silent=True) or {}
    code = (d.get('code') or '').strip().upper()
    if not code:
        code = 'QS-' + _sec.token_hex(4).upper()
    try:
        value = int(d.get('value', 10000))
        max_uses = int(d.get('max_uses', 1))
    except Exception:
        return jsonify({'ok': False, 'error': 'bad_number'}), 400
    who = session.get('staff_id') or cur_uid() or 'admin'
    conn = db()
    try:
        conn.execute("""INSERT INTO promo_codes(code,type,value,description,max_uses,expires_at,created_by,persistent)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (code, d.get('type','token_add'), value, d.get('description',''),
                      max_uses, d.get('expires_at') or None, who,
                      1 if str(d.get('persistent','0')) in ('1','true','True') else 0))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'ok': False, 'error': 'duplicate' if 'UNIQUE' in str(e) else str(e)}), 400
    conn.close()
    alog('promo_create', code, f"{d.get('type')} {value} x{max_uses}")
    return jsonify({'ok': True, 'code': code})

@qstart_core.route('/qstart/api/v1/admin/promo/<code>', methods=['PATCH','DELETE'])
@require_admin
def admin_promo_edit(code):
    conn = db()
    if request.method == 'DELETE':
        conn.execute('DELETE FROM promo_codes WHERE code=?', (code,))
        act = 'promo_delete'
    else:
        d = request.get_json(silent=True) or {}
        conn.execute('UPDATE promo_codes SET active=? WHERE code=?',
                     (1 if d.get('active') else 0, code))
        act = 'promo_toggle'
    conn.commit(); conn.close()
    alog(act, code)
    return jsonify({'ok': True})

@qstart_core.route('/qstart/api/v1/admin/promo/<code>/uses')
@require_admin
def admin_promo_uses(code):
    conn = db()
    rows = conn.execute("""SELECT user_id, datetime(used_at,'unixepoch','localtime') used_at
                           FROM promo_uses WHERE code=? ORDER BY used_at DESC LIMIT 100""", (code,)).fetchall()
    conn.close()
    return jsonify({'ok': True, 'uses': [dict(r) for r in rows]})


# ========== プロジェクトページ ==========
@qstart_core.route('/qstart/projects')
def projects_page():
    if not cur_uid():
        return render_template('qstart_projects.html', need_login=True)
    return render_template('qstart_projects.html', need_login=False)

@qstart_core.route('/qstart/project/<pid>')
def project_detail_page(pid):
    if not cur_uid():
        return render_template('qstart_project.html', pid=pid, need_login=True)
    return render_template('qstart_project.html', pid=pid, need_login=False)


# ========== 管理者ページ ==========
@qstart_core.route('/qstart/admin')
def admin_page():
    if qstart_role() != 'admin':
        return render_template('qstart_admin_login.html',
                               logged_in=bool(cur_uid() or session.get('staff_id')))
    return render_template('qstart_admin.html')


@qstart_core.route('/qstart/admin/login', methods=['POST'])
def admin_login():
    """管理者ログイン(許可された社員アカウントのみ)"""
    import hashlib as _h
    d = request.get_json(silent=True) or {}
    sid = (d.get('staff_id') or '').strip()
    pw = d.get('password') or ''
    if not sid or not pw:
        return jsonify({'ok': False, 'error': 'missing'}), 400

    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    ih = _h.sha256(ip.encode()).hexdigest()[:16]
    conn = db()
    n = conn.execute("""SELECT COUNT(*) FROM qstart_admin_log
        WHERE action='admin_login_fail' AND ip_hash=?
        AND created_at > datetime('now','-60 seconds','localtime')""", (ih,)).fetchone()[0]
    if n >= 10:
        conn.close()
        return jsonify({'ok': False, 'error': 'too_many'}), 429

    def fail(reason=''):
        c2 = db()
        c2.execute("""INSERT INTO qstart_admin_log(admin_id,action,target,detail,ip_hash,created_at)
                      VALUES(?,?,?,?,?,?)""", (sid, 'admin_login_fail', '', reason, ih, now()))
        c2.commit(); c2.close()
        return jsonify({'ok': False, 'error': 'invalid'}), 401

    row = conn.execute('SELECT staff_id,password_hash,name,status FROM qz_staff WHERE staff_id=?', (sid,)).fetchone()
    conn.close()

    if not row:
        return fail('no_user')
    # 既存の照合関数を借りる(循環importを避けて関数内で読む)
    try:
        from qz_common import verify_password, dec
    except Exception:
        return jsonify({'ok': False, 'error': 'server'}), 500
    if not verify_password(pw, row['password_hash']):
        return fail('bad_pw')
    if (row['status'] or 'active') != 'active':
        return fail('status_' + (row['status'] or ''))
    if sid not in QSTART_ADMIN_STAFF:
        return fail('not_allowed')

    session['staff_id'] = sid
    try:
        session['staff_name'] = dec(row['name'])
    except Exception:
        session['staff_name'] = sid
    session.permanent = True

    c3 = db()
    c3.execute("""INSERT INTO qstart_admin_log(admin_id,action,target,detail,ip_hash,created_at)
                  VALUES(?,?,?,?,?,?)""", (sid, 'admin_login', '', '', ih, now()))
    c3.commit(); c3.close()
    return jsonify({'ok': True})


@qstart_core.route('/qstart/admin/logout', methods=['POST'])
def admin_logout():
    alog('admin_logout')
    session.pop('staff_id', None)
    session.pop('staff_name', None)
    return jsonify({'ok': True})
