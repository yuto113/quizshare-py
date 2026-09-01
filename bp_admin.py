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
    if p[2] and role != 'admin':
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
        'SELECT staff_id, division, rank, assigned_at FROM qz_kouan_member '
        'ORDER BY division, rank DESC').fetchall()]
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
