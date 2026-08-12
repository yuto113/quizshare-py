# -*- coding: utf-8 -*-
"""Qstart メール送信 — Resend API"""
import os, json, time, hashlib, urllib.request, sqlite3

DB_PATH = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
RESEND_URL = 'https://api.resend.com/emails'
FROM_ADDR = os.environ.get('QSTART_MAIL_FROM', 'Qstart <onboarding@resend.dev>')

DAILY_MAIL_LIMIT = 95      # Resendの1日100通に対して余裕を持たせる
DAILY_SIGNUP_LIMIT = 100   # 1日のアカウント作成上限


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

def _hash(s):
    return hashlib.sha256((s or '').encode()).hexdigest()[:16]


# ===== 上限チェック =====
def mail_sent_today():
    conn = db()
    n = conn.execute("""SELECT COUNT(*) FROM qstart_mail_log
        WHERE status='sent' AND date(created_at)=date('now','localtime')""").fetchone()[0]
    conn.close()
    return n

def signups_today():
    conn = db()
    r = conn.execute('SELECT count FROM qstart_signup_quota WHERE date=?', (today(),)).fetchone()
    conn.close()
    return (r['count'] if r else 0) or 0

def bump_signup():
    conn = db()
    conn.execute('INSERT OR IGNORE INTO qstart_signup_quota(date,count,updated_at) VALUES(?,0,?)',
                 (today(), now()))
    conn.execute('UPDATE qstart_signup_quota SET count=count+1, updated_at=? WHERE date=?',
                 (now(), today()))
    conn.commit(); conn.close()

def get_limit(key, default):
    conn = db()
    r = conn.execute('SELECT rollout_pct FROM qstart_feature_flags WHERE key=?', (key,)).fetchone()
    conn.close()
    return int(r['rollout_pct']) if (r and r['rollout_pct']) else default

def signup_open():
    conn = db()
    r = conn.execute("SELECT enabled FROM qstart_feature_flags WHERE key='signup_open'").fetchone()
    conn.close()
    return bool(r and r['enabled'])

def can_signup():
    """(可否, 理由) を返す"""
    if not signup_open():
        return False, 'closed'
    if signups_today() >= DAILY_SIGNUP_LIMIT:
        return False, 'busy'
    if mail_sent_today() >= DAILY_MAIL_LIMIT:
        return False, 'busy'
    return True, ''


# ===== 送信ログ =====
def log_mail(to, kind, status, provider='resend', error=''):
    conn = db()
    conn.execute("""INSERT INTO qstart_mail_log(to_hash,kind,status,provider,error,created_at)
                    VALUES(?,?,?,?,?,?)""",
                 (_hash(to), kind, status, provider, (error or '')[:300], now()))
    conn.commit(); conn.close()


# ===== 送信本体 =====
def send_mail(to, subject, html, kind='other'):
    key = os.environ.get('RESEND_API_KEY', '')
    if not key:
        log_mail(to, kind, 'failed', 'none', 'RESEND_API_KEY未設定')
        return False, 'no_api_key'

    if mail_sent_today() >= DAILY_MAIL_LIMIT:
        log_mail(to, kind, 'skipped', 'resend', 'daily_limit')
        return False, 'daily_limit'

    payload = json.dumps({
        'from': FROM_ADDR, 'to': [to], 'subject': subject, 'html': html
    }).encode('utf-8')

    req = urllib.request.Request(RESEND_URL, data=payload, method='POST', headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
        'User-Agent': 'Qstart/1.3 (+https://yuto113.pythonanywhere.com)',
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode())
        if body.get('id'):
            log_mail(to, kind, 'sent')
            return True, body['id']
        log_mail(to, kind, 'failed', 'resend', str(body)[:200])
        return False, 'no_id'
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode()[:300]
        except Exception:
            err = str(e)
        log_mail(to, kind, 'failed', 'resend', f'HTTP {e.code}: {err}')
        return False, f'http_{e.code}'
    except Exception as e:
        log_mail(to, kind, 'failed', 'resend', f'{type(e).__name__}: {e}')
        return False, 'error'


# ===== 認証コードメール =====
def send_verify_code(to, code, lang='ja'):
    T = {
        'ja': {
            'sub': 'Qstart 認証コード',
            'hi': 'Qstartへようこそ',
            'lead': 'アカウント登録を完了するには、次の認証コードを入力してください。',
            'exp': 'このコードは30分間有効です。',
            'no': 'このメールに心当たりがない場合は、破棄してください。',
        },
        'en': {
            'sub': 'Your Qstart verification code',
            'hi': 'Welcome to Qstart',
            'lead': 'Enter this code to finish creating your account.',
            'exp': 'This code expires in 30 minutes.',
            'no': "If you didn't request this, you can ignore this email.",
        },
    }
    t = T.get(lang, T['ja'])
    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#faf9f6;
  font-family:-apple-system,'Hiragino Sans','Noto Sans JP',sans-serif;">
<div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;
  padding:36px 32px;border:1px solid #ece9e2;">
  <div style="font-size:22px;font-weight:700;color:#14213d;margin-bottom:6px;">
    Q<span style="color:#b8860b;">start</span></div>
  <div style="font-size:11px;color:#a09a8a;margin-bottom:26px;">by Qzero会社</div>

  <div style="font-size:17px;font-weight:600;color:#14213d;margin-bottom:10px;">{t['hi']}</div>
  <div style="font-size:13.5px;color:#4a4438;line-height:1.9;margin-bottom:24px;">{t['lead']}</div>

  <div style="background:#faf9f5;border:1px solid #ece9e2;border-radius:12px;
    padding:22px;text-align:center;margin-bottom:20px;">
    <div style="font-size:34px;font-weight:700;letter-spacing:9px;
      color:#14213d;font-family:monospace;">{code}</div>
  </div>

  <div style="font-size:12px;color:#8a8270;line-height:1.8;">{t['exp']}<br>{t['no']}</div>

  <div style="border-top:1px solid #f0ede5;margin-top:28px;padding-top:16px;
    font-size:11px;color:#b8b09e;line-height:1.7;">
    Qstart — ゼロから作られたAI<br>
    このメールは送信専用です。
  </div>
</div>
</body></html>"""
    return send_mail(to, t['sub'], html, kind='verify')
