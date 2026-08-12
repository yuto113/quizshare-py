# -*- coding: utf-8 -*-
"""Qstart 登録管理 — Turnstile + 招待コード"""
import os, json, time, secrets, sqlite3, urllib.request, urllib.parse
from flask import session, request

DB_PATH = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
TURNSTILE_VERIFY = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def _load_env():
    p = os.path.expanduser('~/.env')
    if os.path.exists(p):
        try:
            for line in open(p, encoding='utf-8'):
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass
_load_env()


def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')

def today():
    return time.strftime('%Y-%m-%d')


# ===== 設定 =====
def get_config():
    conn = db()
    r = conn.execute('SELECT * FROM qstart_signup_config WHERE id=1').fetchone()
    conn.close()
    if not r:
        return {'mode':'invite','require_turnstile':1,'require_email':0,'daily_limit':100}
    return dict(r)

def set_config(**kw):
    allowed = ['mode','require_turnstile','require_email','daily_limit']
    sets, vals = [], []
    for k in allowed:
        if k in kw:
            sets.append(f'{k}=?'); vals.append(kw[k])
    if not sets: return False
    vals.append(now())
    conn = db()
    conn.execute(f'UPDATE qstart_signup_config SET {",".join(sets)}, updated_at=? WHERE id=1', vals)
    conn.commit(); conn.close()
    return True


# ===== Turnstile =====
def turnstile_site_key():
    return os.environ.get('TURNSTILE_SITE_KEY', '')

def verify_turnstile(token, ip=''):
    """人間確認。(成功か, 理由) を返す"""
    secret = os.environ.get('TURNSTILE_SECRET_KEY', '')
    if not secret:
        return False, 'no_secret'
    if not token:
        return False, 'no_token'
    data = urllib.parse.urlencode({
        'secret': secret, 'response': token, 'remoteip': ip
    }).encode()
    req = urllib.request.Request(TURNSTILE_VERIFY, data=data, method='POST', headers={
        'User-Agent': 'Qstart/1.5', 'Content-Type': 'application/x-www-form-urlencoded'
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            res = json.loads(r.read().decode())
        if res.get('success'):
            return True, 'ok'
        return False, ','.join(res.get('error-codes', ['failed']))
    except Exception as e:
        return False, f'{type(e).__name__}'


# ===== 招待コード =====
def new_invite(note='', max_uses=1, expires_at=None, by='admin', code=None,
               min_age=13, grant_role=None, bonus_window=0, bonus_monthly=0, bonus_stock=0):
    if not code:
        code = 'QS-' + secrets.token_hex(3).upper()
    conn = db()
    try:
        conn.execute("""INSERT INTO qstart_invites
            (code,note,max_uses,expires_at,created_by,created_at,
             min_age,grant_role,bonus_window,bonus_monthly,bonus_stock)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (code, note, max_uses, expires_at, by, now(),
             int(min_age), grant_role or None,
             int(bonus_window), int(bonus_monthly), int(bonus_stock)))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close(); return None
    conn.close()
    return code


def get_invite(code):
    if not code: return None
    conn = db()
    r = conn.execute('SELECT * FROM qstart_invites WHERE code=?', (code.strip().upper(),)).fetchone()
    conn.close()
    return dict(r) if r else None


def calc_age(birthday):
    """YYYY-MM-DD から満年齢"""
    if not birthday: return None
    try:
        y, m, d = [int(x) for x in birthday.split('-')]
    except Exception:
        return None
    t = time.localtime()
    age = t.tm_year - y
    if (t.tm_mon, t.tm_mday) < (m, d):
        age -= 1
    return age


def check_age(birthday, invite_code=None):
    """年齢チェック。招待コードがあれば下限を緩和できる"""
    cfg = get_config()
    limit = cfg.get('min_age', 13) or 13
    inv = get_invite(invite_code) if invite_code else None
    if inv and inv.get('min_age') is not None:
        limit = min(limit, int(inv['min_age']))
    age = calc_age(birthday)
    if age is None:
        return False, '生年月日を正しく入力してください。', limit
    if age < 0 or age > 120:
        return False, '生年月日を確認してください。', limit
    if age < limit:
        if limit > 0:
            return False, (f'{limit}歳未満の方はご利用いただけません。'
                           '保護者の方の同意がある場合は、招待コードをお使いください。'), limit
        return False, 'ご利用いただけません。', limit
    return True, '', limit


def apply_invite_bonus(code, user_id):
    """招待コードの特典を付与"""
    inv = get_invite(code)
    if not inv: return
    conn = db()
    if inv.get('grant_role'):
        conn.execute("""INSERT INTO qstart_user_flags(user_id,role,updated_by,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            role=excluded.role, updated_at=excluded.updated_at""",
            (user_id, inv['grant_role'], 'invite:' + code, now()))
    bw, bm = int(inv.get('bonus_window') or 0), int(inv.get('bonus_monthly') or 0)
    if bw or bm:
        conn.execute('INSERT OR IGNORE INTO qstart_user_quota(user_id,updated_at) VALUES(?,?)',
                     (user_id, now()))
        conn.execute("""UPDATE qstart_user_quota SET window_bonus=window_bonus+?,
                        monthly_bonus=monthly_bonus+?, granted_by=?, updated_at=? WHERE user_id=?""",
                     (bw, bm, 'invite:' + code, now(), user_id))
    bs = int(inv.get('bonus_stock') or 0)
    if bs:
        conn.execute('INSERT OR IGNORE INTO qstart_user_stock(user_id,tokens,updated_at) VALUES(?,0,?)',
                     (user_id, now()))
        conn.execute('UPDATE qstart_user_stock SET tokens=tokens+?, updated_at=? WHERE user_id=?',
                     (bs, now(), user_id))
    conn.commit(); conn.close()

def check_invite(code):
    """(有効か, 理由) を返す"""
    if not code:
        return False, 'required'
    conn = db()
    r = conn.execute('SELECT * FROM qstart_invites WHERE code=?', (code.strip().upper(),)).fetchone()
    conn.close()
    if not r:
        return False, 'not_found'
    if not r['active']:
        return False, 'disabled'
    if r['max_uses'] > 0 and r['used_count'] >= r['max_uses']:
        return False, 'used_up'
    if r['expires_at'] and r['expires_at'] < today():
        return False, 'expired'
    return True, 'ok'

def use_invite(code, user_id):
    code = (code or '').strip().upper()
    conn = db()
    conn.execute('UPDATE qstart_invites SET used_count=used_count+1 WHERE code=?', (code,))
    conn.execute('INSERT INTO qstart_invite_uses(code,user_id,used_at) VALUES(?,?,?)',
                 (code, user_id, now()))
    conn.commit(); conn.close()

def list_invites():
    conn = db()
    rows = conn.execute('SELECT * FROM qstart_invites ORDER BY created_at DESC LIMIT 200').fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ===== 登録できるかの総合判定 =====
def can_signup(turnstile_token=None, invite_code=None, ip=''):
    """(可否, エラーコード, メッセージ)"""
    cfg = get_config()

    if cfg['mode'] == 'closed':
        return False, 'closed', '現在、新規登録の受付を停止しています。'

    # 1日の上限
    import qstart_mail as qm
    if qm.signups_today() >= (cfg['daily_limit'] or 100):
        return False, 'busy', '現在、アカウントの作成が込み合っています。後日の登録をお願いします。'

    # 人間確認
    if cfg['require_turnstile']:
        ok, why = verify_turnstile(turnstile_token, ip)
        if not ok:
            if why == 'no_secret':
                return False, 'config', 'システム設定に問題があります。管理者にお問い合わせください。'
            return False, 'turnstile', '人間であることの確認に失敗しました。もう一度お試しください。'

    # 招待コード
    MSG = {
        'required':  '招待コードを入力してください。',
        'not_found': '招待コードが見つかりません。',
        'disabled':  'この招待コードは無効になっています。',
        'used_up':   'この招待コードは使用回数の上限に達しました。',
        'expired':   'この招待コードは有効期限が切れています。',
    }
    if cfg['mode'] == 'invite':
        # 招待制: コード必須
        ok, why = check_invite(invite_code)
        if not ok:
            return False, 'invite', MSG.get(why, '招待コードが正しくありません。')
    elif invite_code:
        # 全開放でもコードを入れたなら検証する(特典・年齢緩和のため)
        ok, why = check_invite(invite_code)
        if not ok:
            return False, 'invite', MSG.get(why, '招待コードが正しくありません。')

    return True, '', ''


def public_config():
    """フロントに渡す情報(秘密は含めない)"""
    cfg = get_config()
    import qstart_mail as qm
    left = max(0, (cfg['daily_limit'] or 100) - qm.signups_today())
    return {
        'mode': cfg['mode'],
        'min_age': cfg.get('min_age', 13) or 13,
        'invite_optional': cfg['mode'] == 'open',
        'require_turnstile': bool(cfg['require_turnstile']),
        'require_email': bool(cfg['require_email']),
        'site_key': turnstile_site_key() if cfg['require_turnstile'] else '',
        'slots_left': left,
        'open': cfg['mode'] != 'closed' and left > 0,
    }
