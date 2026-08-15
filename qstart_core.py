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
    # 日本時間で統一(サーバーはUTCなので9時間ずらす)
    try:
        import pytz
        from datetime import datetime as _dt
        return _dt.now(pytz.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        from datetime import datetime as _dt, timedelta as _td
        return (_dt.utcnow() + _td(hours=9)).strftime('%Y-%m-%d %H:%M:%S')

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

def require_moderator(f):
    @wraps(f)
    def w(*a, **kw):
        if qstart_role() not in ('admin', 'moderator'):
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
    conn.execute('DELETE FROM qstart_messages WHERE user_id=?', (uid,))
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
# ========== チャット履歴 ==========
def history_on(uid):
    """このユーザーが履歴保存をONにしているか"""
    if not uid: return False
    conn = db()
    r = conn.execute('SELECT save_history FROM qstart_settings WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return bool(r['save_history']) if r else True


def save_message(uid, chat_id, role, content, model='', tokens=0, title=None):
    """メッセージを1件保存。履歴OFFなら何もしない"""
    if not uid or not chat_id or not history_on(uid):
        return False
    conn = db()
    conn.execute("""INSERT INTO qstart_messages(chat_id,user_id,role,content,model,tokens,created_at)
                    VALUES(?,?,?,?,?,?,?)""",
                 (chat_id, uid, role, (content or '')[:20000], model, tokens, now()))
    # チャット一覧も更新
    conn.execute("""INSERT INTO qstart_chats(chat_id,user_id,title,model,created_at,updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET updated_at=excluded.updated_at""",
                 (chat_id, uid, (title or content or '')[:80], model, now(), now()))
    conn.commit(); conn.close()
    return True


@qstart_core.route('/qstart/api/v1/search')
@require_login
def search_chats():
    """チャット履歴を全文検索"""
    uid = cur_uid()
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify({'ok': True, 'results': []})
    conn = db()
    rows = conn.execute("""SELECT m.chat_id, m.role, m.content, m.created_at,
        c.title, c.project_id FROM qstart_messages m
        LEFT JOIN qstart_chats c ON c.chat_id = m.chat_id
        WHERE m.user_id=? AND m.content LIKE ?
        ORDER BY m.id DESC LIMIT 40""", (uid, f'%{q}%')).fetchall()
    out, seen = [], set()
    for r in rows:
        d = dict(r); c = d['content'] or ''
        i = c.lower().find(q.lower())
        if i >= 0:
            s = max(0, i - 40)
            d['snippet'] = ('…' if s > 0 else '') + c[s:i+len(q)+60] + ('…' if i+len(q)+60 < len(c) else '')
        else:
            d['snippet'] = c[:100]
        d.pop('content', None)
        out.append(d); seen.add(d['chat_id'])
    conn.close()
    return jsonify({'ok': True, 'results': out, 'chats': len(seen)})


@qstart_core.route('/qstart/api/v1/chats/<cid>/export')
@require_login
def export_chat(cid):
    """会話をMarkdownで書き出す"""
    uid = cur_uid()
    fmt = request.args.get('format', 'md')
    conn = db()
    meta = conn.execute('SELECT * FROM qstart_chats WHERE chat_id=? AND user_id=?', (cid, uid)).fetchone()
    msgs = conn.execute("""SELECT role,content,model,created_at FROM qstart_messages
        WHERE chat_id=? AND user_id=? ORDER BY id""", (cid, uid)).fetchall()
    conn.close()
    if not msgs:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    title = (meta['title'] if meta else '') or '無題のチャット'
    if fmt == 'json':
        return jsonify({'ok': True, 'title': title,
                        'messages': [dict(m) for m in msgs]})
    lines = [f'# {title}', '', f'*Qstart で書き出し — {now()}*', '', '---', '']
    names = {'equi': 'Equi 1', 'zin': 'zin 1', 'pure': 'Pure 1', 'apex': 'Apex 1'}
    for m in msgs:
        who = 'あなた' if m['role'] == 'user' else ('Qstart ' + names.get(m['model'], m['model'] or ''))
        lines.append(f'### {who}')
        lines.append('')
        lines.append(m['content'] or '')
        lines.append('')
    lines += ['---', '', 'Qstart by Qzero会社 — ゼロから作られたAI']
    return jsonify({'ok': True, 'title': title, 'markdown': '\n'.join(lines)})


@qstart_core.route('/qstart/api/v1/chats')
@require_login
def list_chats():
    uid = cur_uid()
    conn = db()
    rows = conn.execute("""SELECT c.chat_id, c.title, c.project_id, c.model, c.updated_at,
        (SELECT COUNT(*) FROM qstart_messages m WHERE m.chat_id=c.chat_id) n
        FROM qstart_chats c WHERE c.user_id=?
        ORDER BY c.updated_at DESC LIMIT 100""", (uid,)).fetchall()
    conn.close()
    return jsonify({'ok': True, 'chats': [dict(r) for r in rows]})


@qstart_core.route('/qstart/api/v1/chats/<cid>')
@require_login
def get_chat(cid):
    uid = cur_uid()
    conn = db()
    msgs = conn.execute("""SELECT role,content,model,created_at FROM qstart_messages
        WHERE chat_id=? AND user_id=? ORDER BY id LIMIT 500""", (cid, uid)).fetchall()
    meta = conn.execute('SELECT * FROM qstart_chats WHERE chat_id=? AND user_id=?', (cid, uid)).fetchone()
    conn.close()
    return jsonify({'ok': True, 'chat': dict(meta) if meta else None,
                    'messages': [dict(r) for r in msgs]})


@qstart_core.route('/qstart/api/v1/chats/<cid>', methods=['PATCH','DELETE'])
@require_login
def edit_chat(cid):
    uid = cur_uid()
    conn = db()
    if request.method == 'DELETE':
        conn.execute('DELETE FROM qstart_messages WHERE chat_id=? AND user_id=?', (cid, uid))
        conn.execute('DELETE FROM qstart_chats WHERE chat_id=? AND user_id=?', (cid, uid))
    else:
        d = request.get_json(silent=True) or {}
        sets, vals = [], []
        for k in ['title', 'project_id']:
            if k in d:
                sets.append(f'{k}=?'); vals.append(d[k])
        if sets:
            vals += [now(), cid, uid]
            conn.execute(f'UPDATE qstart_chats SET {",".join(sets)}, updated_at=? WHERE chat_id=? AND user_id=?', vals)
    conn.commit(); conn.close()
    return jsonify({'ok': True})


# ========== 問い合わせ ==========
def init_contact_table():
    conn = db()
    conn.execute('''CREATE TABLE IF NOT EXISTS qstart_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, email TEXT, category TEXT, subject TEXT, body TEXT,
        user_id TEXT, lang TEXT DEFAULT 'ja',
        status TEXT DEFAULT 'open', reply TEXT, handled_by TEXT,
        ip_hash TEXT, created_at TEXT, replied_at TEXT
    )''')
    conn.commit(); conn.close()

init_contact_table()


@qstart_core.route('/qstart/contact')
def contact_page():
    return render_template('qstart_contact.html')


@qstart_core.route('/qstart/api/v1/contact', methods=['POST'])
def contact_submit():
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()[:60]
    email = (d.get('email') or '').strip()[:200]
    body = (d.get('body') or '').strip()[:4000]
    if not body or len(body) < 5:
        return jsonify({'ok': False, 'error': 'お問い合わせ内容を入力してください'}), 400
    if not email or '@' not in email:
        return jsonify({'ok': False, 'error': 'メールアドレスを入力してください'}), 400

    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    ih = hashlib.sha256(ip.encode()).hexdigest()[:16]
    conn = db()
    n = conn.execute("""SELECT COUNT(*) FROM qstart_contacts
        WHERE ip_hash=? AND created_at > datetime('now','-1 hour','localtime')""", (ih,)).fetchone()[0]
    if n >= 5:
        conn.close()
        return jsonify({'ok': False, 'error': '送信が多すぎます。時間をおいてお試しください。'}), 429

    conn.execute('''INSERT INTO qstart_contacts
        (name,email,category,subject,body,user_id,lang,ip_hash,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)''',
        (name, email, (d.get('category') or 'other')[:40],
         (d.get('subject') or '')[:200], body,
         cur_uid() or '', (d.get('lang') or 'ja')[:5], ih, now()))
    conn.commit(); conn.close()

    # 自動返信
    try:
        import qstart_mail as qm
        html = ('<div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;'
          'padding:36px 32px;border:1px solid #ece9e2;font-family:sans-serif;">'
          '<div style="font-size:22px;font-weight:700;color:#14213d;">Q<span style="color:#b8860b;">start</span></div>'
          '<div style="font-size:11px;color:#a09a8a;margin-bottom:26px;">by Qzero会社</div>'
          '<div style="font-size:17px;font-weight:600;color:#14213d;margin-bottom:10px;">'
          'お問い合わせを受け付けました</div>'
          '<div style="font-size:13.5px;color:#4a4438;line-height:1.9;margin-bottom:20px;">'
          'お問い合わせいただきありがとうございます。<br>内容を確認のうえ、順次ご返信いたします。</div>'
          '<div style="background:#faf9f5;border:1px solid #ece9e2;border-radius:10px;'
          'padding:14px;font-size:13px;color:#5c5647;line-height:1.8;white-space:pre-wrap;">'
          + (body[:500].replace('<','&lt;')) + '</div>'
          '<div style="font-size:12px;color:#8a8270;margin-top:22px;line-height:1.8;">'
          'このメールは自動送信です。返信は不要です。</div></div>')
        qm.send_mail(email, 'Qstart お問い合わせを受け付けました', html, kind='contact')
    except Exception:
        pass
    return jsonify({'ok': True})


@qstart_core.route('/qstart/api/v1/admin/contacts')
@require_moderator
def admin_contacts():
    conn = db()
    rows = conn.execute("""SELECT * FROM qstart_contacts
        ORDER BY (status='open') DESC, id DESC LIMIT 100""").fetchall()
    conn.close()
    return jsonify({'ok': True, 'contacts': [dict(r) for r in rows]})


@qstart_core.route('/qstart/api/v1/admin/contacts/<int:cid>', methods=['PATCH'])
@require_moderator
def admin_contact_update(cid):
    d = request.get_json(silent=True) or {}
    conn = db()
    if d.get('reply'):
        conn.execute('UPDATE qstart_contacts SET reply=?, status=?, handled_by=?, replied_at=? WHERE id=?',
                     (d['reply'][:4000], 'replied',
                      cur_uid() or session.get('staff_id',''), now(), cid))
        r = conn.execute('SELECT email,body FROM qstart_contacts WHERE id=?', (cid,)).fetchone()
        conn.commit(); conn.close()
        if r and r['email']:
            try:
                import qstart_mail as qm
                html = ('<div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;'
                  'padding:36px 32px;border:1px solid #ece9e2;font-family:sans-serif;">'
                  '<div style="font-size:22px;font-weight:700;color:#14213d;">Q<span style="color:#b8860b;">start</span></div>'
                  '<div style="font-size:11px;color:#a09a8a;margin-bottom:26px;">by Qzero会社</div>'
                  '<div style="font-size:17px;font-weight:600;color:#14213d;margin-bottom:14px;">'
                  'お問い合わせへの回答</div>'
                  '<div style="font-size:13.5px;color:#4a4438;line-height:1.9;white-space:pre-wrap;">'
                  + d['reply'][:2000].replace('<','&lt;') + '</div>'
                  '<div style="border-top:1px solid #f0ede5;margin-top:24px;padding-top:14px;'
                  'font-size:12px;color:#a09a8a;line-height:1.7;">お問い合わせ内容:<br>'
                  + (r['body'] or '')[:300].replace('<','&lt;') + '</div></div>')
                qm.send_mail(r['email'], 'Qstart お問い合わせへの回答', html, kind='contact_reply')
            except Exception:
                pass
        alog('contact_reply', str(cid))
        return jsonify({'ok': True})
    if 'status' in d:
        conn.execute('UPDATE qstart_contacts SET status=? WHERE id=?', (d['status'], cid))
        conn.commit()
    conn.close()
    return jsonify({'ok': True})


@qstart_core.route('/qstart/status')
def status_page():
    return render_template('qstart_status.html')


# ========== Voto — 採点API専用モデル ==========
_VOTO = None

def load_voto():
    """Votoモデルを読み込む(APIからのみ使う)"""
    global _VOTO
    if _VOTO is not None:
        return _VOTO
    u = get_model_source('voto')
    if not u:
        return None
    try:
        from equi_inference import EquiInference
        _VOTO = EquiInference(u['weights'], u['config'])
    except Exception:
        return None
    return _VOTO


def voto_judge(question, correct, user_answer):
    """採点する。(判定, 理由) を返す"""
    m = load_voto()
    if not m:
        return None, None
    prompt = f'問題「{question[:80]}」正解「{correct[:60]}」回答「{user_answer[:60]}」'
    try:
        out = (m.chat(prompt) or '').strip()
    except Exception:
        return None, None
    if out.startswith('部分'):
        v = '部分正解'
    elif out.startswith('正解'):
        v = '正解'
    elif out.startswith('不正解'):
        v = '不正解'
    else:
        return None, out
    reason = out.split('。', 1)[1].strip() if '。' in out else ''
    return v, reason


# --- 段階的な採点(自由研究の4段階) ---
def grade_answer(question, correct, user_answer, alt_answers=None, use_ai=True):
    """
    ① 完全一致 → ② 表記ゆれ → ③ 別解 → ④ Voto(AI)
    どの段階で判定したかも返す
    """
    import unicodedata as _u
    def norm(s):
        s = _u.normalize('NFKC', (s or '').strip())
        return s.replace(' ', '').replace('　', '')

    ua, ca = norm(user_answer), norm(correct)
    if not ua:
        return {'verdict': '不正解', 'reason': '答えが入力されていません。',
                'stage': 'empty', 'by': 'rule'}

    # ① 完全一致
    if ua == ca:
        return {'verdict': '正解', 'reason': '正解と一致しています。',
                'stage': 1, 'by': 'exact'}

    # ② 表記ゆれ(ひらがな/カタカナ/全半角/括弧/単位)
    K = 'ァィゥェォャュョッアイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポ'
    H = 'ぁぃぅぇぉゃゅょっあいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんがぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ'
    k2h = str.maketrans(K, H)
    if ua.translate(k2h) == ca.translate(k2h):
        return {'verdict': '正解', 'reason': '書き方は違いますが同じ答えです。',
                'stage': 2, 'by': 'kana'}
    import re as _re
    strip_p = lambda s: _re.sub(r'[（(][^）)]*[）)]', '', s).strip()
    if strip_p(ua) and strip_p(ua) == strip_p(ca):
        return {'verdict': '正解', 'reason': '括弧の有無が違うだけです。',
                'stage': 2, 'by': 'paren'}
    if ca.endswith(('県','府','都')) and ua == ca[:-1]:
        return {'verdict': '正解', 'reason': '都道府県名が省略されていますが同じ場所です。',
                'stage': 2, 'by': 'pref'}

    # ③ 別解
    if alt_answers:
        alts = [norm(a) for a in _re.split(r'[,、|/]', alt_answers) if a.strip()]
        if ua in alts:
            return {'verdict': '正解', 'reason': '別の正しい答えです。',
                    'stage': 3, 'by': 'alt'}

    # ④ Voto(AI)
    if use_ai:
        v, r = voto_judge(question, correct, user_answer)
        if v:
            return {'verdict': v, 'reason': r or '', 'stage': 4, 'by': 'voto'}

    return {'verdict': '不正解', 'reason': f'正しい答えは「{correct}」です。',
            'stage': 'fallback', 'by': 'rule'}


@qstart_core.route('/qstart/api/v1/grade', methods=['POST'])
def api_grade():
    """採点API(APIキー or ログインが必要)"""
    d = request.get_json(silent=True) or {}
    q = (d.get('question') or '').strip()
    c = (d.get('correct') or '').strip()
    u = (d.get('answer') or '').strip()
    if not c:
        return jsonify({'ok': False, 'error': 'correct_required'}), 400
    r = grade_answer(q, c, u, d.get('alt'), use_ai=d.get('use_ai', True))
    return jsonify(dict({'ok': True}, **r))


@qstart_core.route('/qstart/api/v1/grade/batch', methods=['POST'])
def api_grade_batch():
    """まとめて採点(自由研究の精度測定用)"""
    d = request.get_json(silent=True) or {}
    items = d.get('items') or []
    if not isinstance(items, list) or len(items) > 500:
        return jsonify({'ok': False, 'error': 'bad_items'}), 400
    use_ai = d.get('use_ai', True)
    out = []
    for it in items:
        r = grade_answer(it.get('question',''), it.get('correct',''),
                         it.get('answer',''), it.get('alt'), use_ai=use_ai)
        r['id'] = it.get('id')
        out.append(r)
    # 段階ごとの内訳
    import collections as _c
    stats = dict(_c.Counter(str(x['stage']) for x in out))
    return jsonify({'ok': True, 'results': out, 'stats': stats, 'n': len(out)})


# ========== メンテナンス ==========
def init_maintenance():
    _mk_compensation_cols()

def _mk_compensation_cols():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS qstart_maintenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        starts_at TEXT, ends_at TEXT, title TEXT, body TEXT,
        status TEXT DEFAULT 'scheduled', created_by TEXT, created_at TEXT)""")
    for col, typ in [('is_emergency','INTEGER DEFAULT 0'),
                     ('compensate','INTEGER DEFAULT 0'),
                     ('compensated_at','TEXT')]:
        try: conn.execute(f'ALTER TABLE qstart_maintenance ADD COLUMN {col} {typ}')
        except Exception: pass
    conn.commit(); conn.close()

def _init_maintenance_orig():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS qstart_maintenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        starts_at TEXT, ends_at TEXT,
        title TEXT, body TEXT,
        status TEXT DEFAULT 'scheduled',
        created_by TEXT, created_at TEXT
    )""")
    conn.commit(); conn.close()

init_maintenance()


def current_maintenance():
    """今メンテナンス中か / 予告中か"""
    conn = db()
    n = now()[:16]   # 'YYYY-MM-DD HH:MM' に揃える(保存側と桁を合わせる)
    active = conn.execute("""SELECT * FROM qstart_maintenance
        WHERE status IN ('scheduled','active')
          AND substr(starts_at,1,16) <= ? AND substr(ends_at,1,16) >= ?
        ORDER BY starts_at LIMIT 1""", (n, n)).fetchone()
    upcoming = conn.execute("""SELECT * FROM qstart_maintenance
        WHERE status='scheduled' AND substr(starts_at,1,16) > ?
        ORDER BY starts_at LIMIT 1""", (n,)).fetchone()
    conn.close()
    return (dict(active) if active else None), (dict(upcoming) if upcoming else None)


def in_maintenance():
    a, _ = current_maintenance()
    return a is not None


@qstart_core.route('/qstart/api/v1/maintenance')
def maintenance_info():
    a, u = current_maintenance()
    return jsonify({'ok': True, 'active': a, 'upcoming': u,
                    'is_admin': qstart_role() == 'admin'})


@qstart_core.route('/qstart/api/v1/admin/maintenance', methods=['GET','POST'])
@require_admin
def admin_maintenance():
    conn = db()
    if request.method == 'GET':
        rows = conn.execute('SELECT * FROM qstart_maintenance ORDER BY starts_at DESC LIMIT 50').fetchall()
        conn.close()
        a, u = current_maintenance()
        return jsonify({'ok': True, 'list': [dict(r) for r in rows],
                        'active': a, 'upcoming': u})

    d = request.get_json(silent=True) or {}
    st = (d.get('starts_at') or '').strip()
    en = (d.get('ends_at') or '').strip()
    if not st or not en:
        conn.close()
        return jsonify({'ok': False, 'error': '開始と終了の日時を指定してください'}), 400
    # 24時間以上先しか予約できない
    import datetime as _dt
    try:
        _s = _dt.datetime.strptime(st, '%Y-%m-%d %H:%M')
        _e = _dt.datetime.strptime(en, '%Y-%m-%d %H:%M')
    except Exception:
        conn.close()
        return jsonify({'ok': False, 'error': '日時の形式が正しくありません'}), 400
    if _e <= _s:
        conn.close()
        return jsonify({'ok': False, 'error': '終了は開始より後にしてください'}), 400
    _jst_now = _dt.datetime.strptime(now()[:16], '%Y-%m-%d %H:%M')
    if not d.get('emergency') and not d.get('force') and (_s - _jst_now).total_seconds() < 86400:
        conn.close()
        return jsonify({'ok': False, 'error': 'メンテナンスは24時間以上先から予約できます',
                        'need_force': True}), 400

    conn.execute("""INSERT INTO qstart_maintenance
        (starts_at,ends_at,title,body,status,created_by,created_at,is_emergency,compensate)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (_s.strftime('%Y-%m-%d %H:%M'), _e.strftime('%Y-%m-%d %H:%M'),
         (d.get('title') or 'メンテナンスのお知らせ')[:120],
         (d.get('body') or '')[:2000],
         'active' if d.get('emergency') else 'scheduled',
         cur_uid() or session.get('staff_id','admin'), now(),
         1 if d.get('emergency') else 0,
         int(d.get('compensate', 0) or 0)))
    conn.commit(); conn.close()
    alog('maintenance_create', st + '〜' + en)
    return jsonify({'ok': True})


@qstart_core.route('/qstart/api/v1/admin/maintenance/<int:mid>', methods=['PATCH','DELETE'])
@require_admin
def admin_maintenance_edit(mid):
    conn = db()
    if request.method == 'DELETE':
        conn.execute('DELETE FROM qstart_maintenance WHERE id=?', (mid,))
        act = 'maintenance_delete'
    else:
        d = request.get_json(silent=True) or {}
        conn.execute('UPDATE qstart_maintenance SET status=? WHERE id=?',
                     (d.get('status', 'done'), mid))
        act = 'maintenance_' + d.get('status', 'done')
    conn.commit(); conn.close()
    alog(act, str(mid))
    return jsonify({'ok': True})


@qstart_core.route('/qstart/api/v1/admin/maintenance/<int:mid>/preview')
@require_admin
def admin_maintenance_preview(mid):
    """補償の対象者と付与量を計算(まだ付与しない)"""
    conn = db()
    m = conn.execute('SELECT * FROM qstart_maintenance WHERE id=?', (mid,)).fetchone()
    if not m:
        conn.close(); return jsonify({'ok': False, 'error': 'not_found'}), 404

    import datetime as _dt
    try:
        _s = _dt.datetime.strptime(m['starts_at'], '%Y-%m-%d %H:%M')
        _e = _dt.datetime.strptime(m['ends_at'], '%Y-%m-%d %H:%M')
        hours = max(0.0, (_e - _s).total_seconds() / 3600)
    except Exception:
        hours = 0.0

    # 3時間で4,000トークン → 1時間あたり約1,333
    per_hour = 4000 / 3.0
    base = int(hours * per_hour)

    # 対象: メンテ期間中またはその前後3時間に使っていた人 = 影響を受けた人
    rows = conn.execute("""SELECT u.user_id, u.nickname,
        COALESCE(f.status,'active') status,
        COALESCE(s.plan,'free') plan,
        s.expires_at,
        (SELECT COUNT(*) FROM qstart_api_usage a
         WHERE a.user_id=u.user_id
           AND a.timestamp >= strftime('%s',?) - 10800
           AND a.timestamp <= strftime('%s',?)) hit
        FROM qstart_users u
        LEFT JOIN qstart_user_flags f ON f.user_id=u.user_id
        LEFT JOIN qstart_subscriptions s ON s.user_id=u.user_id
        ORDER BY u.created_at""", (m['starts_at'], m['ends_at'])).fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d['extend_hours'] = 0
        if d['status'] != 'active':
            d['amount'] = 0
            d['reason'] = '凍結中のため対象外'
        elif (d.get('plan') or 'free') != 'free':
            # 有料プラン: 契約期間を延長(トークンも少し付ける)
            d['amount'] = base
            d['extend_hours'] = round(hours, 2)
            d['reason'] = f"有料プラン（{d['plan']}）→ 期間を{hours:.1f}時間延長"
        elif d['hit'] > 0:
            d['amount'] = base
            d['reason'] = 'メンテ前後に利用あり'
        else:
            d['amount'] = base // 2
            d['reason'] = '利用なし（半額）'
        out.append(d)

    return jsonify({'ok': True, 'hours': round(hours, 2), 'base': base,
                    'per_hour': int(per_hour),
                    'total': sum(x['amount'] for x in out),
                    'users': out, 'already': bool(m['compensated_at'])})


@qstart_core.route('/qstart/api/v1/admin/maintenance/<int:mid>/compensate', methods=['POST'])
@require_admin
def admin_maintenance_compensate(mid):
    """メンテナンスのお詫びとして全ユーザーの予備タンクにトークンを付与"""
    d = request.get_json(silent=True) or {}
    amount = int(d.get('amount', 0) or 0)
    if amount <= 0:
        return jsonify({'ok': False, 'error': '付与量を指定してください'}), 400
    conn = db()
    m = conn.execute('SELECT * FROM qstart_maintenance WHERE id=?', (mid,)).fetchone()
    if not m:
        conn.close(); return jsonify({'ok': False, 'error': 'not_found'}), 404
    if m['compensated_at']:
        conn.close(); return jsonify({'ok': False, 'error': '既に付与済みです'}), 400

    n_now = now()
    # 個別指定(プレビューで調整した結果)があればそれを使う
    plan = d.get('users')
    if plan:
        pairs = [(x['user_id'], int(x.get('amount', 0) or 0)) for x in plan]
    else:
        pairs = [(r['user_id'], amount) for r in conn.execute('SELECT user_id FROM qstart_users')]
    users = [p[0] for p in pairs if p[1] > 0]
    ext_map = {}
    if plan:
        ext_map = {x['user_id']: float(x.get('extend_hours', 0) or 0) for x in plan}
    for uid, amt in pairs:
        hrs = ext_map.get(uid, 0)
        if amt <= 0 and hrs <= 0: continue
        if amt > 0:
            conn.execute('INSERT OR IGNORE INTO qstart_user_stock(user_id,tokens,updated_at) VALUES(?,0,?)',
                         (uid, n_now))
            conn.execute('UPDATE qstart_user_stock SET tokens=tokens+?, updated_at=? WHERE user_id=?',
                         (amt, n_now, uid))
        if hrs > 0:
            # 有料プランの契約期間を延長
            conn.execute("""UPDATE qstart_subscriptions
                SET expires_at = datetime(expires_at, '+' || ? || ' hours'),
                    extended_hours = COALESCE(extended_hours,0) + ?, updated_at=?
                WHERE user_id=?""", (hrs, hrs, n_now, uid))
        conn.execute("""INSERT INTO qstart_compensations
            (maintenance_id,user_id,plan,tokens,extended_hours,created_at)
            VALUES(?,?,?,?,?,?)""",
            (mid, uid, 'paid' if hrs > 0 else 'free', amt, hrs, n_now))
    conn.execute('UPDATE qstart_maintenance SET compensate=?, compensated_at=? WHERE id=?',
                 (amount, n_now, mid))

    # お知らせも自動配信
    conn.execute("""INSERT INTO qstart_announcements
        (title,body,level,lang,target,active,created_by,created_at)
        VALUES(?,?,?,?,?,1,?,?)""",
        ('メンテナンスのお詫び',
         f'{m["starts_at"]} 〜 {m["ends_at"]} のメンテナンスにご協力いただきありがとうございました。\n'
         f'お詫びとして、全ユーザーの皆さまに {amount:,} トークンを付与しました。\n'
         '設定画面の「使用量」からご確認いただけます。\n\n[Qzero会社]',
         'info', 'all', 'all', cur_uid() or 'admin', n_now))
    conn.commit(); conn.close()
    alog('maintenance_compensate', str(mid), f'{amount} x {len(users)}人')
    return jsonify({'ok': True, 'users': len(users), 'amount': amount})


@qstart_core.route('/qstart/maintenance')
def maintenance_page():
    a, u = current_maintenance()
    return render_template('qstart_maintenance.html', m=a or u or {})


# ===== テスト環境 (/admin/qstart 配下) =====
def _test_guard():
    if qstart_role() != 'admin':
        return ('<div style="font-family:sans-serif;text-align:center;padding:60px;">'
                '<h1 style="font-size:22px;">403</h1>'
                '<p style="color:#666;">テスト環境は管理者のみアクセスできます。</p>'
                '<a href="/qstart" style="color:#b8860b;">Qstartに戻る</a></div>'), 403
    return None


@qstart_core.route('/admin/qstart')
def test_site():
    g = _test_guard()
    if g: return g
    return render_template('qstart.html', test_mode=True)


@qstart_core.route('/admin/qstart/chat/<chat_id>')
def test_chat(chat_id):
    g = _test_guard()
    if g: return g
    return render_template('qstart.html', test_mode=True)


@qstart_core.route('/admin/qstart/projects')
def test_projects():
    g = _test_guard()
    if g: return g
    return render_template('qstart_projects.html', need_login=False, test_mode=True)


@qstart_core.route('/admin/qstart/project/<pid>')
def test_project(pid):
    g = _test_guard()
    if g: return g
    return render_template('qstart_project.html', pid=pid, need_login=False, test_mode=True)


@qstart_core.route('/admin/qstart/status')
def test_status():
    g = _test_guard()
    if g: return g
    return render_template('qstart_status.html', test_mode=True)


@qstart_core.route('/admin/qstart/contact')
def test_contact():
    g = _test_guard()
    if g: return g
    return render_template('qstart_contact.html', test_mode=True)


# ========== エラー監視 ==========
def init_error_table():
    conn = db()
    conn.execute('''CREATE TABLE IF NOT EXISTS qstart_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT, path TEXT, method TEXT,
        message TEXT, trace TEXT,
        user_id TEXT, ip_hash TEXT,
        count INTEGER DEFAULT 1,
        first_at TEXT, last_at TEXT,
        fingerprint TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_err_fp ON qstart_errors(fingerprint)')
    conn.commit(); conn.close()

init_error_table()


def log_error(exc, kind='500'):
    import traceback as _tb
    try:
        tr = _tb.format_exc()[-3000:]
        msg = f'{type(exc).__name__}: {exc}'[:500]
        path = request.path if request else ''
        method = request.method if request else ''
        # 同じ場所の同じエラーはまとめる
        fp = hashlib.md5((msg[:200] + path).encode()).hexdigest()[:16]
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '') if request else ''
        ih = hashlib.sha256(ip.split(',')[0].strip().encode()).hexdigest()[:16]
        conn = db()
        r = conn.execute('SELECT id FROM qstart_errors WHERE fingerprint=?', (fp,)).fetchone()
        if r:
            conn.execute('UPDATE qstart_errors SET count=count+1, last_at=?, trace=? WHERE id=?',
                         (now(), tr, r['id']))
        else:
            conn.execute('''INSERT INTO qstart_errors
                (kind,path,method,message,trace,user_id,ip_hash,first_at,last_at,fingerprint)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (kind, path, method, msg, tr, cur_uid() or '', ih, now(), now(), fp))
        # 100件を超えたら古いものを消す
        conn.execute('''DELETE FROM qstart_errors WHERE id NOT IN
            (SELECT id FROM qstart_errors ORDER BY last_at DESC LIMIT 200)''')
        conn.commit(); conn.close()
    except Exception:
        pass


@qstart_core.route('/qstart/api/v1/admin/errors')
@require_admin
def admin_errors():
    conn = db()
    rows = conn.execute('''SELECT id,kind,path,method,message,user_id,count,first_at,last_at
        FROM qstart_errors ORDER BY last_at DESC LIMIT 100''').fetchall()
    n24 = conn.execute("""SELECT COALESCE(SUM(count),0) FROM qstart_errors
        WHERE last_at > datetime('now','-1 day','localtime')""").fetchone()[0]
    conn.close()
    return jsonify({'ok': True, 'errors': [dict(r) for r in rows], 'count_24h': n24})


@qstart_core.route('/qstart/api/v1/admin/errors/<int:eid>')
@require_admin
def admin_error_detail(eid):
    conn = db()
    r = conn.execute('SELECT * FROM qstart_errors WHERE id=?', (eid,)).fetchone()
    conn.close()
    return jsonify({'ok': bool(r), 'error': dict(r) if r else None})


@qstart_core.route('/qstart/api/v1/admin/errors', methods=['DELETE'])
@require_admin
def admin_errors_clear():
    conn = db()
    conn.execute('DELETE FROM qstart_errors')
    conn.commit(); conn.close()
    alog('errors_clear')
    return jsonify({'ok': True})


# ========== ヘルスチェック(稼働状況) ==========
@qstart_core.route('/qstart/health')
def health():
    import time as _t
    st = {'ok': True, 'time': now(), 'checks': {}}
    t0 = _t.time()
    try:
        conn = db()
        conn.execute('SELECT 1').fetchone()
        conn.close()
        st['checks']['database'] = {'ok': True, 'ms': round((_t.time()-t0)*1000, 1)}
    except Exception as e:
        st['ok'] = False
        st['checks']['database'] = {'ok': False, 'error': str(e)[:100]}
    try:
        conn = db()
        rows = conn.execute('SELECT model,enabled,maintenance FROM qstart_model_flags').fetchall()
        conn.close()
        st['checks']['models'] = {m['model']: ('maintenance' if m['maintenance']
                                  else ('on' if m['enabled'] else 'off')) for m in rows}
    except Exception:
        st['checks']['models'] = {}
    try:
        conn = db()
        n = conn.execute("""SELECT COALESCE(SUM(count),0) FROM qstart_errors
            WHERE last_at > datetime('now','-1 hour','localtime')""").fetchone()[0]
        conn.close()
        st['checks']['errors_1h'] = n
        if n > 50:
            st['ok'] = False
    except Exception:
        pass
    st['checks']['mail'] = bool(os.environ.get('RESEND_API_KEY'))
    st['checks']['turnstile'] = bool(os.environ.get('TURNSTILE_SECRET_KEY'))
    return jsonify(st), (200 if st['ok'] else 503)


# ========== パスワード再設定 ==========
@qstart_core.route('/qstart/api/v1/password/request', methods=['POST'])
def pw_request():
    import secrets as _sec, qstart_mail as qm
    d = request.get_json(silent=True) or {}
    email = (d.get('email') or '').strip()
    if not email or '@' not in email:
        return jsonify({'ok': False, 'error': 'メールアドレスを入力してください'}), 400
    conn = db()
    conn.execute("CREATE TABLE IF NOT EXISTS qstart_pw_reset (token TEXT PRIMARY KEY, user_id TEXT, expires_at REAL, used INTEGER DEFAULT 0)")
    u = conn.execute('SELECT user_id FROM qstart_users WHERE email=?', (email,)).fetchone()
    if u:
        tok = _sec.token_urlsafe(32)
        conn.execute('INSERT INTO qstart_pw_reset(token,user_id,expires_at) VALUES(?,?,?)',
                     (tok, u['user_id'], time.time() + 3600))
        conn.commit()
        link = 'https://yuto113.pythonanywhere.com/qstart/reset?t=' + tok
        html = ('<div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;'
          'padding:36px 32px;border:1px solid #ece9e2;font-family:sans-serif;">'
          '<div style="font-size:22px;font-weight:700;color:#14213d;">Q<span style="color:#b8860b;">start</span></div>'
          '<div style="font-size:11px;color:#a09a8a;margin-bottom:26px;">by Qzero会社</div>'
          '<div style="font-size:17px;font-weight:600;color:#14213d;margin-bottom:10px;">パスワードの再設定</div>'
          '<div style="font-size:13.5px;color:#4a4438;line-height:1.9;margin-bottom:20px;">'
          '以下のボタンから、新しいパスワードを設定してください。</div>'
          '<a href="' + link + '" style="display:inline-block;padding:12px 24px;background:#14213d;'
          'color:#fff;text-decoration:none;border-radius:9px;font-size:14px;">パスワードを再設定</a>'
          '<div style="font-size:12px;color:#8a8270;line-height:1.8;margin-top:22px;">'
          'このリンクは1時間有効です。<br>心当たりがない場合は、このメールを破棄してください。</div></div>')
        qm.send_mail(email, 'Qstart パスワードの再設定', html, kind='pw_reset')
    conn.close()
    return jsonify({'ok': True, 'message': 'メールを送信しました。届かない場合は迷惑メールをご確認ください。'})


@qstart_core.route('/qstart/api/v1/password/reset', methods=['POST'])
def pw_reset():
    d = request.get_json(silent=True) or {}
    tok = (d.get('token') or '').strip()
    pw = d.get('password') or ''
    if len(pw) < 6:
        return jsonify({'ok': False, 'error': 'パスワードは6文字以上にしてください'}), 400
    conn = db()
    try:
        r = conn.execute('SELECT user_id,expires_at,used FROM qstart_pw_reset WHERE token=?', (tok,)).fetchone()
    except Exception:
        conn.close(); return jsonify({'ok': False, 'error': 'リンクが無効です'}), 400
    if not r or r['used'] or r['expires_at'] < time.time():
        conn.close()
        return jsonify({'ok': False, 'error': 'リンクの有効期限が切れています'}), 400
    from qz_common import hash_password
    conn.execute('UPDATE qstart_users SET password_hash=? WHERE user_id=?',
                 (hash_password(pw), r['user_id']))
    conn.execute('UPDATE qstart_pw_reset SET used=1 WHERE token=?', (tok,))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@qstart_core.route('/qstart/reset')
def pw_reset_page():
    return render_template('qstart_reset.html', token=request.args.get('t', ''))


# ========== 起動時に必要な情報を1本で返す ==========
@qstart_core.route('/qstart/api/v1/bootstrap')
def bootstrap():
    """ページを開いたときに必要なものを全部まとめて返す。
    従来 /me /settings /models /announcements /status /features の6本を1本に。"""
    uid = cur_uid()
    conn = db()
    out = {'ok': True, 'guest': not uid}

    # --- ユーザー情報 ---
    if uid:
        u = conn.execute('SELECT user_id,nickname,email,lang,created_at FROM qstart_users WHERE user_id=?', (uid,)).fetchone()
        fl = conn.execute('SELECT role,status FROM qstart_user_flags WHERE user_id=?', (uid,)).fetchone()
        st = (fl['status'] if fl else 'active') or 'active'
        is_staff = bool(session.get('qstart_staff'))
        email = (u['email'] if u else '') or ''
        if email and '@' in email:
            nm, dm = email.split('@', 1)
            email = (nm[:2] + '***' if len(nm) > 2 else '***') + '@' + dm
        out['me'] = {
            'user_id': uid,
            'nickname': session.get('qstart_nick') or (u['nickname'] if u else uid),
            'email': email,
            'plan': 'スタッフ' if is_staff else 'Free',
            'role': qstart_role(uid),
            'status': st,
            'suspended': st != 'active',
            'is_staff': is_staff,
        }
        # --- 設定 ---
        s = conn.execute('SELECT * FROM qstart_settings WHERE user_id=?', (uid,)).fetchone()
        if not s:
            conn.execute('INSERT INTO qstart_settings(user_id,updated_at) VALUES(?,?)', (uid, now()))
            conn.commit()
            s = conn.execute('SELECT * FROM qstart_settings WHERE user_id=?', (uid,)).fetchone()
        out['settings'] = {k: s[k] for k in SETTING_KEYS}
        # --- 残高 ---
        q = conn.execute('SELECT window_bonus,monthly_bonus FROM qstart_user_quota WHERE user_id=?', (uid,)).fetchone()
        stk = conn.execute('SELECT tokens FROM qstart_user_stock WHERE user_id=?', (uid,)).fetchone()
        out['quota'] = {
            'window_bonus': (q['window_bonus'] if q else 0) or 0,
            'monthly_bonus': (q['monthly_bonus'] if q else 0) or 0,
            'stock': (stk['tokens'] if stk else 0) or 0,
        }
    else:
        out['me'] = {'role': 'guest', 'suspended': False}
        out['settings'] = {}
        out['quota'] = {'window_bonus': 0, 'monthly_bonus': 0, 'stock': 0}

    # --- モデル ---
    out['models'] = [dict(r) for r in conn.execute(
        'SELECT model,enabled,maintenance,note,min_role,family,version,display,is_latest,params FROM qstart_model_flags ORDER BY family, version DESC')]

    # --- お知らせ ---
    anns = conn.execute("""SELECT * FROM qstart_announcements
        WHERE active=1 AND (starts_at IS NULL OR starts_at<=?) AND (ends_at IS NULL OR ends_at>=?)
        ORDER BY id DESC LIMIT 30""", (now(), now())).fetchall()
    read = set()
    if uid:
        read = {r[0] for r in conn.execute(
            'SELECT ann_id FROM qstart_announce_reads WHERE user_id=?', (uid,))}
    items = [dict(r, unread=(r['id'] not in read)) for r in anns]
    out['announcements'] = items
    out['banner'] = [x for x in items if x['unread']] if uid else items[:1]

    # --- 最近のチャット ---
    if uid:
        out['chats'] = [dict(r) for r in conn.execute(
            """SELECT chat_id,title,project_id,updated_at FROM qstart_chats
               WHERE user_id=? ORDER BY updated_at DESC LIMIT 30""", (uid,))]
    else:
        out['chats'] = []

    # --- 機能フラグ ---
    keys = [r['key'] for r in conn.execute('SELECT key FROM qstart_feature_flags')]
    conn.close()
    out['features'] = {k: feature_for(k, uid) for k in keys}

    return jsonify(out)


# ========== 凍結・権限チェック ==========
def user_status(uid):
    if not uid: return 'active'
    conn = db()
    r = conn.execute('SELECT status FROM qstart_user_flags WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return (r['status'] if r else 'active') or 'active'

def is_suspended(uid):
    return user_status(uid) in ('suspended', 'banned')


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


# ========== モデルの取得元 ==========
def init_model_source():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS qstart_model_source (
        model TEXT PRIMARY KEY,
        weights_url TEXT, config_url TEXT,
        note TEXT, updated_at TEXT
    )""")
    conn.commit(); conn.close()

init_model_source()


def get_model_source(model):
    """モデルの重みの場所。URLでもローカルパスでも可"""
    conn = db()
    r = conn.execute('SELECT weights_url,config_url FROM qstart_model_source WHERE model=?',
                     (model,)).fetchone()
    conn.close()
    if r and r['weights_url']:
        return {'weights': r['weights_url'], 'config': r['config_url']}
    # 既定のローカルパス
    base = '/home/yuto113/quizshare-py'
    known = {'equi': 'equi1', 'zin': 'zin1'}
    d = known.get(model)
    if d and os.path.exists(f'{base}/{d}/weights.npz'):
        return {'weights': f'{base}/{d}/weights.npz', 'config': f'{base}/{d}/config.json'}
    return None


@qstart_core.route('/qstart/api/v1/admin/model-source', methods=['GET','POST'])
@require_admin
def admin_model_source():
    conn = db()
    if request.method == 'GET':
        rows = conn.execute('SELECT * FROM qstart_model_source').fetchall()
        conn.close()
        return jsonify({'ok': True, 'sources': [dict(r) for r in rows]})
    d = request.get_json(silent=True) or {}
    conn.execute("""INSERT INTO qstart_model_source(model,weights_url,config_url,note,updated_at)
        VALUES(?,?,?,?,?) ON CONFLICT(model) DO UPDATE SET
        weights_url=excluded.weights_url, config_url=excluded.config_url,
        note=excluded.note, updated_at=excluded.updated_at""",
        (d.get('model'), (d.get('weights_url') or '').strip(),
         (d.get('config_url') or '').strip(), d.get('note',''), now()))
    conn.commit(); conn.close()
    alog('model_source', d.get('model',''), d.get('weights_url','')[:120])
    return jsonify({'ok': True})


# ========== モデル一覧(誰でも読める) ==========
@qstart_core.route('/qstart/api/v1/models')
def public_models():
    conn = db()
    rows = conn.execute('SELECT model,enabled,maintenance,note,min_role,family,version,display,is_latest,params FROM qstart_model_flags ORDER BY family, version DESC').fetchall()
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
    try:
        s['errors_24h'] = conn.execute("""SELECT COALESCE(SUM(count),0) FROM qstart_errors
            WHERE last_at > datetime('now','-1 day','localtime')""").fetchone()[0]
    except Exception:
        s['errors_24h'] = 0
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
