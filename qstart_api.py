from flask import Blueprint, request, jsonify, render_template, session

# Equi 1 モデル読み込み（起動時に1回だけ）
_equi_model = None
def get_equi():
    global _equi_model
    if _equi_model is None:
        try:
            import sys
            sys.path.insert(0, '/home/yuto113/quizshare-py')
            from equi_inference import EquiInference
            _equi_model = EquiInference(
                '/home/yuto113/quizshare-py/equi1/weights.npz',
                '/home/yuto113/quizshare-py/equi1/config.json'
            )
            print('✅ Equi 1 読み込み完了!')
        except Exception as e:
            print(f'❌ Equi 1 読み込みエラー: {e}')
    return _equi_model
import sqlite3, secrets, os, time
from functools import wraps

qstart_api = Blueprint('qstart_api', __name__, template_folder='templates')
DB_PATH = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')

def init_qstart_api_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_api_keys (
        api_key TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        name TEXT DEFAULT 'default', created_at REAL DEFAULT (strftime('%s','now')),
        last_used REAL, is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS qstart_api_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
        source TEXT NOT NULL, model TEXT NOT NULL,
        tokens_used INTEGER DEFAULT 0, timestamp REAL DEFAULT (strftime('%s','now'))
    )''')
    conn.commit()
    conn.close()

init_qstart_api_db()

# ===== 使用量設定 =====
WINDOW_SECONDS = 3 * 3600
WINDOW_TOKENS = 4000
MONTHLY_TOKENS_LINKED = 1500000  # $5分(1$=30万)

def get_window_usage(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = time.time() - WINDOW_SECONDS
    c.execute("SELECT COALESCE(SUM(tokens_used),0) FROM qstart_api_usage WHERE user_id=? AND timestamp>=? AND source!='promo'", (uid, cutoff))
    used = c.fetchone()[0]
    conn.close()
    return used

def get_promo_bonus(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(tokens_used),0) FROM qstart_api_usage WHERE user_id=? AND source='promo'", (uid,))
    bonus = c.fetchone()[0]
    conn.close()
    return abs(bonus)

def get_monthly_usage(uid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(tokens_used),0) FROM qstart_api_usage WHERE user_id=? AND timestamp>=strftime('%s',date('now','start of month'))", (uid,))
    used = c.fetchone()[0]
    conn.close()
    return used

def user_is_linked(uid):
    """Qstartアカウントを持っているか(月間枠の対象)
    以前は社員ログインとの連携が必要だったが、
    Qstartアカウント自体が本人確認になるので簡素化した"""
    if session.get('qstart_staff'):
        return True
    u = uid or session.get('qstart_user')
    if not u or str(u).startswith('guest_'):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        r = conn.execute('SELECT 1 FROM qstart_users WHERE user_id=?', (u,)).fetchone()
        conn.close()
        return bool(r)
    except Exception:
        return False

def get_perm_bonus(uid):
    """持続性アリで付与された恒久ボーナス"""
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT window_bonus,monthly_bonus FROM qstart_user_quota WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    return ((r[0] if r else 0) or 0, (r[1] if r else 0) or 0)

def get_stock(uid):
    """持続性ナシの予備タンク残高"""
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT tokens FROM qstart_user_stock WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    return (r[0] if r else 0) or 0

def use_stock(uid, tokens):
    """予備タンクから引く。引けた分を返す"""
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT tokens FROM qstart_user_stock WHERE user_id=?", (uid,)).fetchone()
    have = (r[0] if r else 0) or 0
    use = min(have, tokens)
    if use > 0:
        conn.execute("UPDATE qstart_user_stock SET tokens=tokens-? WHERE user_id=?", (use, uid))
        conn.commit()
    conn.close()
    return use

def check_can_use(uid):
    """A方式: 通常枠 → 月間枠 → 予備タンク の順に使う"""
    perm_w, perm_m = get_perm_bonus(uid)
    w = get_window_usage(uid)
    w_limit = WINDOW_TOKENS + perm_w
    if w < w_limit:
        return True, 'window', w_limit - w
    if user_is_linked(uid):
        m = get_monthly_usage(uid)
        m_limit = MONTHLY_TOKENS_LINKED + perm_m
        if m < m_limit:
            return True, 'monthly', m_limit - m
    # 最後に予備タンク
    st = get_stock(uid)
    if st > 0:
        return True, 'stock', st
    return False, 'exhausted', 0

def record_usage(uid, source, model, tokens):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO qstart_api_usage(user_id,source,model,tokens_used) VALUES(?,?,?,?)", (uid,source,model,tokens))
    conn.commit()
    conn.close()

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('Authorization','').replace('Bearer ','')
        if not api_key:
            return jsonify({'error':'APIキーが必要です'}), 401
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id FROM qstart_api_keys WHERE api_key=? AND is_active=1", (api_key,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({'error':'無効なAPIキーです'}), 401
        c.execute("UPDATE qstart_api_keys SET last_used=strftime('%s','now') WHERE api_key=?", (api_key,))
        conn.commit()
        conn.close()
        uid = row[0]
        can, pool, remaining = check_can_use(uid)
        if not can:
            return jsonify({'error':'使用量の上限に達しました'}), 429
        request.qstart_api_user = uid
        return f(*args, **kwargs)
    return decorated

# ===== ページ =====
@qstart_api.route('/qstart/api')
def api_dashboard():
    return render_template('qstart_api.html')

# ===== ログイン: 既存の /api/qstart/login を使う =====
# HTMLから直接 /api/qstart/login を呼ぶのでここには不要！

# ===== ログアウト =====
@qstart_api.route('/qstart/api/v1/logout', methods=['POST'])
def api_logout():
    session.pop('qstart_user', None)
    session.pop('qstart_nick', None)
    session.pop('qstart_staff', None)
    return jsonify({'ok':True})

# ===== APIキー一覧 =====
@qstart_api.route('/qstart/api/v1/keys', methods=['GET'])
def list_api_keys():
    uid = session.get('qstart_user')
    if not uid: return jsonify({'error':'ログインが必要です'}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT api_key, name, created_at, last_used FROM qstart_api_keys WHERE user_id=? AND is_active=1 ORDER BY created_at DESC", (uid,))
    keys = []
    for r in c.fetchall():
        # キーは最初4文字+末尾4文字だけ表示（セキュリティ）
        k = r[0]
        masked = k[:8] + '...' + k[-4:]
        keys.append({'masked':masked,'full':k,'name':r[1],'created_at':r[2],'last_used':r[3]})
    conn.close()
    return jsonify({'ok':True,'keys':keys})

# ===== APIキー削除 =====
@qstart_api.route('/qstart/api/v1/keys/<key_name>', methods=['DELETE'])
def delete_api_key(key_name):
    uid = session.get('qstart_user')
    if not uid: return jsonify({'error':'ログインが必要です'}), 401
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE qstart_api_keys SET is_active=0 WHERE user_id=? AND name=?", (uid, key_name))
    conn.commit()
    conn.close()
    return jsonify({'ok':True})

# ===== APIキー発行 =====
@qstart_api.route('/qstart/api/v1/keys', methods=['POST'])
def create_api_key():
    uid = session.get('qstart_user')
    if not uid: return jsonify({'error':'ログインが必要です'}), 401
    data = request.get_json() or {}
    name = data.get('name','default')
    api_key = 'qsk_' + secrets.token_hex(24)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO qstart_api_keys(api_key,user_id,name) VALUES(?,?,?)", (api_key,uid,name))
    conn.commit()
    conn.close()
    return jsonify({'ok':True,'api_key':api_key,'name':name})

# ===== 使用量 =====
@qstart_api.route('/qstart/api/v1/usage')
def get_usage():
    uid = None
    api_key = request.headers.get('Authorization','').replace('Bearer ','')
    if api_key:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id FROM qstart_api_keys WHERE api_key=? AND is_active=1", (api_key,))
        row = c.fetchone()
        conn.close()
        if row: uid = row[0]
    if not uid:
        uid = session.get('qstart_user')
    if not uid: return jsonify({'error':'認証が必要です'}), 401
    w_used = get_window_usage(uid)
    m_used = get_monthly_usage(uid)
    linked = user_is_linked(uid)
    m_limit = MONTHLY_TOKENS_LINKED if linked else 0
    bonus = get_promo_bonus(uid)
    # 恒久ボーナス(持続性アリ) と 予備タンク(持続性ナシ)
    _cn = sqlite3.connect(DB_PATH)
    _q = _cn.execute("SELECT window_bonus,monthly_bonus FROM qstart_user_quota WHERE user_id=?", (uid,)).fetchone()
    _s = _cn.execute("SELECT tokens FROM qstart_user_stock WHERE user_id=?", (uid,)).fetchone()
    _cn.close()
    perm_w = (_q[0] if _q else 0) or 0
    perm_m = (_q[1] if _q else 0) or 0
    stock = (_s[0] if _s else 0) or 0
    w_limit = WINDOW_TOKENS + bonus + perm_w
    return jsonify({
        'window':{'used':w_used,'limit':w_limit,'remaining':max(0,w_limit-w_used),
                  'bonus':bonus,'permanent':perm_w},
        'monthly':{'used':m_used,'limit':m_limit+perm_m,
                   'remaining':max(0,m_limit+perm_m-m_used),'permanent':perm_m},
        'stock':stock,
        'is_linked':linked,
        'is_staff':bool(session.get('qstart_staff')),
        'active_pool':'window' if w_used < w_limit else ('monthly' if linked else 'exhausted')
    })

# ===== チャット =====
@qstart_api.route('/qstart/api/v1/chat', methods=['POST'])
@require_api_key
def api_chat():
    data = request.get_json()
    msg = data.get('message','')
    model = data.get('model','equi')
    # 別名を正規化(equi-1 → equi など)
    _alias = {'equi-1':'equi','pure-1':'pure','zin-1':'zin',
              'apex-1':'apex','apex-2':'apex','apex2':'apex','apex-3':'apex3'}
    model = _alias.get(model, model)
    # APIで使えるモデルをDBから取得
    _c = sqlite3.connect(DB_PATH)
    available = [r[0] for r in _c.execute(
        "SELECT model FROM qstart_model_scope WHERE scope='api' AND enabled=1 AND maintenance=0")]
    _c.close()
    if model not in available:
        return jsonify({'error':f'モデル"{model}"は利用できません','available':available}), 400
    equi = get_equi()
    if equi is None:
        return jsonify({'error':'モデルの読み込みに失敗しました'}), 500
    try:
        response_text = equi.chat(msg)
    except Exception as e:
        response_text = f'エラー: {str(e)}'
    tokens = len(msg) + len(response_text)
    record_usage(request.qstart_api_user, 'api', model, tokens)
    return jsonify({'model':model,'message':response_text,'tokens_used':tokens})

# ===== 優待コード =====

# 管理者: コード発行
@qstart_api.route('/qstart/api/v1/promo/create', methods=['POST'])
def promo_create():
    # 管理者チェック（Yutoのみ）
    uid = session.get('qstart_user')
    if uid != 'yuto':
        return jsonify({'error': '管理者のみ利用可能です'}), 403

    data = request.get_json() or {}
    import secrets as _sec

    code = data.get('code', '').strip()
    if not code:
        code = 'QS-' + _sec.token_hex(4).upper()

    promo_type = data.get('type', 'token_add')  # token_add, monthly_add, feature
    value = int(data.get('value', 10000))
    description = data.get('description', '')
    max_uses = int(data.get('max_uses', 1))  # 1, 5, 10, 50, -1(無制限)
    expires_at = data.get('expires_at')  # YYYY-MM-DD or null

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO promo_codes(code, type, value, description, max_uses, expires_at, created_by) VALUES(?,?,?,?,?,?,?)",
            (code, promo_type, value, description, max_uses, expires_at, uid)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'error': f'コード作成エラー: {str(e)}'}), 400
    conn.close()

    return jsonify({
        'ok': True, 'code': code, 'type': promo_type,
        'value': value, 'max_uses': max_uses, 'description': description
    })

# 管理者: コード一覧
@qstart_api.route('/qstart/api/v1/promo/list')
def promo_list():
    uid = session.get('qstart_user')
    if uid != 'yuto':
        return jsonify({'error': '管理者のみ'}), 403

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT code, type, value, description, max_uses, used_count, expires_at, active, created_at FROM promo_codes ORDER BY created_at DESC")
    codes = []
    for r in c.fetchall():
        codes.append({
            'code': r[0], 'type': r[1], 'value': r[2], 'description': r[3],
            'max_uses': r[4], 'used_count': r[5], 'expires_at': r[6],
            'active': bool(r[7]), 'created_at': r[8]
        })
    conn.close()
    return jsonify({'ok': True, 'codes': codes})

# 管理者: コード無効化
@qstart_api.route('/qstart/api/v1/promo/disable', methods=['POST'])
def promo_disable():
    uid = session.get('qstart_user')
    if uid != 'yuto':
        return jsonify({'error': '管理者のみ'}), 403

    data = request.get_json() or {}
    code = data.get('code', '')
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE promo_codes SET active=0 WHERE code=?", (code,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ユーザー: コード使用
@qstart_api.route('/qstart/api/v1/promo/use', methods=['POST'])
def promo_use():
    uid = session.get('qstart_user')
    if not uid:
        return jsonify({'error': 'ログインが必要です'}), 401

    data = request.get_json() or {}
    code = data.get('code', '').strip().upper()
    if not code:
        return jsonify({'error': 'コードを入力してください'}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # コード確認
    c.execute("SELECT type, value, description, max_uses, used_count, expires_at, active, COALESCE(persistent,0) FROM promo_codes WHERE code=?", (code,))
    row = c.fetchone()

    if not row:
        conn.close()
        return jsonify({'error': 'コードが見つかりません'}), 404

    promo_type, value, desc, max_uses, used_count, expires_at, active, persistent = row

    if not active:
        conn.close()
        return jsonify({'error': 'このコードは無効になっています'}), 400

    if max_uses > 0 and used_count >= max_uses:
        conn.close()
        return jsonify({'error': 'このコードは使用上限に達しています'}), 400

    # 期限チェック
    if expires_at:
        import time as _t
        from datetime import datetime as _dt
        try:
            exp = _dt.strptime(expires_at, '%Y-%m-%d').timestamp()
            if _t.time() > exp:
                conn.close()
                return jsonify({'error': 'このコードは有効期限切れです'}), 400
        except:
            pass

    # 同じユーザーが同じコードを使ったか確認
    c.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, uid))
    if c.fetchone():
        conn.close()
        return jsonify({'error': 'このコードは既に使用済みです'}), 400

    # 優待を適用
    import time as _tt
    _n = _tt.strftime('%Y-%m-%d %H:%M:%S')
    result_msg = ''
    if promo_type in ('token_add', 'monthly_add'):
        if persistent:
            # 持続性アリ: 恒久的に枠を増やす(リセットのたびに復活)
            conn.execute("INSERT OR IGNORE INTO qstart_user_quota(user_id,updated_at) VALUES(?,?)", (uid, _n))
            if promo_type == 'token_add':
                conn.execute("UPDATE qstart_user_quota SET window_bonus=window_bonus+?, updated_at=? WHERE user_id=?", (value, _n, uid))
                result_msg = f'3時間枠の上限が {value:,} トークン増えました！これは毎回のリセットで復活します。'
            else:
                conn.execute("UPDATE qstart_user_quota SET monthly_bonus=monthly_bonus+?, updated_at=? WHERE user_id=?", (value, _n, uid))
                result_msg = f'月間枠の上限が {value:,} トークン増えました！毎月復活します。'
        else:
            # 持続性ナシ: 予備タンクに入る(通常枠を使い切ってから消費・復活しない)
            conn.execute("INSERT OR IGNORE INTO qstart_user_stock(user_id,tokens,updated_at) VALUES(?,0,?)", (uid, _n))
            conn.execute("UPDATE qstart_user_stock SET tokens=tokens+?, updated_at=? WHERE user_id=?", (value, _n, uid))
            result_msg = f'予備タンクに {value:,} トークン追加されました！通常枠を使い切ったあとに使われます。'
    elif promo_type == 'feature':
        result_msg = f'特別機能が有効になりました：{desc}'
    else:
        result_msg = f'優待が適用されました：{desc}'

    # 使用記録
    conn.execute("INSERT INTO promo_uses(code, user_id) VALUES(?,?)", (code, uid))
    conn.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
    conn.commit()
    conn.close()

    return jsonify({'ok': True, 'message': result_msg, 'type': promo_type, 'value': value})

# ===== モデル一覧 =====
# ★ /qstart/api/v1/models は qstart_core.py に一本化した(DBから取得)
#   ここでは API 専用の一覧を別パスで提供する
@qstart_api.route('/qstart/api/v1/models/api')
def list_models_api():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT f.model, f.display, f.note, f.params,
        s.enabled, s.maintenance FROM qstart_model_flags f
        JOIN qstart_model_scope s ON s.model=f.model AND s.scope='api'
        ORDER BY f.family, f.version DESC""").fetchall()
    conn.close()
    return jsonify({'models': [{
        'id': r['model'],
        'name': 'Qstart ' + (r['display'] or r['model']),
        'status': ('maintenance' if r['maintenance'] else ('live' if r['enabled'] else 'coming')),
        'params': r['params'] or '',
        'description': r['note'] or ''} for r in rows]})
