# -*- coding: utf-8 -*-
"""統合管理センター /admin
   /staff/* /setting/* /qstart/admin をここに集約する。
   中身のテンプレートは templates/admin/ に置く（断片HTML）。
"""
from flask import Blueprint, render_template, session, jsonify, redirect

bp = Blueprint('adminc', __name__)


def _role():
    """admin / staff / None。app.py の admin_center_role() と同じ判定。"""
    from app import admin_center_role
    return admin_center_role()


# base.html を外したので、そこにあった共通関数をここで補う
TOAST_SHIM = """<style>
#qzt{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:9999;
  display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none}
#qzt div{background:#14213d;color:#fff;padding:10px 18px;border-radius:9px;font-size:13px;
  box-shadow:0 4px 18px rgba(0,0,0,.25);animation:qzt-in .22s;max-width:80vw}
#qzt div.ng{background:#c0453b}
#qzt div.ok{background:#2a7d6f}
@keyframes qzt-in{from{opacity:0;transform:translateY(8px)}}
</style>
<div id="qzt"></div>
<script>
window.toast = function(msg, type){
  var w = document.getElementById('qzt');
  if (!w){ console.log(msg); return; }
  var d = document.createElement('div');
  if (type === 'error' || type === 'ng') d.className = 'ng';
  else if (type === 'success' || type === 'ok') d.className = 'ok';
  d.textContent = String(msg == null ? '' : msg);
  w.appendChild(d);
  setTimeout(function(){ d.remove(); }, 2600);
};
window.showToast = window.toast;
</script>"""


# key: (テンプレート, 表示名, 管理者専用か)
PAGES = {
    'errors': ('admin/errors.html', 'エラー監視', True),
    'dashboard': ('admin/dashboard.html', '統計', True),
    'hr': ('admin/hr.html', '人事', True),
    'moderation': ('admin/moderation.html', 'モデレーション', True),
    'patterns': ('admin/patterns.html', '会話パターン', True),
    'tasks': ('admin/tasks.html', 'タスク', False),
    'calendar': ('admin/calendar.html', 'カレンダー', False),
    'files': ('admin/files.html', 'ファイル', False),
    'handbook': ('admin/handbook.html', 'ハンドブック', False),
    'profile': ('admin/profile.html', 'プロフィール', False),
    'board': ('admin/board.html', '掲示板', False),
}


@bp.route('/admin/staff/<key>')
def admin_staff_page(key):
    role = _role()
    if not role:
        return redirect('/admin/login')
    p = PAGES.get(key)
    if not p:
        return '<div style="padding:40px;text-align:center;color:#888;">準備中です</div>', 404
    # ページ管理の設定を優先する。設定がなければ PAGES の既定にしたがう。
    if is_owner():
        vis = True                     # オーナーはすべてのページを見られる
    else:
        vis = page_visible(key)
    if vis is False:
        return '<div style="padding:40px;text-align:center;color:#888;">' \
               'このページは公開されていません</div>', 403
    if vis is None and p[2] and role != 'admin':
        return '<div style="padding:40px;text-align:center;color:#888;">管理者のみ</div>', 403
    html = render_template(p[0], role=role, page_title=p[1],
                           staff_id=session.get('staff_id'),
                           staff_name=session.get('staff_name') or session.get('staff_id'))
    return TOAST_SHIM + html


# ====================================================================
# Qzero公安部 / 警備局警備企画課 / 管理ページ管理
# ====================================================================
import sqlite3, os, json
from flask import request

OWNER_ID = 'yuto'          # ★ここだけは誰にも変更できない


def _db():
    c = sqlite3.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    c.row_factory = sqlite3.Row
    return c


def is_owner():
    return session.get('staff_id') == OWNER_ID


def kouan_rank(division):
    """その人の division 内での階級。所属してなければ None"""
    sid = session.get('staff_id')
    if not sid:
        return None
    if sid == OWNER_ID:
        return 'ura_rijikan' if division == 'keibi' else 'chief'
    c = _db()
    r = c.execute('SELECT rank FROM qz_kouan_member WHERE staff_id=? AND division=?',
                  (sid, division)).fetchone()
    c.close()
    return r['rank'] if r else None


def audit(action, target='', detail=''):
    """公安の操作を記録する。対象者本人もこれを見られる。"""
    try:
        c = _db()
        c.execute('INSERT INTO qz_kouan_audit(actor_id,action,target,detail) VALUES(?,?,?,?)',
                  (session.get('staff_id', '?'), action, target, detail[:500]))
        c.commit(); c.close()
    except Exception:
        pass


def page_visible(key):
    """管理ページ管理の設定にしたがって、このページを見せてよいか"""
    if is_owner():
        return True
    c = _db()
    r = c.execute('SELECT visible_to, allow_ids FROM qz_admin_page_acl WHERE page_key=?',
                  (key,)).fetchone()
    c.close()
    if not r:
        return None                      # 設定なし → 呼び出し元の既定にまかせる
    v = r['visible_to']
    if v == 'owner':
        return False                     # オーナー以外は絶対に見せない
    if v == 'admin':
        return _role() == 'admin'
    if v == 'staff':
        return _role() in ('admin', 'staff')
    if v == 'custom':
        ids = [x.strip() for x in (r['allow_ids'] or '').split(',') if x.strip()]
        return session.get('staff_id') in ids
    return False


# ---------- ページ ----------
@bp.route('/admin/kouan')
def admin_kouan_page():
    if not _role():
        return redirect('/admin/login')
    if page_visible('kouan') is False:
        return '<div style="padding:40px;text-align:center;color:#888;">権限がありません</div>', 403
    if not (kouan_rank('kouan') or kouan_rank('keibi')):
        return '<div style="padding:40px;text-align:center;color:#888;">公安部の所属者のみ</div>', 403
    return TOAST_SHIM + render_template('admin/kouan.html',
        rank_kouan=kouan_rank('kouan'), rank_keibi=kouan_rank('keibi'),
        is_owner=is_owner(), me=session.get('staff_id'))


@bp.route('/admin/pages')
def admin_pages_page():
    if not is_owner():
        return '<div style="padding:40px;text-align:center;color:#888;">管理者のみ</div>', 403
    return TOAST_SHIM + render_template('admin/pages.html', me=session.get('staff_id'))


# ---------- API: 公安部 ----------
@bp.route('/api/admin/kouan/overview')
def api_kouan_overview():
    rk, rb = kouan_rank('kouan'), kouan_rank('keibi')
    if not (rk or rb):
        return jsonify(ok=False, error='公安部の所属者のみ'), 403
    c = _db()
    members = [dict(r) for r in c.execute(
        'SELECT staff_id, division, rank, assigned_at, secret FROM qz_kouan_member '
        'ORDER BY division, rank DESC').fetchall()]
    members = hide_secret(members)
    reports = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_report ORDER BY id DESC LIMIT 50').fetchall()]
    agents = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_agent ORDER BY id DESC LIMIT 50').fetchall()]
    if rb is None:                       # 警備でなければ承認待ちは見えない
        agents = [a for a in agents if a['status'] == 'approved']
    audits = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_audit ORDER BY id DESC LIMIT 50').fetchall()]
    staff = [dict(r) for r in c.execute(
        "SELECT staff_id, name FROM qz_staff WHERE status='active'").fetchall()]
    c.close()
    return jsonify(ok=True, members=members, reports=reports, agents=agents,
                   audits=audits, staff=staff,
                   rank_kouan=rk, rank_keibi=rb, is_owner=is_owner())


@bp.route('/api/admin/kouan/report', methods=['POST'])
def api_kouan_report_new():
    if not _role():
        return jsonify(ok=False, error='ログインが必要です'), 403
    d = request.get_json(silent=True) or {}
    body = (d.get('body') or '').strip()
    if not body:
        return jsonify(ok=False, error='内容を書いてください'), 400
    c = _db()
    c.execute('INSERT INTO qz_kouan_report(reporter_id,target,category,body) VALUES(?,?,?,?)',
              (session.get('staff_id'), (d.get('target') or '').strip(),
               d.get('category') or 'other', body[:2000]))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/kouan/report/status', methods=['POST'])
def api_kouan_report_status():
    if not kouan_rank('kouan'):
        return jsonify(ok=False, error='公安部のみ'), 403
    d = request.get_json(silent=True) or {}
    st = d.get('status')
    if st not in ('open', 'working', 'closed'):
        return jsonify(ok=False, error='状態が不正です'), 400
    c = _db()
    c.execute("UPDATE qz_kouan_report SET status=?, handler_id=?, "
              "handled_at=datetime('now','localtime'), note=? WHERE id=?",
              (st, session.get('staff_id'), (d.get('note') or '')[:500], d.get('id')))
    c.commit(); c.close()
    audit('report_' + st, str(d.get('id')), d.get('note') or '')
    return jsonify(ok=True)


# ---------- API: 協力者（公安が申請 → 警備が許可） ----------
@bp.route('/api/admin/kouan/agent/request', methods=['POST'])
def api_agent_request():
    if not kouan_rank('kouan'):
        return jsonify(ok=False, error='公安部のみが申請できます'), 403
    d = request.get_json(silent=True) or {}
    cn = (d.get('code_name') or '').strip()
    if not cn:
        return jsonify(ok=False, error='コードネームが必要です'), 400
    c = _db()
    c.execute('INSERT INTO qz_kouan_agent(code_name,real_id,requested_by,note) VALUES(?,?,?,?)',
              (cn[:60], (d.get('real_id') or '').strip(),
               session.get('staff_id'), (d.get('note') or '')[:500]))
    c.commit(); c.close()
    audit('agent_request', cn)
    return jsonify(ok=True)


@bp.route('/api/admin/kouan/agent/decide', methods=['POST'])
def api_agent_decide():
    if kouan_rank('keibi') != 'ura_rijikan':
        return jsonify(ok=False, error='裏理事官のみが許可できます'), 403
    d = request.get_json(silent=True) or {}
    st = 'approved' if d.get('approve') else 'rejected'
    c = _db()
    c.execute("UPDATE qz_kouan_agent SET status=?, approved_by=?, "
              "approved_at=datetime('now','localtime') WHERE id=?",
              (st, session.get('staff_id'), d.get('id')))
    c.commit(); c.close()
    audit('agent_' + st, str(d.get('id')))
    return jsonify(ok=True)


# ---------- API: 所属の任免（オーナーのみ） ----------
@bp.route('/api/admin/kouan/member', methods=['POST'])
def api_kouan_member():
    if not is_owner():
        return jsonify(ok=False, error='任免はオーナーのみ'), 403
    d = request.get_json(silent=True) or {}
    sid, div = (d.get('staff_id') or '').strip(), d.get('division')
    if div not in ('kouan', 'keibi') or not sid:
        return jsonify(ok=False, error='指定が不正です'), 400
    c = _db()
    if d.get('remove'):
        if sid == OWNER_ID:
            c.close(); return jsonify(ok=False, error='オーナーは解任できません'), 400
        c.execute('DELETE FROM qz_kouan_member WHERE staff_id=? AND division=?', (sid, div))
    else:
        c.execute('INSERT OR REPLACE INTO qz_kouan_member(staff_id,division,rank,assigned_by) '
                  'VALUES(?,?,?,?)', (sid, div, d.get('rank') or 'member', session.get('staff_id')))
    c.commit(); c.close()
    audit('member_' + ('remove' if d.get('remove') else 'assign'), sid, div)
    return jsonify(ok=True)


# ---------- API: 管理ページ管理（オーナーのみ） ----------
@bp.route('/api/admin/pages', methods=['GET'])
def api_pages_get():
    if not is_owner():
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()
    acl = {r['page_key']: dict(r) for r in
           c.execute('SELECT * FROM qz_admin_page_acl').fetchall()}
    staff = [dict(r) for r in c.execute(
        "SELECT staff_id, name FROM qz_staff WHERE status='active'").fetchall()]
    c.close()
    pages = [{'key': k, 'label': v[1], 'admin_only': v[2]} for k, v in PAGES.items()]
    pages += [{'key': 'kouan', 'label': '公安部', 'admin_only': True},
              {'key': 'pages', 'label': '管理ページ管理', 'admin_only': True}]
    for p in pages:
        a = acl.get(p['key'], {})
        p['visible_to'] = a.get('visible_to', 'admin')
        p['allow_ids'] = a.get('allow_ids', '')
        p['locked'] = (p['key'] == 'pages')
    return jsonify(ok=True, pages=pages, staff=staff, owner=OWNER_ID)


@bp.route('/api/admin/pages', methods=['POST'])
def api_pages_set():
    if not is_owner():
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    key, v = d.get('page_key'), d.get('visible_to')
    if key == 'pages':
        return jsonify(ok=False, error='管理ページ管理は変更できません'), 400
    if v not in ('owner', 'admin', 'staff', 'custom'):
        return jsonify(ok=False, error='範囲が不正です'), 400
    c = _db()
    c.execute("INSERT INTO qz_admin_page_acl(page_key,visible_to,allow_ids,updated_by) "
              "VALUES(?,?,?,?) ON CONFLICT(page_key) DO UPDATE SET "
              "visible_to=excluded.visible_to, allow_ids=excluded.allow_ids, "
              "updated_by=excluded.updated_by, updated_at=datetime('now','localtime')",
              (key, v, (d.get('allow_ids') or '').strip(), session.get('staff_id')))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# ゼロ（警備局警備企画課）と 裏理事官／オーナー
# ====================================================================
import random

def codename():
    """現行のコードネーム。改称すると変わる。"""
    c = _db()
    r = c.execute('SELECT name FROM qz_kouan_codename WHERE to_date IS NULL '
                  'ORDER BY id DESC LIMIT 1').fetchone()
    c.close()
    return r['name'] if r else 'ゼロ'


def in_term(row):
    """任期内か。term_to を過ぎていたら失効。"""
    import datetime
    t = row['term_to'] if 'term_to' in row.keys() else None
    if not t:
        return True
    return datetime.date.today().isoformat() <= t


def zero_rank():
    """ゼロ（keibi）での階級。任期切れなら None。"""
    sid = session.get('staff_id')
    if not sid:
        return None
    if sid == OWNER_ID:
        return 'ura_rijikan'
    c = _db()
    r = c.execute("SELECT * FROM qz_kouan_member WHERE staff_id=? AND division='keibi'",
                  (sid,)).fetchone()
    c.close()
    if not r or not in_term(r):
        return None
    return r['rank']


def is_ura():
    return zero_rank() == 'ura_rijikan' or is_owner()


def can_see_secret():
    """秘匿された所属を見られるのは裏理事官とオーナーだけ"""
    return is_ura()


def hide_secret(rows):
    """秘匿対象を一覧から取り除く。名簿から消えること自体は分かる。"""
    if can_see_secret():
        return rows
    return [r for r in rows if not r.get('secret')]


def my_alias():
    """ゼロ隊員の偽名。登録してあれば指示などがこの名前で出る。"""
    c = _db()
    r = c.execute('SELECT alias FROM qz_kouan_alias WHERE staff_id=?',
                  (session.get('staff_id'),)).fetchone()
    c.close()
    return r['alias'] if r else ''


def new_agent_no():
    """協力者の4桁番号。重複しないものを引く。"""
    c = _db()
    used = {r['agent_no'] for r in c.execute(
        "SELECT agent_no FROM qz_kouan_agent WHERE agent_no<>''").fetchall()}
    c.close()
    pool = [f'{n:04d}' for n in range(1, 10000) if f'{n:04d}' not in used]
    return random.choice(pool) if pool else ''


# ---------- ページ ----------
@bp.route('/admin/zero')
def admin_zero_page():
    if not zero_rank():
        return '<div style="padding:40px;text-align:center;color:#888;">' \
               'この部署の所属者のみが閲覧できます</div>', 403
    return TOAST_SHIM + render_template('admin/zero.html',
        codename=codename(), rank=zero_rank(), alias=my_alias(),
        is_ura=is_ura(), me=session.get('staff_id'))


@bp.route('/admin/ura')
def admin_ura_page():
    if not is_ura():
        return '<div style="padding:40px;text-align:center;color:#888;">' \
               '裏理事官およびオーナーのみが閲覧できます</div>', 403
    return TOAST_SHIM + render_template('admin/ura.html',
        codename=codename(), is_owner=is_owner(), me=session.get('staff_id'))


# ---------- API: ゼロ ----------
@bp.route('/api/admin/zero/overview')
def api_zero_overview():
    if not zero_rank():
        return jsonify(ok=False, error='この部署の所属者のみ'), 403
    c = _db()
    agents = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_agent ORDER BY id DESC').fetchall()]
    grants = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_mailgrant ORDER BY granted_at DESC').fetchall()]
    orders = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_order ORDER BY id DESC LIMIT 50').fetchall()]
    staff = [dict(r) for r in c.execute(
        "SELECT staff_id,name FROM qz_staff WHERE status='active'").fetchall()]
    aliases = [dict(r) for r in c.execute('SELECT * FROM qz_kouan_alias').fetchall()]
    c.close()
    return jsonify(ok=True, agents=agents, grants=grants, orders=orders,
                   staff=staff, aliases=aliases, codename=codename(),
                   rank=zero_rank(), is_ura=is_ura(), alias=my_alias())


@bp.route('/api/admin/zero/agent/decide', methods=['POST'])
def api_zero_agent_decide():
    """承認すると4桁番号が振られる。却下・解放もここ。"""
    if not zero_rank():
        return jsonify(ok=False, error='この部署の所属者のみ'), 403
    d = request.get_json(silent=True) or {}
    act = d.get('action')
    if act not in ('approve', 'reject', 'release'):
        return jsonify(ok=False, error='操作が不正です'), 400
    if act in ('approve', 'release') and not is_ura():
        return jsonify(ok=False, error='裏理事官のみが決裁できます'), 403
    c = _db()
    if act == 'approve':
        no = new_agent_no()
        c.execute("UPDATE qz_kouan_agent SET status='approved', agent_no=?, approved_by=?, "
                  "approved_at=datetime('now','localtime') WHERE id=? AND status='pending'",
                  (no, session.get('staff_id'), d.get('id')))
        msg = f'承認しました（番号 {no}）'
    elif act == 'reject':
        c.execute("UPDATE qz_kouan_agent SET status='rejected', approved_by=?, "
                  "approved_at=datetime('now','localtime') WHERE id=?",
                  (session.get('staff_id'), d.get('id')))
        msg = '却下しました'
    else:
        c.execute("UPDATE qz_kouan_agent SET status='released', approved_by=?, "
                  "approved_at=datetime('now','localtime') WHERE id=?",
                  (session.get('staff_id'), d.get('id')))
        msg = '解放しました'
    c.commit(); c.close()
    audit('agent_' + act, str(d.get('id')))
    return jsonify(ok=True, message=msg)


@bp.route('/api/admin/zero/mailgrant', methods=['POST'])
def api_zero_mailgrant():
    """メール閲覧の解放。対象者ごとに ok/no を決める。"""
    if not is_ura():
        return jsonify(ok=False, error='裏理事官のみが決裁できます'), 403
    d = request.get_json(silent=True) or {}
    tid = (d.get('target_id') or '').strip()
    if not tid:
        return jsonify(ok=False, error='対象を選んでください'), 400
    allow = 1 if d.get('allowed') else 0
    c = _db()
    c.execute("INSERT INTO qz_kouan_mailgrant(target_id,allowed,reason,granted_by,expires_at) "
              "VALUES(?,?,?,?,?) ON CONFLICT(target_id) DO UPDATE SET "
              "allowed=excluded.allowed, reason=excluded.reason, granted_by=excluded.granted_by, "
              "expires_at=excluded.expires_at, granted_at=datetime('now','localtime')",
              (tid, allow, (d.get('reason') or '')[:300], session.get('staff_id'),
               (d.get('expires_at') or '').strip()))
    c.commit(); c.close()
    audit('mailgrant_' + ('open' if allow else 'close'), tid, d.get('reason') or '')
    return jsonify(ok=True)


@bp.route('/api/admin/zero/order', methods=['POST'])
def api_zero_order():
    """指示を出す。偽名を登録していれば偽名で発出される。"""
    if not zero_rank():
        return jsonify(ok=False, error='この部署の所属者のみ'), 403
    d = request.get_json(silent=True) or {}
    body = (d.get('body') or '').strip()
    if not body:
        return jsonify(ok=False, error='指示の内容を書いてください'), 400
    c = _db()
    c.execute('INSERT INTO qz_kouan_order(to_agent,to_staff,body,issued_by,issued_as) '
              'VALUES(?,?,?,?,?)',
              (d.get('to_agent') or None, (d.get('to_staff') or '').strip(),
               body[:2000], session.get('staff_id'), my_alias()))
    c.commit(); c.close()
    audit('order_issue', str(d.get('to_staff') or d.get('to_agent') or ''), body[:100])
    return jsonify(ok=True)


@bp.route('/api/admin/zero/order/done', methods=['POST'])
def api_zero_order_done():
    if not zero_rank():
        return jsonify(ok=False, error='この部署の所属者のみ'), 403
    d = request.get_json(silent=True) or {}
    st = d.get('status') if d.get('status') in ('done', 'canceled') else 'done'
    c = _db()
    c.execute("UPDATE qz_kouan_order SET status=?, done_at=datetime('now','localtime') WHERE id=?",
              (st, d.get('id')))
    c.commit(); c.close()
    audit('order_' + st, str(d.get('id')))
    return jsonify(ok=True)


@bp.route('/api/admin/zero/alias', methods=['POST'])
def api_zero_alias():
    """自分の偽名を登録する。以後の指示はこの名前で出る。"""
    if not zero_rank():
        return jsonify(ok=False, error='この部署の所属者のみ'), 403
    d = request.get_json(silent=True) or {}
    a = (d.get('alias') or '').strip()[:40]
    c = _db()
    if a:
        c.execute("INSERT INTO qz_kouan_alias(staff_id,alias,set_by) VALUES(?,?,?) "
                  "ON CONFLICT(staff_id) DO UPDATE SET alias=excluded.alias, "
                  "created_at=datetime('now','localtime')",
                  (session.get('staff_id'), a, session.get('staff_id')))
    else:
        c.execute('DELETE FROM qz_kouan_alias WHERE staff_id=?', (session.get('staff_id'),))
    c.commit(); c.close()
    audit('alias_set', session.get('staff_id'), a)
    return jsonify(ok=True)


# ---------- API: 裏理事官／オーナー ----------
@bp.route('/api/admin/ura/overview')
def api_ura_overview():
    if not is_ura():
        return jsonify(ok=False, error='裏理事官およびオーナーのみ'), 403
    c = _db()
    audits = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_audit ORDER BY id DESC LIMIT 200').fetchall()]
    grants = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_mailgrant ORDER BY granted_at DESC').fetchall()]
    aliases = [dict(r) for r in c.execute('SELECT * FROM qz_kouan_alias').fetchall()]
    names = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_codename ORDER BY id DESC').fetchall()]
    resigns = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_resign ORDER BY id DESC').fetchall()]
    members = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_member ORDER BY division, rank DESC').fetchall()]
    mails = [dict(r) for r in c.execute(
        'SELECT COUNT(*) AS n FROM qz_cipher_mails').fetchall()]
    c.close()
    return jsonify(ok=True, audits=audits, grants=grants, aliases=aliases,
                   codenames=names, resigns=resigns, members=members,
                   mail_count=mails[0]['n'] if mails else 0,
                   codename=codename(), is_owner=is_owner())


@bp.route('/api/admin/ura/mailview', methods=['POST'])
def api_ura_mailview():
    """緊急閲覧。対象者に通知は出さないが、記録は必ず残る。"""
    if not is_ura():
        return jsonify(ok=False, error='裏理事官およびオーナーのみ'), 403
    d = request.get_json(silent=True) or {}
    tid = (d.get('target_id') or '').strip()
    reason = (d.get('reason') or '').strip()
    if not tid or not reason:
        return jsonify(ok=False, error='対象と理由の両方が必要です'), 400
    c = _db()
    rows = [dict(r) for r in c.execute(
        'SELECT * FROM qz_cipher_mails ORDER BY id DESC LIMIT 100').fetchall()]
    c.close()
    audit('emergency_mailview', tid, reason)      # ★通知は出さないが記録は残す
    return jsonify(ok=True, mails=rows, notice='この閲覧は記録に残りました')


@bp.route('/api/admin/ura/codename', methods=['POST'])
def api_ura_codename():
    """コードネームを改称する。旧名は履歴に残る。"""
    if not is_ura():
        return jsonify(ok=False, error='裏理事官およびオーナーのみ'), 403
    d = request.get_json(silent=True) or {}
    n = (d.get('name') or '').strip()[:30]
    if not n:
        return jsonify(ok=False, error='新しいコードネームを入力してください'), 400
    c = _db()
    c.execute("UPDATE qz_kouan_codename SET to_date=datetime('now','localtime') "
              "WHERE to_date IS NULL")
    c.execute('INSERT INTO qz_kouan_codename(name,reason,changed_by) VALUES(?,?,?)',
              (n, (d.get('reason') or '')[:200], session.get('staff_id')))
    c.commit(); c.close()
    audit('codename_change', n, d.get('reason') or '')
    return jsonify(ok=True)


@bp.route('/api/admin/ura/resign', methods=['POST'])
def api_ura_resign():
    """引責辞任。記録に基づいて解任する。オーナーのみ。"""
    if not is_owner():
        return jsonify(ok=False, error='引責辞任の発令はオーナーのみ'), 403
    d = request.get_json(silent=True) or {}
    ids = [x.strip() for x in (d.get('staff_ids') or []) if x.strip()]
    if not ids:
        return jsonify(ok=False, error='対象を選んでください'), 400
    if OWNER_ID in ids:
        return jsonify(ok=False, error='オーナーは対象にできません'), 400
    c = _db()
    for sid in ids:
        c.execute('INSERT INTO qz_kouan_resign(staff_id,reason,incident,ordered_by) '
                  'VALUES(?,?,?,?)',
                  (sid, (d.get('reason') or '')[:300], (d.get('incident') or '')[:200],
                   session.get('staff_id')))
        c.execute('DELETE FROM qz_kouan_member WHERE staff_id=?', (sid,))
    c.commit(); c.close()
    audit('resign', ','.join(ids), d.get('incident') or '')
    return jsonify(ok=True, count=len(ids))


@bp.route('/api/admin/ura/term', methods=['POST'])
def api_ura_term():
    """任期の設定・更新。オーナーのみ。"""
    if not is_owner():
        return jsonify(ok=False, error='任期の設定はオーナーのみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute("UPDATE qz_kouan_member SET term_from=?, term_to=?, secret=? "
              "WHERE staff_id=? AND division=?",
              ((d.get('term_from') or '').strip(), (d.get('term_to') or '').strip(),
               1 if d.get('secret') else 0, d.get('staff_id'), d.get('division')))
    c.commit(); c.close()
    audit('term_set', d.get('staff_id') or '', d.get('term_to') or '')
    return jsonify(ok=True)


@bp.route('/api/admin/mymenu')
def api_admin_mymenu():
    """自分が実際に開けるページのキー一覧。メニューの組み立てに使う。"""
    if not _role():
        return jsonify(ok=False), 401
    keys = []
    for k, p in PAGES.items():
        vis = page_visible(k)
        if vis is False:
            continue
        if vis is None and p[2] and _role() != 'admin':
            continue
        keys.append('staff/' + k)
    if page_visible('kouan') is not False and (kouan_rank('kouan') or kouan_rank('keibi')):
        keys.append('kouan/kouan')
    if zero_rank():
        keys.append('kouan/zero')
    if is_ura():
        keys.append('kouan/ura')
    if is_owner():
        keys.append('pages/pages')
    return jsonify(ok=True, keys=keys, is_admin=(_role() == 'admin'), is_owner=is_owner())


@bp.route('/api/admin/kouan/secret', methods=['POST'])
def api_secret_toggle():
    """組織図からの秘匿を切り替える。裏理事官とオーナーのみ。"""
    if not is_ura():
        return jsonify(ok=False, error='裏理事官およびオーナーのみ'), 403
    d = request.get_json(silent=True) or {}
    sid, div = (d.get('staff_id') or '').strip(), d.get('division')
    if not sid or div not in ('kouan', 'keibi'):
        return jsonify(ok=False, error='指定が不正です'), 400
    v = 1 if d.get('secret') else 0
    c = _db()
    c.execute('UPDATE qz_kouan_member SET secret=? WHERE staff_id=? AND division=?',
              (v, sid, div))
    c.commit(); c.close()
    audit('secret_' + ('on' if v else 'off'), sid, div)
    return jsonify(ok=True)


@bp.route('/api/admin/kouan/members')
def api_kouan_members_all():
    """所属一覧。秘匿対象は裏理事官とオーナーにだけ見える。"""
    if not (zero_rank() or kouan_rank('kouan')):
        return jsonify(ok=False, error='所属者のみ'), 403
    c = _db()
    rows = [dict(r) for r in c.execute(
        'SELECT * FROM qz_kouan_member ORDER BY division, rank DESC').fetchall()]
    c.close()
    return jsonify(ok=True, members=hide_secret(rows),
                   can_toggle=is_ura(), is_owner=is_owner())
