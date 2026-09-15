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
    'mpadmin': ('admin/mpadmin.html', '会員システム', True),
    'mpsocial': ('admin/mpsocial.html', '会員システム（ひろい）', True),
    'ops': ('admin/ops.html', 'システム状況', True),
    'alert': ('admin/alert.html', '天気・防災情報', False),
    'call': ('admin/call.html', '通話', False),
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


@bp.route('/admin/staff/files/view/<int:file_id>')
def admin_file_view(file_id):
    """ストレージのファイルを別タブで表示する"""
    if not _role():
        return redirect('/admin/login')
    from bp_staff import page_staff_file_view_storage as _v
    return _v(file_id)


@bp.route('/admin/staff/file/<int:message_id>')
def admin_message_file_view(message_id):
    """掲示板の添付を別タブで表示する"""
    if not _role():
        return redirect('/admin/login')
    from bp_staff import page_staff_file_view as _v
    return _v(message_id)


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


# ====================================================================
# 通話（WebRTC / 1対1）
# ====================================================================
import time as _t

PRESENCE_SEC = 35        # 何秒以内に更新があればオンラインとみなすか
RING_TIMEOUT = 40        # 呼び出しを諦めるまでの秒数


@bp.route('/api/admin/call/ping', methods=['POST'])
def api_call_ping():
    """管理センターを開いている間、定期的に呼ばれる。
       同時に、自分あての着信も返す。"""
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    now = _t.time()
    c = _db()
    c.execute("INSERT INTO qz_presence(staff_id,last_seen) VALUES(?,?) "
              "ON CONFLICT(staff_id) DO UPDATE SET last_seen=?", (me, now, now))

    # 応答のないまま時間が過ぎた呼び出しは不在にする
    c.execute("UPDATE qz_call SET status='missed', ended_at=? "
              "WHERE status='ringing' AND created_at < ?", (now, now - RING_TIMEOUT))

    # 自分あての着信
    inc = c.execute("SELECT id, caller, offer FROM qz_call "
                    "WHERE callee=? AND status='ringing' ORDER BY id DESC LIMIT 1",
                    (me,)).fetchone()

    # 自分がかけた通話の相手の応答
    mine = c.execute("SELECT id, status, answer FROM qz_call "
                     "WHERE caller=? AND status IN ('ringing','active') "
                     "ORDER BY id DESC LIMIT 1", (me,)).fetchone()

    # オンラインの社員（自分以外）
    rows = c.execute("""
        SELECT s.staff_id, s.name,
               COALESCE(p.last_seen, 0) AS seen
        FROM qz_staff s LEFT JOIN qz_presence p ON p.staff_id = s.staff_id
        WHERE s.status='active' AND s.staff_id <> ?
        ORDER BY seen DESC""", (me,)).fetchall()
    c.commit(); c.close()

    from bp_staff import dec as _dec
    people = []
    for r in rows:
        try:
            nm = _dec(r['name']) if r['name'] else r['staff_id']
        except Exception:
            nm = r['staff_id']
        people.append({'staff_id': r['staff_id'], 'name': nm,
                       'online': (now - (r['seen'] or 0)) < PRESENCE_SEC})

    return jsonify(ok=True, people=people,
                   incoming=(dict(inc) if inc else None),
                   outgoing=(dict(mine) if mine else None))


@bp.route('/api/admin/call/start', methods=['POST'])
def api_call_start():
    """発信する。相手がオンラインでなければ断る。"""
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False, error='ログインしてね'), 401
    d = request.get_json(silent=True) or {}
    to = (d.get('callee') or '').strip()
    if not to or to == me:
        return jsonify(ok=False, error='相手を選んでね'), 400
    now = _t.time()
    c = _db()
    p = c.execute('SELECT last_seen FROM qz_presence WHERE staff_id=?', (to,)).fetchone()
    if not p or (now - p['last_seen']) > PRESENCE_SEC:
        c.close()
        return jsonify(ok=False, error='相手は管理センターを開いていないよ'), 409
    # 相手が別の通話中なら断る
    busy = c.execute("SELECT 1 FROM qz_call WHERE status IN ('ringing','active') "
                     "AND (caller=? OR callee=?)", (to, to)).fetchone()
    if busy:
        c.close()
        return jsonify(ok=False, error='相手は通話中だよ'), 409
    # 自分の古い通話は片付ける
    c.execute("UPDATE qz_call SET status='ended', ended_at=? "
              "WHERE (caller=? OR callee=?) AND status IN ('ringing','active')",
              (now, me, me))
    cur = c.execute('INSERT INTO qz_call(caller,callee,offer,created_at) VALUES(?,?,?,?)',
                    (me, to, json.dumps(d.get('offer')), now))
    cid = cur.lastrowid
    c.commit(); c.close()
    return jsonify(ok=True, call_id=cid)


@bp.route('/api/admin/call/answer', methods=['POST'])
def api_call_answer():
    """着信に応答する"""
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute("UPDATE qz_call SET status='active', answer=?, answered_at=? "
              "WHERE id=? AND callee=? AND status='ringing'",
              (json.dumps(d.get('answer')), _t.time(), d.get('call_id'), me))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/call/end', methods=['POST'])
def api_call_end():
    """切る。断るときもここ。"""
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    st = 'declined' if d.get('decline') else 'ended'
    c = _db()
    c.execute("UPDATE qz_call SET status=?, ended_at=? WHERE id=? AND (caller=? OR callee=?)",
              (st, _t.time(), d.get('call_id'), me, me))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/call/ice', methods=['POST'])
def api_call_ice():
    """接続経路の候補を送る／受け取る"""
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    cid = d.get('call_id')
    c = _db()
    if d.get('cand'):
        c.execute('INSERT INTO qz_call_ice(call_id,sender,cand) VALUES(?,?,?)',
                  (cid, me, json.dumps(d['cand'])))
        c.commit()
    since = int(d.get('since') or 0)
    rows = c.execute('SELECT id, cand FROM qz_call_ice WHERE call_id=? AND sender<>? '
                     'AND id > ? ORDER BY id', (cid, me, since)).fetchall()
    c.close()
    return jsonify(ok=True, cands=[{'id': r['id'], 'cand': json.loads(r['cand'])}
                                   for r in rows])


@bp.route('/api/admin/call/state')
def api_call_state():
    """通話の状態を確かめる"""
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    cid = request.args.get('call_id')
    c = _db()
    r = c.execute('SELECT * FROM qz_call WHERE id=? AND (caller=? OR callee=?)',
                  (cid, me, me)).fetchone()
    c.close()
    if not r:
        return jsonify(ok=False, error='見つからない'), 404
    return jsonify(ok=True, call=dict(r))


# ====================================================================
# 天気・防災情報
#   気象庁の公開データから警報・注意報を取り、
#   LINE・共有グループ・AIとのDM に流す。
#   ※あくまで補助。公式の情報を必ず確認してもらう前提で作る。
# ====================================================================
import urllib.request as _u
import time as _tm

AI_ID = 'ai_qstart'          # 配信元の社員アカウント
ALERT_HEADER = '~天気・防災情報~'
FETCH_INTERVAL = 300         # 取得の間隔（秒）
JMA_OFFICIAL = 'https://www.jma.go.jp/bosai/'

# 「警報以上」とみなすもの
SEVERE = ('特別警報', '警報')


def _st_get(k, default=''):
    c = _db()
    r = c.execute('SELECT v FROM qz_alert_state WHERE k=?', (k,)).fetchone()
    c.close()
    return r['v'] if r else default


def _st_set(k, v):
    c = _db()
    c.execute("INSERT INTO qz_alert_state(k,v,updated_at) VALUES(?,?,datetime('now','localtime')) "
              "ON CONFLICT(k) DO UPDATE SET v=?, updated_at=datetime('now','localtime')",
              (k, str(v), str(v)))
    c.commit(); c.close()


def _jma_fetch(code='230000'):
    """気象庁の警報・注意報を取る。県コードは愛知=230000。"""
    url = f'https://www.jma.go.jp/bosai/warning/data/warning/{code}.json'
    req = _u.Request(url, headers={'User-Agent': 'MIRAI-POWER/1.0'})
    with _u.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


# 電文の種類（dataTypeCode）＝災害の区分
JMA_DTC = {
    'VPWW55': '大雨',   'VPWW56': '土砂災害', 'VPWW57': '高潮',
    'VPWW58': '暴風',   'VPWW59': '波浪',     'VPWW60': '大雪',
    'VPWW61': 'その他',
}

# 警報コード → 名前
# 出典: 気象庁「警報等情報要素コード管理表」(code.WeatherWarning)
#       jmaxml_20260826_code.xlsx / 令和8年8月26日版
JMA_NAMES = {
    # 注意報（レベル2など）
    '10': 'レベル2大雨注意報',   '12': '大雪注意報',   '13': '風雪注意報',
    '14': '雷注意報',            '15': '強風注意報',   '16': '波浪注意報',
    '17': '融雪注意報',          '18': '洪水注意報',
    '19': 'レベル2高潮注意報',   '20': '濃霧注意報',   '21': '乾燥注意報',
    '22': 'なだれ注意報',        '23': '低温注意報',   '24': '霜注意報',
    '25': '着氷注意報',          '26': '着雪注意報',   '27': 'その他の注意報',
    '29': 'レベル2土砂災害注意報',
    # 警報（レベル3など）
    '02': '暴風雪警報',          '03': 'レベル3大雨警報',
    '04': '洪水警報',            '05': '暴風警報',
    '06': '大雪警報',            '07': '波浪警報',
    '08': 'レベル3高潮警報',     '09': 'レベル3土砂災害警報',
    # 危険警報（レベル4）
    '43': 'レベル4大雨危険警報',
    '48': 'レベル4高潮危険警報',
    '49': 'レベル4土砂災害危険警報',
    # 特別警報（レベル5）
    '32': '暴風雪特別警報',      '33': 'レベル5大雨特別警報',
    '35': '暴風特別警報',        '36': '大雪特別警報',
    '37': '波浪特別警報',        '38': 'レベル5高潮特別警報',
    '39': 'レベル5土砂災害特別警報',
}


def _level_of(name):
    """名前からレベルを判定する。2026年からレベル表記が付いた。"""
    if 'レベル5' in name or 'レベル５' in name or '特別警報' in name:
        return '特別警報'
    if 'レベル4' in name or 'レベル４' in name or '危険警報' in name:
        return '危険警報'
    if '警報' in name:
        return '警報'
    return '注意報'


def alert_fetch_and_send(force=False):
    """新しい警報があれば記録して配信する。戻り値は新規件数。"""
    last = float(_st_get('last_fetch', '0') or 0)
    now = _tm.time()
    if not force and (now - last) < FETCH_INTERVAL:
        return 0
    _st_set('last_fetch', now)

    # 登録されている地域の県コードを集める
    c = _db()
    codes = {r['jma_code'] for r in c.execute(
        "SELECT DISTINCT jma_code FROM qz_alert_area WHERE jma_code IS NOT NULL "
        "AND jma_code <> ''").fetchall()}
    c.close()
    if not codes:
        codes = {'230000'}

    new_items = []
    for code in codes:
        try:
            data = _jma_fetch(code)
        except Exception as e:
            _st_set('last_error', f'{type(e).__name__}: {e}')
            continue
        _st_set('last_ok', now)

        report_time = data.get('reportDatetime', '')
        for at in data.get('areaTypes', []):
            for area in at.get('areas', []):
                aname = (area.get('name') or '')
                for w in area.get('warnings', []):
                    if w.get('status') in ('解除', '', None):
                        continue
                    nm = JMA_NAMES.get(w.get('code', ''), w.get('code', ''))
                    if not nm:
                        continue
                    lv = _level_of(nm)
                    key = f'{code}:{aname}:{nm}:{report_time}'
                    new_items.append({
                        'uniq_key': key, 'kind': 'warning', 'level': lv,
                        'pref': code, 'area_name': aname, 'title': nm,
                        'body': f'{aname}に{nm}が発表されています。',
                        'issued_at': report_time,
                    })

    if not new_items:
        return 0

    # 既に流したものは飛ばす
    c = _db()
    sent = 0
    for it in new_items:
        try:
            c.execute("""INSERT INTO qz_alert_log
                (uniq_key,kind,level,pref,area_name,title,body,issued_at,active)
                VALUES(?,?,?,?,?,?,?,?,1)""",
                (it['uniq_key'], it['kind'], it['level'], it['pref'],
                 it['area_name'], it['title'], it['body'], it['issued_at']))
            sent += 1
        except Exception:
            continue          # 重複は無視
    # 古いものは終了扱いにする
    c.execute("UPDATE qz_alert_log SET active=0 WHERE fetched_at < datetime('now','localtime','-6 hours')")
    c.commit()

    fresh = [dict(r) for r in c.execute(
        'SELECT * FROM qz_alert_log WHERE sent_line=0 ORDER BY id').fetchall()]
    c.close()

    for a in fresh:
        _alert_deliver(a)
    return sent


def _alert_text(a):
    """配信する文面。先頭に必ず見出しを入れる。"""
    icon = {'特別警報': '🚨', '警報': '⚠️', '注意報': '🔔'}.get(a['level'], '🔔')
    return (f"{ALERT_HEADER}\n\n"
            f"{icon} {a['title']}\n"
            f"{a['area_name']}\n\n"
            f"{a['body']}\n"
            f"発表 {a.get('issued_at','')[:16].replace('T',' ')}\n\n"
            f"━━━━━━━━━━\n"
            f"必ず公式の情報を確認してください\n"
            f"気象庁 {JMA_OFFICIAL}")


def _alert_deliver(a):
    """LINE・共有グループ・AIとのDM に流す"""
    text = _alert_text(a)

    # ① LINE
    try:
        from bp_staff import line_send_to_group
        line_send_to_group(text)
    except Exception as e:
        print('[防災LINE]', e)

    try:
        from bp_staff import enc as _enc
    except Exception:
        _enc = lambda x: x

    title = ALERT_HEADER + ' ' + (a.get('title') or '')
    c = _db()

    def _post(ch_id):
        c.execute("""INSERT INTO qz_messages
            (staff_id, staff_name, title, body, channel_id, is_system, created_at)
            VALUES(?,?,?,?,?,0,datetime('now','localtime'))""",
            (AI_ID, _enc('AI'), _enc(title), _enc(text), ch_id))

    # ② 共有グループ（AI が入っているグループ全部）
    for r in c.execute("SELECT id, members FROM qz_channels WHERE channel_type='group'").fetchall():
        try:
            if AI_ID in json.loads(r['members'] or '[]'):
                _post(r['id'])
        except Exception as e:
            print('[防災グループ]', r['id'], e)

    # ③ AIとのDM。地域を登録している社員にだけ送る
    targets = {r['staff_id'] for r in c.execute(
        "SELECT DISTINCT staff_id FROM qz_alert_area WHERE staff_id <> '*'").fetchall()}
    if targets:
        for r in c.execute("SELECT id, members FROM qz_channels WHERE channel_type='dm'").fetchall():
            try:
                mem = json.loads(r['members'] or '[]')
                if AI_ID not in mem:
                    continue
                other = [x for x in mem if x != AI_ID]
                if other and other[0] in targets:
                    _post(r['id'])
            except Exception as e:
                print('[防災DM]', r['id'], e)

    c.execute('UPDATE qz_alert_log SET sent_line=1, sent_board=1 WHERE id=?', (a['id'],))
    c.commit(); c.close()


# ---------- API ----------
@bp.route('/api/admin/alert/active')
def api_alert_active():
    """継続中の警報。AIとのDMを開いたとき、音を鳴らすかの判定に使う。"""
    if not _role():
        return jsonify(ok=False), 401
    try:
        alert_fetch_and_send()
    except Exception as e:
        print('[防災取得]', e)
    c = _db()
    rows = [dict(r) for r in c.execute(
        "SELECT id,level,title,area_name,issued_at FROM qz_alert_log "
        "WHERE active=1 AND level IN ('特別警報','警報') ORDER BY id DESC LIMIT 20").fetchall()]
    last_ok = c.execute("SELECT v,updated_at FROM qz_alert_state WHERE k='last_ok'").fetchone()
    c.close()
    return jsonify(ok=True, severe=rows, count=len(rows),
                   last_ok=(last_ok['updated_at'] if last_ok else None),
                   official=JMA_OFFICIAL)


@bp.route('/api/admin/alert/areas', methods=['GET'])
def api_alert_areas_get():
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    c = _db()
    mine = [dict(r) for r in c.execute(
        'SELECT * FROM qz_alert_area WHERE staff_id=? ORDER BY id', (me,)).fetchall()]
    fixed = [dict(r) for r in c.execute(
        'SELECT * FROM qz_alert_area WHERE is_default=1').fetchall()]
    c.close()
    return jsonify(ok=True, areas=mine, fixed=fixed, need_setup=(len(mine) == 0))


@bp.route('/api/admin/alert/areas', methods=['POST'])
def api_alert_areas_add():
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    pref = (d.get('pref') or '').strip()
    if not pref:
        return jsonify(ok=False, error='都道府県を選んでください'), 400
    c = _db()
    c.execute("""INSERT INTO qz_alert_area
                 (staff_id,pref,city,ward,area,river,jma_code,city_code)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (me, pref, (d.get('city') or '').strip(), (d.get('ward') or '').strip(),
               (d.get('area') or '').strip(), (d.get('river') or '').strip(),
               (d.get('jma_code') or '').strip(), (d.get('city_code') or '').strip()))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/alert/areas/<int:aid>', methods=['DELETE'])
def api_alert_areas_del(aid):
    me = session.get('staff_id')
    if not me:
        return jsonify(ok=False), 401
    c = _db()
    c.execute('DELETE FROM qz_alert_area WHERE id=? AND staff_id=? AND is_default=0', (aid, me))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/alert/test', methods=['POST'])
def api_alert_test():
    """配信のテスト。DMには送らず、LINEと共有グループだけに流す。
       本物と紛らわしくならないよう、必ず【訓練】と明記する。"""
    if not is_owner():
        return jsonify(ok=False, error='オーナーのみ'), 403
    d = request.get_json(silent=True) or {}

    text = (f"【訓練】{ALERT_HEADER}\n"
            f"━━━ これは訓練です ━━━\n\n"
            f"⚠️ {d.get('title') or '大雨警報'}（訓練）\n"
            f"{d.get('area') or '名古屋市（熱田区 沢上）'}\n\n"
            f"{d.get('body') or 'これは配信のテストです。実際の災害ではありません。'}\n\n"
            f"━━━━━━━━━━\n"
            f"【訓練】実際の警報ではありません\n"
            f"本物の情報は気象庁で確認してください\n"
            f"{JMA_OFFICIAL}")

    sent = {'line': False, 'groups': []}

    # ① LINE
    try:
        from bp_staff import line_send_to_group
        line_send_to_group(text)
        sent['line'] = True
    except Exception as e:
        sent['line_error'] = str(e)[:120]

    # ② 共有グループ（AI が入っているグループ）。DM には送らない。
    try:
        from bp_staff import enc as _enc
    except Exception:
        _enc = lambda x: x
    c = _db()
    for r in c.execute("SELECT id, name, members FROM qz_channels "
                       "WHERE channel_type='group'").fetchall():
        try:
            if AI_ID not in json.loads(r['members'] or '[]'):
                continue
            c.execute("""INSERT INTO qz_messages
                (staff_id, staff_name, title, body, channel_id, is_system, created_at)
                VALUES(?,?,?,?,?,0,datetime('now','localtime'))""",
                (AI_ID, _enc('AI'), _enc('【訓練】' + ALERT_HEADER), _enc(text), r['id']))
            sent['groups'].append(r['name'])
        except Exception as e:
            sent.setdefault('errors', []).append(f"{r['id']}: {str(e)[:80]}")
    c.commit(); c.close()

    audit('alert_test', '', '訓練配信')
    return jsonify(ok=True, sent=sent, text=text)


# ====================================================================
# 運営まわり
#   システム状況 / お問い合わせ / 削除依頼 / 一斉通知 / 規約の版管理
# ====================================================================
import shutil as _sh
import glob as _gl

DISK_LIMIT = 512 * 1024 * 1024      # PythonAnywhere 無料枠


def _dir_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


@bp.route('/api/admin/ops/status')
def api_ops_status():
    """システム状況。容量とバックアップを見えるようにする。"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403

    home = os.path.expanduser('~')
    used = _dir_bytes(home)
    dbp = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')

    # バックアップの一覧
    bks = []
    for p in sorted(_gl.glob(home + '/backups/db/*'), reverse=True)[:10]:
        try:
            bks.append({'name': os.path.basename(p),
                        'bytes': os.path.getsize(p),
                        'at': _tm.strftime('%Y-%m-%d %H:%M',
                                           _tm.localtime(os.path.getmtime(p)))})
        except OSError:
            pass

    c = _db()
    log = [dict(r) for r in c.execute(
        'SELECT * FROM qz_backup_log ORDER BY id DESC LIMIT 10').fetchall()]
    # 直近のエラー。テーブル名が環境で違うことがあるので、あるものを探す。
    err_n = 0
    for t in ('error_logs', 'qstart_errors', 'qz_errors', 'error_log'):
        try:
            r0 = c.execute(f"SELECT COUNT(*) AS n FROM {t} "
                           "WHERE created_at > datetime('now','localtime','-7 days')").fetchone()
            err_n = r0['n']; break
        except Exception:
            continue

    def _one(sql, default=None):
        try:
            return c.execute(sql).fetchone()
        except Exception:
            return default

    open_contacts = _one("SELECT COUNT(*) AS n FROM qz_contact WHERE status='open'")
    alert_ok = _one("SELECT updated_at FROM qz_alert_state WHERE k='last_ok'")
    c.close()

    from app import app as _app
    return jsonify(ok=True,
        disk={'used': used, 'limit': DISK_LIMIT,
              'pct': round(used / DISK_LIMIT * 100, 1),
              'free': DISK_LIMIT - used},
        db_bytes=(os.path.getsize(dbp) if os.path.exists(dbp) else 0),
        backups=bks, backup_log=log,
        routes=len(list(_app.url_map.iter_rules())),
        errors_7d=err_n,
        open_contacts=(open_contacts['n'] if open_contacts else 0),
        alert_last_ok=(alert_ok['updated_at'] if alert_ok else None))


# ---------- お問い合わせ ----------
@bp.route('/api/contact', methods=['POST'])
def api_contact_new():
    """外部からの問い合わせ。ログイン不要。"""
    d = request.get_json(silent=True) or {}
    body = (d.get('body') or '').strip()
    if not body:
        return jsonify(ok=False, error='内容を書いてください'), 400
    if len(body) > 4000:
        return jsonify(ok=False, error='長すぎます'), 400
    import hashlib as _h
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0]

    iph = _h.sha256(ip.encode()).hexdigest()[:16]

    # 連打を防ぐ（1分に2件まで）
    try:
        from qz_common import rate_limit as _rl
        if not _rl('contact:' + ip, 2):
            return jsonify(ok=False,
                error='送信が早すぎます。少し待ってください'), 429
    except Exception:
        pass

    # 同じ相手から1時間に3件まで。DBの記録で数える。
    c0 = _db()
    mine = c0.execute("SELECT COUNT(*) AS n FROM qz_contact WHERE ip_hash=? "
                      "AND created_at > datetime('now','localtime','-1 hour')",
                      (iph,)).fetchone()
    c0.close()
    if mine and mine['n'] >= 3:
        return jsonify(ok=False,
            error='送信が多すぎます。しばらく待ってから送ってください'), 429

    # 全体の件数にも上限。DBが膨らんで容量を食い潰さないように。
    c0 = _db()
    tot = c0.execute("SELECT COUNT(*) AS n FROM qz_contact "
                     "WHERE created_at > datetime('now','localtime','-1 day')").fetchone()
    c0.close()
    if tot and tot['n'] >= 200:
        return jsonify(ok=False, error='受付が混み合っています。時間をおいてください'), 429
    c = _db()
    c.execute("""INSERT INTO qz_contact(kind,name,email,body,ip_hash)
                 VALUES(?,?,?,?,?)""",
              (d.get('kind') or 'question', (d.get('name') or '')[:60],
               (d.get('email') or '')[:200], body,
               iph))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/ops/contacts')
def api_contacts_list():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()
    rows = [dict(r) for r in c.execute(
        'SELECT * FROM qz_contact ORDER BY id DESC LIMIT 100').fetchall()]
    dels = [dict(r) for r in c.execute(
        'SELECT * FROM qz_deletion ORDER BY id DESC LIMIT 50').fetchall()]
    c.close()
    return jsonify(ok=True, contacts=rows, deletions=dels)


@bp.route('/api/admin/ops/contacts/<int:cid>', methods=['POST'])
def api_contact_update(cid):
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute("UPDATE qz_contact SET status=?, reply=?, handler=?, "
              "handled_at=datetime('now','localtime') WHERE id=?",
              (d.get('status') or 'working', (d.get('reply') or '')[:2000],
               session.get('staff_id'), cid))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/ops/deletion', methods=['POST'])
def api_deletion_new():
    """削除依頼への対応を記録する。個人情報保護法の観点で記録が要る。"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    tgt = (d.get('target') or '').strip()
    if not tgt:
        return jsonify(ok=False, error='何を削除したか書いてください'), 400
    c = _db()
    c.execute("""INSERT INTO qz_deletion(contact_id,target,scope,reason,done_by,
                 done_at,note) VALUES(?,?,?,?,?,datetime('now','localtime'),?)""",
              (d.get('contact_id'), tgt, d.get('scope') or 'other',
               (d.get('reason') or '')[:500], session.get('staff_id'),
               (d.get('note') or '')[:500]))
    c.commit(); c.close()
    return jsonify(ok=True)


# ---------- 一斉通知 ----------
@bp.route('/api/admin/ops/broadcast', methods=['GET'])
def api_broadcast_list():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()
    rows = [dict(r) for r in c.execute(
        'SELECT * FROM qz_broadcast ORDER BY id DESC LIMIT 50').fetchall()]
    c.close()
    return jsonify(ok=True, items=rows)


@bp.route('/api/admin/ops/broadcast', methods=['POST'])
def api_broadcast_new():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    t = (d.get('title') or '').strip()
    b = (d.get('body') or '').strip()
    if not t or not b:
        return jsonify(ok=False, error='題名と内容が必要です'), 400
    c = _db()
    c.execute("""INSERT INTO qz_broadcast(title,body,level,starts_at,ends_at,created_by)
                 VALUES(?,?,?,?,?,?)""",
              (t[:120], b[:2000], d.get('level') or 'info',
               d.get('starts_at') or None, d.get('ends_at') or None,
               session.get('staff_id')))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/ops/broadcast/<int:bid>', methods=['POST'])
def api_broadcast_toggle(bid):
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute('UPDATE qz_broadcast SET active=? WHERE id=?',
              (1 if d.get('active') else 0, bid))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/broadcast')
def api_broadcast_public():
    """QuizShare 側が読む。いま出ているお知らせ。"""
    c = _db()
    rows = [dict(r) for r in c.execute(
        "SELECT id,title,body,level FROM qz_broadcast WHERE active=1 "
        "AND (starts_at IS NULL OR starts_at <= datetime('now','localtime')) "
        "AND (ends_at IS NULL OR ends_at >= datetime('now','localtime')) "
        "ORDER BY id DESC LIMIT 5").fetchall()]
    c.close()
    return jsonify(ok=True, items=rows)


# ---------- 規約の版管理 ----------
@bp.route('/api/admin/ops/policy', methods=['GET'])
def api_policy_list():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()
    rows = [dict(r) for r in c.execute(
        'SELECT * FROM qz_policy ORDER BY id DESC').fetchall()]
    c.close()
    return jsonify(ok=True, items=rows)


@bp.route('/api/admin/ops/policy', methods=['POST'])
def api_policy_new():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    v = (d.get('version') or '').strip()
    if not v:
        return jsonify(ok=False, error='版を入力してください'), 400
    c = _db()
    c.execute("""INSERT INTO qz_policy(kind,version,summary,effective,created_by)
                 VALUES(?,?,?,?,?)""",
              (d.get('kind') or 'terms', v[:20], (d.get('summary') or '')[:600],
               d.get('effective') or None, session.get('staff_id')))
    c.commit(); c.close()
    return jsonify(ok=True)


# ---------- アクセス統計 ----------
@bp.route('/api/admin/ops/access')
def api_access_stats():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()
    try:
        daily = [dict(r) for r in c.execute("""
            SELECT date(created_at) AS d, COUNT(*) AS n
            FROM access_logs WHERE created_at > datetime('now','localtime','-30 days')
            GROUP BY date(created_at) ORDER BY d DESC LIMIT 30""").fetchall()]
        dev = [dict(r) for r in c.execute("""
            SELECT device, COUNT(*) AS n FROM access_logs
            WHERE created_at > datetime('now','localtime','-30 days')
            GROUP BY device ORDER BY n DESC LIMIT 10""").fetchall()]
        hours = [dict(r) for r in c.execute("""
            SELECT strftime('%H', created_at) AS h, COUNT(*) AS n
            FROM access_logs WHERE created_at > datetime('now','localtime','-30 days')
            GROUP BY h ORDER BY h""").fetchall()]
    except Exception as e:
        c.close()
        return jsonify(ok=False, error=str(e)[:120]), 500
    c.close()
    return jsonify(ok=True, daily=daily, devices=dev, hours=hours)


# ====================================================================
# 会員システムの管理
# ====================================================================

@bp.route('/api/admin/mp/overview')
def api_mp_overview():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()

    def one(sql, d=0):
        try:
            r = c.execute(sql).fetchone()
            return list(r)[0] if r else d
        except Exception:
            return d

    stats = {
        'members':  one("SELECT COUNT(*) FROM mp_member"),
        'active':   one("SELECT COUNT(*) FROM mp_member WHERE status='active'"),
        'apps':     one("SELECT COUNT(*) FROM mp_app WHERE status='published'"),
        'drafts':   one("SELECT COUNT(*) FROM mp_app WHERE status='draft'"),
        'files':    one("SELECT COUNT(*) FROM mp_file"),
        'comments': one("SELECT COUNT(*) FROM mp_comment WHERE hidden=0"),
        'data':     one("SELECT COUNT(*) FROM mp_appdata WHERE hidden=0"),
        'reports':  one("SELECT COUNT(*) FROM mp_report WHERE status='open'"),
        'new7':     one("SELECT COUNT(*) FROM mp_member "
                        "WHERE created_at > datetime('now','localtime','-7 days')"),
        'apps7':    one("SELECT COUNT(*) FROM mp_app "
                        "WHERE created_at > datetime('now','localtime','-7 days')"),
    }

    members = [dict(r) for r in c.execute("""
        SELECT m.member_id, m.nickname, m.tier, m.status, m.grade, m.show_grade,
               m.created_at, m.last_login, m.invited_by,
               (SELECT COUNT(*) FROM mp_app a WHERE a.member_id=m.member_id
                AND a.status='published') AS apps,
               (SELECT COALESCE(SUM(likes),0) FROM mp_app a
                WHERE a.member_id=m.member_id) AS likes
        FROM mp_member m ORDER BY m.id DESC LIMIT 200""").fetchall()]

    apps = [dict(r) for r in c.execute("""
        SELECT a.id, a.title, a.member_id, a.status, a.likes, a.views,
               a.created_at, m.nickname
        FROM mp_app a LEFT JOIN mp_member m ON m.member_id=a.member_id
        ORDER BY a.id DESC LIMIT 100""").fetchall()]

    reports = [dict(r) for r in c.execute("""
        SELECT r.*, a.title, a.member_id AS author
        FROM mp_report r LEFT JOIN mp_app a ON a.id=r.app_id
        ORDER BY r.id DESC LIMIT 50""").fetchall()]

    invites = [dict(r) for r in c.execute(
        'SELECT * FROM mp_invite ORDER BY rowid DESC LIMIT 50').fetchall()]

    c.close()
    return jsonify(ok=True, stats=stats, members=members, apps=apps,
                   reports=reports, invites=invites)


@bp.route('/api/admin/mp/member/<mid>', methods=['POST'])
def api_mp_member(mid):
    """会員の状態を変える。止める・もどす・区分を変える。"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    if d.get('status') in ('active', 'suspended', 'banned'):
        c.execute('UPDATE mp_member SET status=?, note=? WHERE member_id=?',
                  (d['status'], (d.get('note') or '')[:300], mid))
    if d.get('tier') in ('regular', 'invited', 'staff'):
        c.execute('UPDATE mp_member SET tier=? WHERE member_id=?', (d['tier'], mid))
    c.commit(); c.close()
    audit('mp_member_' + (d.get('status') or d.get('tier') or 'edit'), mid,
          d.get('note') or '')
    return jsonify(ok=True)


@bp.route('/api/admin/mp/app/<int:aid>', methods=['POST'])
def api_mp_app(aid):
    """作品を隠す・もどす。消さずに下書きへ戻す。"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    st = d.get('status')
    if st not in ('published', 'draft', 'removed'):
        return jsonify(ok=False, error='状態が不正です'), 400
    c = _db()
    c.execute('UPDATE mp_app SET status=?, removed_by=?, removed_why=? WHERE id=?',
              (st, session.get('staff_id') if st == 'removed' else None,
               (d.get('why') or '')[:300] if st == 'removed' else None, aid))
    c.commit(); c.close()
    audit('mp_app_' + st, str(aid), d.get('why') or '')
    return jsonify(ok=True)


@bp.route('/api/admin/mp/report/<int:rid>', methods=['POST'])
def api_mp_report(rid):
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute("UPDATE mp_report SET status=?, handled_by=?, "
              "handled_at=datetime('now','localtime') WHERE id=?",
              (d.get('status') or 'closed', session.get('staff_id'), rid))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/admin/mp/invite', methods=['POST'])
def api_mp_invite():
    """会員の招待コードを作る"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    import secrets as _s
    d = request.get_json(silent=True) or {}
    code = (d.get('code') or '').strip().upper() or ('MP-' + _s.token_hex(3).upper())
    c = _db()
    try:
        c.execute("""INSERT INTO mp_invite(code,owner,note,max_uses,created_by)
                     VALUES(?,?,?,?,?)""",
                  (code, (d.get('owner') or '').strip() or None,
                   (d.get('note') or '')[:200], int(d.get('max_uses') or 10),
                   session.get('staff_id')))
    except Exception:
        c.close(); return jsonify(ok=False, error='そのコードは既にあります'), 409
    c.commit(); c.close()
    return jsonify(ok=True, code=code)


@bp.route('/api/admin/mp/invite/<code>', methods=['POST'])
def api_mp_invite_edit(code):
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute('UPDATE mp_invite SET active=? WHERE code=?',
              (1 if d.get('active') else 0, code))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# 会員システムの ひろい管理（つぶやき・図鑑・しつもん・ページ・データ）
# ====================================================================

@bp.route('/api/admin/mp/social')
def api_mp_social():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()

    def rows(sql, args=()):
        try:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
        except Exception:
            return []

    posts = rows("""SELECT p.id,p.member_id,p.body,p.likes,p.hidden,p.created_at,
                    g.name AS group_name, m.nickname
                    FROM mp_post p
                    LEFT JOIN mp_member m ON m.member_id=p.member_id
                    LEFT JOIN mp_group g ON g.id=p.group_id
                    ORDER BY p.id DESC LIMIT 80""")
    groups = rows("""SELECT g.*, (SELECT COUNT(*) FROM mp_group_member gm
                     WHERE gm.group_id=g.id) AS members,
                     (SELECT COUNT(*) FROM mp_post p WHERE p.group_id=g.id) AS posts
                     FROM mp_group g ORDER BY g.id DESC""")
    wikis = rows("""SELECT w.slug,w.title,w.category,w.views,w.locked,
                    w.updated_by,w.updated_at,
                    (SELECT COUNT(*) FROM mp_wiki_rev r WHERE r.wiki_id=w.id) AS revs
                    FROM mp_wiki w ORDER BY w.updated_at DESC LIMIT 60""")
    qas = rows("""SELECT q.id,q.member_id,q.title,q.solved,q.hidden,q.views,
                  q.created_at, m.nickname,
                  (SELECT COUNT(*) FROM mp_qa_answer a WHERE a.qa_id=q.id) AS answers
                  FROM mp_qa q LEFT JOIN mp_member m ON m.member_id=q.member_id
                  ORDER BY q.id DESC LIMIT 60""")
    pages = rows("""SELECT p.*, a.title AS app_title, m.nickname
                    FROM mp_page p JOIN mp_app a ON a.id=p.app_id
                    LEFT JOIN mp_member m ON m.member_id=p.member_id
                    ORDER BY p.views DESC LIMIT 60""")
    data = rows("""SELECT d.id,d.app_id,d.tname,d.vals,d.by_member,d.hidden,
                   d.created_at, a.title
                   FROM mp_appdata d LEFT JOIN mp_app a ON a.id=d.app_id
                   ORDER BY d.id DESC LIMIT 80""")
    polls = rows("""SELECT p.*, m.nickname,
                    (SELECT COUNT(*) FROM mp_poll_answer x WHERE x.poll_id=p.id) AS answers
                    FROM mp_poll p LEFT JOIN mp_member m ON m.member_id=p.owner
                    ORDER BY p.id DESC LIMIT 40""")
    shelves = rows("""SELECT s.*, (SELECT COUNT(*) FROM mp_shelf_app sa
                      WHERE sa.shelf_id=s.id) AS apps
                      FROM mp_shelf s ORDER BY s.id DESC LIMIT 40""")
    comments = rows("""SELECT co.id,co.app_id,co.member_id,co.body,co.hidden,
                       co.created_at, a.title FROM mp_comment co
                       LEFT JOIN mp_app a ON a.id=co.app_id
                       ORDER BY co.id DESC LIMIT 60""")
    c.close()
    return jsonify(ok=True, posts=posts, groups=groups, wikis=wikis, qas=qas,
                   pages=pages, data=data, polls=polls, shelves=shelves,
                   comments=comments)


@bp.route('/api/admin/mp/hide', methods=['POST'])
def api_mp_hide():
    """けさずに かくす。なにを かくしたか 記録に のこす。"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    kind, rid = d.get('kind'), d.get('id')
    on = 1 if d.get('hide') else 0
    T = {'post': ('mp_post', 'id'), 'comment': ('mp_comment', 'id'),
         'qa': ('mp_qa', 'id'), 'data': ('mp_appdata', 'id')}
    if kind not in T:
        return jsonify(ok=False, error='しゅるいが ちがいます'), 400
    tbl, key = T[kind]
    c = _db()
    try:
        c.execute(f'UPDATE {tbl} SET hidden=? WHERE {key}=?', (on, rid))
        c.commit()
    except Exception as e:
        c.close(); return jsonify(ok=False, error=str(e)[:100]), 500
    c.close()
    audit('mp_' + kind + ('_hide' if on else '_show'), str(rid), d.get('why') or '')
    return jsonify(ok=True)


@bp.route('/api/admin/mp/wiki/<slug>/lock', methods=['POST'])
def api_mp_wiki_lock(slug):
    """図鑑の ページを 直せなく する"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute('UPDATE mp_wiki SET locked=? WHERE slug=?',
              (1 if d.get('lock') else 0, slug))
    c.commit(); c.close()
    audit('mp_wiki_' + ('lock' if d.get('lock') else 'unlock'), slug)
    return jsonify(ok=True)


@bp.route('/api/admin/mp/page/<slug>', methods=['POST'])
def api_mp_page_toggle(slug):
    """公開ページを 止める・もどす"""
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute('UPDATE mp_page SET active=? WHERE slug=?',
              (1 if d.get('active') else 0, slug))
    c.commit(); c.close()
    audit('mp_page_' + ('on' if d.get('active') else 'off'), slug, d.get('why') or '')
    return jsonify(ok=True)


# ---------- 会員への おしらせ（管理側） ----------
@bp.route('/api/admin/mp/anns', methods=['GET'])
def api_mp_anns():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT a.*, (SELECT COUNT(*) FROM mp_ann_read r WHERE r.ann_id=a.id) AS reads
        FROM mp_ann a ORDER BY a.id DESC LIMIT 50""").fetchall()]
    total = c.execute("SELECT COUNT(*) AS n FROM mp_member "
                      "WHERE status='active'").fetchone()
    c.close()
    return jsonify(ok=True, anns=rows, members=(total['n'] if total else 0))


@bp.route('/api/admin/mp/ann', methods=['POST'])
def api_mp_ann_new():
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    t = (d.get('title') or '').strip()
    if not t:
        return jsonify(ok=False, error='だいめいを 入れてください'), 400
    c = _db()
    c.execute("""INSERT INTO mp_ann(title,body,level,pinned,starts_at,ends_at,created_by)
                 VALUES(?,?,?,?,?,?,?)""",
              (t[:120], (d.get('body') or '')[:3000], d.get('level') or 'info',
               1 if d.get('pinned') else 0, d.get('starts_at') or None,
               d.get('ends_at') or None, session.get('staff_id')))
    c.commit(); c.close()
    audit('mp_ann_new', t[:40])
    return jsonify(ok=True)


@bp.route('/api/admin/mp/ann/<int:aid>', methods=['POST'])
def api_mp_ann_edit(aid):
    if _role() != 'admin':
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    if 'active' in d:
        c.execute('UPDATE mp_ann SET active=? WHERE id=?',
                  (1 if d['active'] else 0, aid))
    if 'pinned' in d:
        c.execute('UPDATE mp_ann SET pinned=? WHERE id=?',
                  (1 if d['pinned'] else 0, aid))
    c.commit(); c.close()
    return jsonify(ok=True)
