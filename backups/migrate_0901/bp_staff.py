# ====================================================================
# bp_staff.py: 社員システム全部(掲示板・人事・公安・暗号・LINE連携)
# app.pyから引っ越してきた。@app.route→@bp.routeに変えただけで中身は同じ。
# ====================================================================
import os
import json
from flask import Blueprint, render_template, request, session, redirect

from qz_common import (
    enc, dec, ok, err, rate_limit, client_ip,
    hash_password, verify_password,
)

bp = Blueprint('staff', __name__)

# ===== QZERO 社員システム =====

@bp.route('/staff/login')
def page_staff_login():
    return render_template('staff_login.html')

@bp.route('/api/staff/login', methods=['POST'])
def api_staff_login():
    data = request.get_json(silent=True) or {}
    staff_id = (data.get('staff_id') or '').strip()
    password = (data.get('password') or '')
    if not staff_id or not password:
        return err('IDとパスワードを入力してね')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT id, password_hash, name, status, active_from FROM qz_staff WHERE staff_id=?', (staff_id,)).fetchone()
    conn.close()
    if not row or not verify_password(password, row[1]):
        return err('IDまたはパスワードが違うよ', 401)
    # 承認待ち・入社日前の人はまだ入れない
    if (row[3] or 'active') == 'pending':
        return err('承認待ちだよ。管理者の審査が終わるまで待っててね', 403)
    if (row[3] or 'active') == 'retired':
        return err('このアカウントは退社済みだよ', 403)
    if row[4]:
        import pytz as _p_login
        from datetime import datetime as _d_login
        today = _d_login.now(_p_login.timezone('Asia/Tokyo')).strftime('%Y-%m-%d')
        if today < row[4]:
            return err('入社日は ' + row[4] + ' だよ。その日からログインできるよ', 403)
    session['staff_id'] = staff_id
    session['staff_name'] = dec(row[2])
    return ok(redirect='/staff/board')

def staff_can_chat(status, active_from):
    # 在籍中かどうか(退社済み・承認待ち・入社日前はFalse)
    if (status or 'active') != 'active':
        return False
    if active_from:
        import pytz as _p
        from datetime import datetime as _d
        today = _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d')
        if today < active_from:
            return False
    return True

def staff_is_admin():
    # 今ログインしているスタッフが管理者(admin)かどうか調べる
    sid = session.get('staff_id')
    if not sid:
        return False
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT role FROM qz_staff WHERE staff_id=?', (sid,)).fetchone()
    conn.close()
    return bool(row and row[0] == 'admin')

def _next_month_first():
    # 「翌月1日」の日付を作る(入社日用)
    import pytz as _p
    from datetime import datetime as _d
    now = _d.now(_p.timezone('Asia/Tokyo'))
    if now.month == 12:
        return f'{now.year + 1:04d}-01-01'
    return f'{now.year:04d}-{now.month + 1:02d}-01'

@bp.route('/api/staff/hr/list', methods=['GET'])
def api_staff_hr_list():
    # 人事: スタッフ全員と承認待ちの一覧(管理者だけ)
    if not staff_is_admin():
        return err('管理者だけが見られるよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT staff_id, name, role, status, active_from, security_role FROM qz_staff ORDER BY id').fetchall()
    conn.close()
    staff = [{'staff_id': r[0], 'name': dec(r[1]), 'role': r[2] or 'member',
              'status': r[3] or 'active', 'active_from': r[4],
              'security_role': r[5] or ''} for r in rows]
    return ok(staff=staff)

@bp.route('/api/staff/hr/add', methods=['POST'])
def api_staff_hr_add():
    # 人事: 管理者がその場でアカウントを作る(即時追加)
    if not staff_is_admin():
        return err('管理者だけができるよ', 403)
    data = request.get_json(silent=True) or {}
    staff_id = (data.get('staff_id') or '').strip()
    name = (data.get('name') or '').strip()
    password = (data.get('password') or '')
    import re as _re
    if not _re.fullmatch(r'[A-Za-z0-9_]{3,20}', staff_id):
        return err('IDは半角英数字と_(アンダーバー)で3〜20文字にしてね')
    if not name or len(name) > 20:
        return err('名前は1〜20文字にしてね')
    if len(password) < 6:
        return err('パスワードは6文字以上にしてね')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    if conn.execute('SELECT id FROM qz_staff WHERE staff_id=?', (staff_id,)).fetchone():
        conn.close()
        return err('そのIDはもう使われているよ')
    conn.execute('INSERT INTO qz_staff (staff_id, name, password_hash, role, status) VALUES (?,?,?,?,?)',
                 (staff_id, enc(name), hash_password(password), 'member', 'active'))
    conn.commit()
    conn.close()
    return ok(message='追加したよ')

@bp.route('/api/staff/hr/decide', methods=['POST'])
def api_staff_hr_decide():
    # 人事: 応募を承認(翌月1日入社)か不承認(削除)にする
    if not staff_is_admin():
        return err('管理者だけができるよ', 403)
    data = request.get_json(silent=True) or {}
    staff_id = (data.get('staff_id') or '').strip()
    approve = bool(data.get('approve'))
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT status FROM qz_staff WHERE staff_id=?', (staff_id,)).fetchone()
    if not row:
        conn.close()
        return err('見つからないよ', 404)
    if (row[0] or 'active') != 'pending':
        conn.close()
        return err('その人は承認待ちじゃないよ')
    if approve:
        start = _next_month_first()
        conn.execute('UPDATE qz_staff SET status=?, active_from=? WHERE staff_id=?', ('active', start, staff_id))
        msg = '承認したよ。入社日は ' + start
    else:
        conn.execute('DELETE FROM qz_staff WHERE staff_id=?', (staff_id,))
        msg = '不承認にして削除したよ'
    conn.commit()
    conn.close()
    return ok(message=msg)

@bp.route('/api/staff/hr/remove', methods=['POST'])
def api_staff_hr_remove():
    # 人事: スタッフを退社にする(ログイン不可になるけど投稿は残る)
    if not staff_is_admin():
        return err('管理者だけができるよ', 403)
    data = request.get_json(silent=True) or {}
    staff_id = (data.get('staff_id') or '').strip()
    if staff_id == session.get('staff_id'):
        return err('自分自身は退社にできないよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT role FROM qz_staff WHERE staff_id=?', (staff_id,)).fetchone()
    if not row:
        conn.close()
        return err('見つからないよ', 404)
    if row[0] == 'admin':
        conn.close()
        return err('管理者は退社にできないよ')
    conn.execute("UPDATE qz_staff SET status='retired', active_from=NULL WHERE staff_id=?", (staff_id,))
    conn.commit()
    conn.close()
    return ok(message=staff_id + ' さんを退社にしたよ。おつかれさま')

@bp.route('/api/staff/register', methods=['POST'])
def api_staff_register():
    # 入社応募: 登録コードを知っている人だけ。承認されるまで「承認待ち」
    import hmac as _hmac
    signup_code = os.environ.get('STAFF_SIGNUP_CODE', '')
    if not signup_code:
        return err('応募の受付はまだ準備中だよ(登録コードが未設定)')
    data = request.get_json(silent=True) or {}
    code = (data.get('signup_code') or '').strip()
    staff_id = (data.get('staff_id') or '').strip()
    name = (data.get('name') or '').strip()
    password = (data.get('password') or '')
    # compare_digest = 比較の時間から答えを推測されない安全な比べ方
    if not _hmac.compare_digest(code, signup_code):
        return err('登録コードが違うよ', 403)
    import re as _re
    if not _re.fullmatch(r'[A-Za-z0-9_]{3,20}', staff_id):
        return err('IDは半角英数字と_(アンダーバー)で3〜20文字にしてね')
    if not name or len(name) > 20:
        return err('名前は1〜20文字にしてね')
    if len(password) < 6:
        return err('パスワードは6文字以上にしてね')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    if conn.execute('SELECT id FROM qz_staff WHERE staff_id=?', (staff_id,)).fetchone():
        conn.close()
        return err('そのIDはもう使われているよ')
    conn.execute('INSERT INTO qz_staff (staff_id, name, password_hash, role, status) VALUES (?,?,?,?,?)',
                 (staff_id, enc(name), hash_password(password), 'member', 'pending'))
    conn.commit()
    conn.close()
    return ok(message='応募を受け付けたよ! 管理者が審査するから待っててね。承認されたら翌月1日に入社だよ')

@bp.route('/staff/hr')
def page_staff_hr():
    # 人事ページ(管理者だけが開ける)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if not staff_is_admin():
        return redirect('/staff/board')
    return render_template('staff_hr.html', my_id=session.get('staff_id'))

def _record_error(source, path, message, detail, user_agent=''):
    # エラーをDBに記録する(記録自体が失敗してもサイトは止めない)
    try:
        import sqlite3 as _sq
        import pytz as _p
        from datetime import datetime as _d
        conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
        conn.execute("""CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, source TEXT, path TEXT,
            message TEXT, detail TEXT, user_agent TEXT)""")
        conn.execute('INSERT INTO error_logs (created_at, source, path, message, detail, user_agent) VALUES (?,?,?,?,?,?)',
                     (_d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S'),
                      source, str(path)[:200], str(message)[:300], str(detail)[:2000], str(user_agent)[:200]))
        # 最新1000件だけ残して古いものは自動で消す
        conn.execute('DELETE FROM error_logs WHERE id NOT IN (SELECT id FROM error_logs ORDER BY id DESC LIMIT 1000)')
        conn.commit()
        conn.close()
    except Exception:
        pass

def _on_request_exception(sender, exception, **extra):
    # サーバー側で予期しないエラーが起きたら自動で記録する
    import traceback
    try:
        _record_error('server', request.path, repr(exception),
                      traceback.format_exc(), request.headers.get('User-Agent', ''))
    except Exception:
        pass

from flask import got_request_exception
# appの読み込み中にappを借りると循環するので、Blueprint登録完了時に接続する
@bp.record_once
def _connect_error_logger(state):
    got_request_exception.connect(_on_request_exception, state.app)

@bp.route('/api/error_report', methods=['POST'])
def api_error_report():
    # ブラウザ側のJavaScriptエラーを受け取って記録する
    # (いたずら防止で1分に5回まで)
    if not rate_limit(f'errrep:{client_ip()}', 5):
        return ok()
    data = request.get_json(silent=True) or {}
    _record_error('browser', data.get('page', ''), data.get('message', ''),
                  data.get('detail', ''), request.headers.get('User-Agent', ''))
    return ok()

@bp.route('/api/staff/errors/list', methods=['GET'])
def api_staff_errors_list():
    if not staff_is_admin():
        return err('管理者だけが見られるよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute("""CREATE TABLE IF NOT EXISTS error_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, source TEXT, path TEXT,
        message TEXT, detail TEXT, user_agent TEXT)""")
    rows = conn.execute('SELECT id, created_at, source, path, message, detail FROM error_logs ORDER BY id DESC LIMIT 100').fetchall()
    conn.close()
    return ok(errors=[{'id': r[0], 'created_at': r[1], 'source': r[2],
                       'path': r[3], 'message': r[4], 'detail': r[5]} for r in rows])

@bp.route('/api/staff/errors/clear', methods=['POST'])
def api_staff_errors_clear():
    if not staff_is_admin():
        return err('管理者だけができるよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM error_logs')
    conn.commit()
    conn.close()
    return ok(message='全部消したよ')

@bp.route('/staff/errors')
def page_staff_errors():
    # エラー一覧ページ(管理者だけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if not staff_is_admin():
        return redirect('/staff/board')
    return render_template('staff_errors.html')

@bp.app_context_processor
def _inject_staff_nav_flags():
    # どのページの部品からでも「管理者かどうか」を使えるように渡す
    try:
        flag = staff_is_admin() if session.get('staff_id') else False
    except Exception:
        flag = False
    try:
        kouan = staff_kouan_role()
    except Exception:
        kouan = ''
    return dict(staff_nav_is_admin=flag, staff_nav_kouan=kouan)

@bp.route('/api/staff/moderation/list', methods=['GET'])
def api_staff_moderation_list():
    # 調査中クイズを全グループ横断で一覧する(管理者だけ)
    if not staff_is_admin():
        return err('管理者だけが見られるよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute("""SELECT q.id, q.question, q.author_name, q.review_reason, q.created_at, g.name
                           FROM quizzes q LEFT JOIN groups g ON q.group_id = g.id
                           WHERE q.under_review = 1 ORDER BY q.created_at DESC""").fetchall()
    conn.close()
    items = []
    for r in rows:
        items.append({'quiz_id': r[0],
                      'question': str(dec(r[1] or ''))[:80],
                      'author': dec(r[2] or ''),
                      'reason': dec(r[3] or '') if r[3] else '',
                      'created_at': str(r[4] or '')[:16],
                      'group': r[5] or '(不明なグループ)'})
    return ok(items=items)

@bp.route('/api/staff/moderation/update', methods=['POST'])
def api_staff_moderation_update():
    # 理由の書き換え or 調査中の解除(管理者だけ)
    if not staff_is_admin():
        return err('管理者だけができるよ', 403)
    data = request.get_json(silent=True) or {}
    quiz_id = str(data.get('quiz_id') or '')
    action = data.get('action')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT id FROM quizzes WHERE id=? AND under_review=1', (quiz_id,)).fetchone()
    if not row:
        conn.close()
        return err('そのクイズは調査中じゃないよ', 404)
    if action == 'unflag':
        conn.execute('UPDATE quizzes SET under_review=0, review_reason=NULL WHERE id=?', (quiz_id,))
        msg = '解除したよ。クイズが復活!'
    elif action == 'reason':
        reason = str(data.get('reason') or '')[:200]
        conn.execute('UPDATE quizzes SET review_reason=? WHERE id=?',
                     (enc(reason) if reason else None, quiz_id))
        msg = '理由を書き換えたよ'
    else:
        conn.close()
        return err('actionがおかしいよ')
    conn.commit()
    conn.close()
    return ok(message=msg)

@bp.route('/staff/moderation')
def page_staff_moderation():
    # モデレーションページ(管理者だけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if not staff_is_admin():
        return redirect('/staff/board')
    return render_template('staff_moderation.html')

def staff_kouan_role():
    # ログイン中スタッフの公安役職('zero'/'kouan'/'')を返す
    sid = session.get('staff_id')
    if not sid:
        return ''
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT security_role FROM qz_staff WHERE staff_id=?', (sid,)).fetchone()
    conn.close()
    return (row[0] or '') if row else ''

def _kouan_db():
    # 公安用のテーブルを用意してDB接続を返す
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute("""CREATE TABLE IF NOT EXISTS qz_kouan_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT,
        created_by TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qz_kouan_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, staff_name TEXT,
        body TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qz_kouan_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, detail TEXT,
        reward INTEGER, status TEXT, done_by TEXT, created_at TEXT, done_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qz_kouan_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id TEXT, amount INTEGER,
        note TEXT, created_at TEXT)""")
    return conn

def _kouan_now():
    import pytz as _p
    from datetime import datetime as _d
    return _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')

@bp.route('/api/staff/kouan/orders', methods=['GET'])
def api_kouan_orders_get():
    if staff_kouan_role() == '':
        return err('権限がないよ', 403)
    conn = _kouan_db()
    orders = conn.execute('SELECT id, title, body, created_by, created_at FROM qz_kouan_orders ORDER BY id DESC LIMIT 50').fetchall()
    result = []
    for o in orders:
        reps = conn.execute('SELECT staff_name, body, created_at FROM qz_kouan_replies WHERE order_id=? ORDER BY id', (o[0],)).fetchall()
        result.append({'id': o[0], 'title': dec(o[1] or ''), 'body': dec(o[2] or ''),
                       'created_by': '🕶️ ゼロ', 'created_at': o[4],
                       'replies': [{'staff_name': dec(r[0] or ''), 'body': dec(r[1] or ''), 'created_at': r[2]} for r in reps]})
    conn.close()
    return ok(orders=result)

@bp.route('/api/staff/kouan/orders', methods=['POST'])
def api_kouan_orders_post():
    # 指令を出せるのはゼロだけ
    if staff_kouan_role() != 'zero':
        return err('指令を出せるのはゼロだけだよ', 403)
    data = request.get_json(silent=True) or {}
    title = str(data.get('title') or '').strip()[:100]
    body = str(data.get('body') or '').strip()[:2000]
    if not title or not body:
        return err('タイトルと本文を入力してね')
    conn = _kouan_db()
    conn.execute('INSERT INTO qz_kouan_orders (title, body, created_by, created_at) VALUES (?,?,?,?)',
                 (enc(title), enc(body), enc(session.get('staff_name') or ''), _kouan_now()))
    conn.commit()
    conn.close()
    return ok(message='指令を発令したよ')

@bp.route('/api/staff/kouan/reply', methods=['POST'])
def api_kouan_reply():
    # 公安メンバーは返信のみできる
    if staff_kouan_role() == '':
        return err('権限がないよ', 403)
    data = request.get_json(silent=True) or {}
    order_id = int(data.get('order_id') or 0)
    body = str(data.get('body') or '').strip()[:2000]
    if not body:
        return err('本文を入力してね')
    conn = _kouan_db()
    if not conn.execute('SELECT id FROM qz_kouan_orders WHERE id=?', (order_id,)).fetchone():
        conn.close()
        return err('その指令は見つからないよ', 404)
    conn.execute('INSERT INTO qz_kouan_replies (order_id, staff_name, body, created_at) VALUES (?,?,?,?)',
                 (order_id, enc(session.get('staff_name') or ''), enc(body), _kouan_now()))
    conn.commit()
    conn.close()
    return ok(message='返信したよ')

@bp.route('/api/staff/kouan/tasks', methods=['GET'])
def api_kouan_tasks_get():
    if staff_kouan_role() == '':
        return err('権限がないよ', 403)
    conn = _kouan_db()
    rows = conn.execute('SELECT id, title, detail, reward, status, done_by, created_at, done_at, assignee_id FROM qz_kouan_tasks ORDER BY id DESC LIMIT 50').fetchall()
    names = dict(conn.execute('SELECT staff_id, name FROM qz_staff').fetchall())
    conn.close()
    my_id = session.get('staff_id')
    tasks = []
    for r in rows:
        assignee_name = dec(names.get(r[8]) or '') if r[8] else ''
        tasks.append({'id': r[0], 'title': dec(r[1] or ''), 'detail': dec(r[2] or ''),
                      'reward': r[3] or 0, 'status': r[4] or 'open',
                      'done_by': dec(r[5] or '') if r[5] else '',
                      'created_at': r[6], 'done_at': r[7],
                      'assignee': assignee_name,
                      'can_done': (r[4] or 'open') != 'done' and (not r[8] or r[8] == my_id)})
    return ok(tasks=tasks)

@bp.route('/api/staff/kouan/tasks', methods=['POST'])
def api_kouan_tasks_post():
    # タスクを発行できるのはゼロだけ(報酬つき)
    if staff_kouan_role() != 'zero':
        return err('タスクを出せるのはゼロだけだよ', 403)
    data = request.get_json(silent=True) or {}
    title = str(data.get('title') or '').strip()[:100]
    detail = str(data.get('detail') or '').strip()[:2000]
    reward = max(0, min(1000000, int(data.get('reward') or 0)))
    assignee_id = str(data.get('assignee_id') or '').strip()
    if not title:
        return err('タスク名を入力してね')
    conn = _kouan_db()
    # 担当者が指定されていたら、公安メンバーかどうか確認する
    if assignee_id:
        row = conn.execute("SELECT staff_id FROM qz_staff WHERE staff_id=? AND security_role IS NOT NULL AND security_role != ''", (assignee_id,)).fetchone()
        if not row:
            conn.close()
            return err('その担当者は公安メンバーじゃないよ')
    conn.execute('INSERT INTO qz_kouan_tasks (title, detail, reward, status, created_at, assignee_id) VALUES (?,?,?,?,?,?)',
                 (enc(title), enc(detail), reward, 'open', _kouan_now(), assignee_id or None))
    conn.commit()
    conn.close()
    return ok(message='極秘タスクを発行したよ')

@bp.route('/api/staff/kouan/tasks/done', methods=['POST'])
def api_kouan_task_done():
    if staff_kouan_role() == '':
        return err('権限がないよ', 403)
    data = request.get_json(silent=True) or {}
    task_id = int(data.get('task_id') or 0)
    conn = _kouan_db()
    row = conn.execute('SELECT status, assignee_id, reward FROM qz_kouan_tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        conn.close()
        return err('タスクが見つからないよ', 404)
    if row[0] == 'done':
        conn.close()
        return err('もう完了済みだよ')
    if row[1] and row[1] != session.get('staff_id'):
        conn.close()
        return err('このタスクの担当者じゃないよ', 403)
    # 報酬は予算口座から支払う。予算が足りなければ完了できない
    reward = int(row[2] or 0)
    budget = conn.execute("SELECT COALESCE(SUM(amount),0) FROM qz_kouan_grants WHERE staff_id='BUDGET'").fetchone()[0]
    if reward > 0 and budget < reward:
        conn.close()
        return err('予算が足りないよ(いま ' + str(budget) + ' KP)。ゼロに予算の補充をお願いしてね')
    if reward > 0:
        conn.execute('INSERT INTO qz_kouan_grants (staff_id, amount, note, created_at) VALUES (?,?,?,?)',
                     ('BUDGET', -reward, enc('タスク報酬の支払い'), _kouan_now()))
    conn.execute('UPDATE qz_kouan_tasks SET status=?, done_by=?, done_by_id=?, done_at=? WHERE id=?',
                 ('done', enc(session.get('staff_name') or ''), session.get('staff_id'), _kouan_now(), task_id))
    conn.commit()
    conn.close()
    return ok(message='任務完了! おつかれさま')

@bp.route('/api/staff/hr/security', methods=['POST'])
def api_staff_hr_security():
    # 人事: 公安の任命(なし→公安→ゼロ→なし)
    if not staff_is_admin():
        return err('管理者だけができるよ', 403)
    data = request.get_json(silent=True) or {}
    staff_id = str(data.get('staff_id') or '').strip()
    sec = data.get('security_role')
    if sec not in ['', 'kouan', 'zero']:
        return err('役職の値がおかしいよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE qz_staff SET security_role=? WHERE staff_id=?', (sec or None, staff_id))
    conn.commit()
    conn.close()
    return ok(message='任命したよ')

# KPのレート(ここを変えれば表示が全部変わる)

# ===== LINE連携 =====
import hashlib, hmac, base64
import urllib.request as _line_req

def line_download_content(message_id):
    # LINEサーバーからメッセージの中身(画像やファイル)をダウンロードする
    # 返り値: (base64のdata URL, Content-Type) 失敗なら (None, None)
    token = os.environ.get('LINE_CHANNEL_TOKEN', '')
    if not token:
        return None, None
    try:
        url = 'https://api-data.line.me/v2/bot/message/' + str(message_id) + '/content'
        req = _line_req.Request(url, headers={'Authorization': 'Bearer ' + token})
        resp = _line_req.urlopen(req, timeout=15)
        raw = resp.read(6_000_000 + 1)  # 6MBまで(掲示板の上限と同じ)
        if len(raw) > 6_000_000:
            return None, None  # 大きすぎたら諦めて「送られました」表示にする
        ctype = resp.headers.get('Content-Type', 'application/octet-stream')
        import base64 as _b64
        return 'data:' + ctype + ';base64,' + _b64.b64encode(raw).decode(), ctype
    except Exception as e:
        print('[LINE DL失敗]', e)
        return None, None


def line_send_to_group(text):
    token = os.environ.get('LINE_CHANNEL_TOKEN', '')
    group_id = os.environ.get('LINE_GROUP_ID', '')
    if not token or not group_id:
        return
    try:
        body = json.dumps({'to': group_id, 'messages': [{'type': 'text', 'text': text}]}).encode()
        req = _line_req.Request('https://api.line.me/v2/bot/message/push', data=body,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        _line_req.urlopen(req, timeout=5)
    except Exception as e:
        print('[LINE送信エラー]', e)

@bp.route('/api/line/webhook', methods=['POST'])
def api_line_webhook():
    if not rate_limit('line_webhook:' + request.remote_addr, 30):
        return 'rate limited', 429
    secret = os.environ.get('LINE_CHANNEL_SECRET', '')
    body = request.get_data(as_text=True)
    sig = request.headers.get('X-Line-Signature', '')
    if secret:
        digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if sig != base64.b64encode(digest).decode():
            return 'bad sig', 403
    data = request.get_json(silent=True) or {}
    for event in data.get('events', []):
        if event.get('type') != 'message':
            continue
        msg = event.get('message', {})
        mtype = msg.get('type')
        # 種類ごとに仕分け: text=本文 / image,file=本物を取り込み / それ以外=お知らせ表示
        text = ''
        _img_data = None
        _file_data = None
        _file_name = None
        if mtype == 'text':
            text = msg.get('text', '')
        elif mtype == 'image':
            _img_data, _ct = line_download_content(msg.get('id'))
            if not _img_data:
                text = '画像が送られました。(取り込みに失敗)'
        elif mtype == 'file':
            _file_data, _ct = line_download_content(msg.get('id'))
            _file_name = msg.get('fileName') or 'LINEのファイル'
            if not _file_data:
                text = 'ファイルが送られました。(取り込みに失敗)'
        elif mtype == 'sticker':
            text = 'スタンプが送られました。'
        elif mtype == 'video':
            text = '動画が送られました。'
        elif mtype == 'audio':
            text = '音声が送られました。'
        elif mtype == 'location':
            text = '位置情報が送られました。(' + str(msg.get('address') or '') + ')'
        else:
            text = mtype + 'が送られました。'
        if not text and not _img_data and not _file_data:
            continue
        source = event.get('source', {})
        # ===== 門番: 登録済みグループ以外は掲示板に載せない =====
        # 1対1(user)や知らないグループ(他人がBotを招待した場合)は全部無視する
        if source.get('type') != 'group':
            continue  # 1対1・複数人トークはここでストップ
        if source.get('type') == 'group' and not os.environ.get('LINE_GROUP_ID'):
            gid = source['groupId']
            os.environ['LINE_GROUP_ID'] = gid
            with open('/home/yuto113/.line_env', 'a') as f:
                f.write('LINE_GROUP_ID=' + gid + '\n')
        if source.get('groupId') != os.environ.get('LINE_GROUP_ID'):
            continue  # 登録済みグループ以外(よそのグループ)はここでストップ
        user_id = source.get('userId', '')
        display_name = 'LINEメンバー'
        if user_id:
            token = os.environ.get('LINE_CHANNEL_TOKEN', '')
            try:
                if source.get('type') == 'group':
                    profile_url = 'https://api.line.me/v2/bot/group/' + source['groupId'] + '/member/' + user_id + '/profile'
                else:
                    profile_url = 'https://api.line.me/v2/bot/profile/' + user_id
                prof_req = _line_req.Request(profile_url, headers={'Authorization': 'Bearer ' + token})
                prof_data = json.loads(_line_req.urlopen(prof_req, timeout=5).read())
                display_name = prof_data.get('displayName', 'LINEメンバー')
            except Exception:
                display_name = 'LINEさん#' + user_id[-4:]
        # 登録済みの名前を優先
        try:
            import sqlite3 as _sq_nm
            _nc = _sq_nm.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
            _nrow = _nc.execute('SELECT name FROM qz_line_names WHERE user_id=?', (user_id[:8],)).fetchone()
            _nc.close()
            if _nrow:
                display_name = _nrow[0]
        except Exception:
            pass
        import sqlite3 as _sq_line
        conn = _sq_line.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
        from datetime import datetime, timezone, timedelta
        _jst = timezone(timedelta(hours=9))
        _now = datetime.now(_jst).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('INSERT INTO qz_messages (staff_id, staff_name, title, body, channel_id, created_at, image_data, file_data, file_name) VALUES (?,?,?,?,?,?,?,?,?)',
            ('line_' + user_id[:8], display_name + '(LINE)', '', text, '22', _now, _img_data, _file_data, _file_name))
        conn.commit()
        conn.close()
    return 'OK', 200




KP_RATE_TEXT = "100KP = 1回年(QZERO社内通貨)"

@bp.route('/api/staff/kouan/members', methods=['GET'])
def api_kouan_members():
    # 公安メンバーの名簿(担当者選び・送金相手選び用。公安なら誰でも)
    # ゼロは名簿に出さない(正体保護)
    if staff_kouan_role() == '':
        return err('権限がないよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute("SELECT staff_id, name FROM qz_staff WHERE security_role = 'kouan'").fetchall()
    conn.close()
    return ok(members=[{'staff_id': r[0], 'name': dec(r[1] or '')} for r in rows])

@bp.route('/api/staff/kouan/grant', methods=['POST'])
def api_kouan_grant():
    # ゼロがKPを直接配布する(マイナスなら回収)
    if staff_kouan_role() != 'zero':
        return err('配布できるのはゼロだけだよ', 403)
    data = request.get_json(silent=True) or {}
    staff_id = str(data.get('staff_id') or '').strip()
    amount = int(data.get('amount') or 0)
    note = str(data.get('note') or '').strip()[:100]
    if amount == 0:
        return err('0KPは配布できないよ')
    amount = max(-1000000, min(1000000, amount))
    # 「self」が来たらゼロ自身への配布とみなす
    if staff_id == 'self':
        staff_id = session.get('staff_id')
    conn = _kouan_db()
    # 予算口座(BUDGET)は人間じゃない仮想口座なので、メンバー確認を飛ばす
    if staff_id != 'BUDGET':
        row = conn.execute("SELECT staff_id FROM qz_staff WHERE staff_id=? AND security_role IS NOT NULL AND security_role != ''", (staff_id,)).fetchone()
        if not row:
            conn.close()
            return err('その人は公安メンバーじゃないよ')
    conn.execute('INSERT INTO qz_kouan_grants (staff_id, amount, note, created_at) VALUES (?,?,?,?)',
                 (staff_id, amount, enc(note) if note else None, _kouan_now()))
    conn.commit()
    conn.close()
    if amount > 0:
        return ok(message=str(amount) + ' KPを配布したよ')
    return ok(message=str(-amount) + ' KPを回収したよ')

@bp.route('/api/staff/kouan/transfer', methods=['POST'])
def api_kouan_transfer():
    # 公安メンバー同士でKPをあげる(送金)
    role = staff_kouan_role()
    if role == '':
        return err('権限がないよ', 403)
    data = request.get_json(silent=True) or {}
    to_id = str(data.get('to_staff_id') or '').strip()
    amount = int(data.get('amount') or 0)
    my_id = session.get('staff_id')
    if amount <= 0:
        return err('1KP以上を入力してね')
    amount = min(1000000, amount)
    if to_id == my_id:
        return err('自分にはあげられないよ')
    conn = _kouan_db()
    row = conn.execute("SELECT staff_id FROM qz_staff WHERE staff_id=? AND security_role IS NOT NULL AND security_role != ''", (to_id,)).fetchone()
    if not row:
        conn.close()
        return err('その人は公安メンバーじゃないよ')
    # ゼロ以外は残高チェック(持っている以上はあげられない)
    if role != 'zero':
        task_sum = conn.execute("SELECT COALESCE(SUM(reward),0) FROM qz_kouan_tasks WHERE status='done' AND done_by_id=?", (my_id,)).fetchone()[0]
        grant_sum = conn.execute('SELECT COALESCE(SUM(amount),0) FROM qz_kouan_grants WHERE staff_id=?', (my_id,)).fetchone()[0]
        if task_sum + grant_sum < amount:
            conn.close()
            return err('残高が足りないよ(いま ' + str(task_sum + grant_sum) + ' KP)')
        conn.execute('INSERT INTO qz_kouan_grants (staff_id, amount, note, created_at) VALUES (?,?,?,?)',
                     (my_id, -amount, enc('送金(あげた)'), _kouan_now()))
    conn.execute('INSERT INTO qz_kouan_grants (staff_id, amount, note, created_at) VALUES (?,?,?,?)',
                 (to_id, amount, enc('プレゼント'), _kouan_now()))
    conn.commit()
    conn.close()
    return ok(message=str(amount) + ' KPをあげたよ')

@bp.route('/api/staff/kouan/points', methods=['GET'])
def api_kouan_points():
    # 公安メンバー全員のKP残高(完了したタスクの報酬の合計)
    if staff_kouan_role() == '':
        return err('権限がないよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    members = conn.execute("SELECT staff_id, name, security_role FROM qz_staff WHERE security_role = 'kouan'").fetchall()
    sums = dict(conn.execute("SELECT done_by_id, COALESCE(SUM(reward),0) FROM qz_kouan_tasks WHERE status='done' AND done_by_id IS NOT NULL GROUP BY done_by_id").fetchall())
    # ゼロから直接配布されたKPも合計する
    conn.execute('''CREATE TABLE IF NOT EXISTS qz_kouan_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id TEXT, amount INTEGER,
        note TEXT, created_at TEXT)''')
    for sid, total in conn.execute('SELECT staff_id, COALESCE(SUM(amount),0) FROM qz_kouan_grants GROUP BY staff_id').fetchall():
        sums[sid] = sums.get(sid, 0) + total
    # 予算口座は人間の残高一覧から取り出して、別枠で返す
    budget = int(sums.pop('BUDGET', 0))
    conn.close()
    points = [{'staff_id': m[0], 'name': dec(m[1] or ''), 'security_role': m[2],
               'kp': int(sums.get(m[0], 0))} for m in members]
    points.sort(key=lambda p: -p['kp'])
    my_id = session.get('staff_id')
    return ok(points=points, rate=KP_RATE_TEXT, my_id=my_id,
              my_kp=int(sums.get(my_id, 0)), is_zero=(staff_kouan_role() == 'zero'),
              budget=budget)

@bp.route('/staff/kp')
def page_staff_kp():
    # KP残高ページ(公安メンバーだけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if staff_kouan_role() == '':
        return redirect('/staff/board')
    return render_template('staff_kp.html')

@bp.route('/staff/kouan')
def page_staff_kouan():
    # 公安ページ(任命された人だけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    role = staff_kouan_role()
    if role == '':
        return redirect('/staff/board')
    return render_template('staff_kouan.html', kouan_role=role)

@bp.route('/staff/handbook')
def page_staff_handbook():
    # 社員ハンドブック(スタッフなら誰でも読める)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_handbook.html')

def _cipher_db():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute("""CREATE TABLE IF NOT EXISTS qz_cipher_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT, key_name TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qz_cipher_members (
        key_id INTEGER, staff_id TEXT, UNIQUE(key_id, staff_id))""")
    return conn

def _my_cipher_keys():
    # 自分が使えるキーの一覧を返す
    sid = session.get('staff_id')
    if not sid:
        return []
    conn = _cipher_db()
    rows = conn.execute("""SELECT k.id, k.key_name FROM qz_cipher_keys k
                           JOIN qz_cipher_members m ON k.id = m.key_id
                           WHERE m.staff_id = ? OR m.staff_id = '*'""", (sid,)).fetchall()
    conn.close()
    # 重複を除いて返す
    seen = {}
    for r in rows:
        seen[r[0]] = r[1]
    return [{'id': k, 'name': v} for k, v in seen.items()]

@bp.route('/api/staff/cipher/keys', methods=['GET'])
def api_cipher_keys():
    # 人事用: 全キーとメンバー一覧(管理者だけ)
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    conn = _cipher_db()
    keys = conn.execute('SELECT id, key_name, created_at FROM qz_cipher_keys ORDER BY id').fetchall()
    result = []
    for k in keys:
        mems = [r[0] for r in conn.execute('SELECT staff_id FROM qz_cipher_members WHERE key_id=?', (k[0],)).fetchall()]
        result.append({'id': k[0], 'name': k[1], 'created_at': k[2], 'members': mems})
    conn.close()
    return ok(keys=result)

@bp.route('/api/staff/cipher/keys/new', methods=['POST'])
def api_cipher_key_new():
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()[:30]
    if not name:
        return err('キーの名前を入れてね')
    import pytz as _p
    from datetime import datetime as _d
    conn = _cipher_db()
    conn.execute('INSERT INTO qz_cipher_keys (key_name, created_at) VALUES (?,?)',
                 (name, _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d')))
    conn.commit()
    conn.close()
    return ok(message='キー「' + name + '」を発行したよ')

@bp.route('/api/staff/cipher/keys/member', methods=['POST'])
def api_cipher_key_member():
    # キーの「使える人」を追加/削除(staff_id='*'で全員)
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    key_id = int(data.get('key_id') or 0)
    staff_id = str(data.get('staff_id') or '').strip()
    action = data.get('action')
    if not staff_id:
        return err('社員IDを入れてね')
    conn = _cipher_db()
    if not conn.execute('SELECT id FROM qz_cipher_keys WHERE id=?', (key_id,)).fetchone():
        conn.close()
        return err('そのキーは無いよ', 404)
    if action == 'add':
        # 「*」以外は実在スタッフか確認
        if staff_id != '*':
            row = conn.execute('SELECT staff_id FROM qz_staff WHERE staff_id=?', (staff_id,)).fetchone()
            if not row:
                conn.close()
                return err('その社員IDは見つからないよ')
        conn.execute('INSERT OR IGNORE INTO qz_cipher_members (key_id, staff_id) VALUES (?,?)', (key_id, staff_id))
        msg = '追加したよ'
    elif action == 'remove':
        conn.execute('DELETE FROM qz_cipher_members WHERE key_id=? AND staff_id=?', (key_id, staff_id))
        msg = '外したよ'
    elif action == 'delete_key':
        conn.execute('DELETE FROM qz_cipher_members WHERE key_id=?', (key_id,))
        conn.execute('DELETE FROM qz_cipher_keys WHERE id=?', (key_id,))
        msg = 'キーを削除したよ(過去の暗号文は読めなくなる)'
    else:
        conn.close()
        return err('actionがおかしいよ')
    conn.commit()
    conn.close()
    return ok(message=msg)

@bp.route('/api/staff/cipher/mykeys', methods=['GET'])
def api_cipher_mykeys():
    # 自分が使えるキー一覧(掲示板の🔐ボタン用)
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    return ok(keys=_my_cipher_keys())

@bp.route('/staff/cipher')
def page_staff_cipher():
    # 暗号ツールページ(スタッフなら誰でも開ける)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_cipher.html')

@bp.route('/api/staff/cipher/encode', methods=['POST'])
def api_cipher_encode():
    # 文章をキーで暗号化して、貼り付け可能な暗号文にする
    sid = session.get('staff_id')
    if not sid:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    key_id = int(data.get('key_id') or 0)
    text = str(data.get('text') or '').strip()[:2000]
    if not text:
        return err('文章を入れてね')
    my_keys = [k['id'] for k in _my_cipher_keys()]
    if key_id not in my_keys:
        return err('そのキーを使う権限がないよ', 403)
    # キーID+暗号本文をまとめて暗号化し、「QZ暗号」形式の文字列にする
    import base64 as _b64
    payload = str(key_id) + '|' + text
    token = enc(payload)  # サーバーの暗号技術(Fernet)で本当に暗号化
    code = 'QZ-ANGO:' + _b64.urlsafe_b64encode(token.encode()).decode()
    conn = _cipher_db()
    kname = conn.execute('SELECT key_name FROM qz_cipher_keys WHERE id=?', (key_id,)).fetchone()
    conn.close()
    return ok(code=code, key_name=kname[0] if kname else '')

@bp.route('/api/staff/cipher/decode', methods=['POST'])
def api_cipher_decode():
    # 暗号文を貼り付け→権限があれば解読
    sid = session.get('staff_id')
    if not sid:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    code = str(data.get('code') or '').strip()
    if not code.startswith('QZ-ANGO:'):
        return err('これはQZ暗号の形式じゃないみたい(QZ-ANGO:で始まるやつを貼ってね)')
    import base64 as _b64
    try:
        token = _b64.urlsafe_b64decode(code[8:].encode()).decode()
        payload = dec(token)
        key_id_str, text = payload.split('|', 1)
        key_id = int(key_id_str)
    except Exception:
        return err('暗号文がこわれているみたい')
    # 権限チェック: このキーの使える人か?
    conn = _cipher_db()
    allowed = conn.execute("""SELECT 1 FROM qz_cipher_members
                              WHERE key_id=? AND (staff_id=? OR staff_id='*')""",
                           (key_id, sid)).fetchone()
    kname = conn.execute('SELECT key_name FROM qz_cipher_keys WHERE id=?', (key_id,)).fetchone()
    conn.close()
    if not allowed:
        return err('このキー(' + (kname[0] if kname else '?') + ')を解読する権限がないよ', 403)
    return ok(text=text, key_name=kname[0] if kname else '')

@bp.route('/api/staff/list-simple', methods=['GET'])
def api_staff_list_simple():
    # プルダウン用: 社員のIDと名前の一覧(管理者だけ)
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute("SELECT staff_id, name FROM qz_staff WHERE COALESCE(status,'active')='active' ORDER BY staff_id").fetchall()
    conn.close()
    return ok(staff=[{'id': r[0], 'name': dec(r[1] or '')} for r in rows])

def _cipher_mail_db():
    conn = _cipher_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS qz_cipher_mails (
        id INTEGER PRIMARY KEY AUTOINCREMENT, from_id TEXT, to_id TEXT,
        code TEXT, created_at TEXT, read_flag INTEGER DEFAULT 0)""")
    return conn

@bp.route('/api/staff/cipher/mail/send', methods=['POST'])
def api_cipher_mail_send():
    # 暗号文を社内メールとして送る
    sid = session.get('staff_id')
    if not sid:
        return err('ログインしてね', 401)
    if not rate_limit(f'cmail:{client_ip()}', 10):
        return err('少し待ってね')
    data = request.get_json(silent=True) or {}
    to_id = str(data.get('to_id') or '').strip()
    code = str(data.get('code') or '').strip()
    if not to_id:
        return err('宛先を選んでね')
    if not code.startswith('QZ-ANGO:') or len(code) > 8000:
        return err('先に文章を暗号化してね(QZ-ANGO:の暗号文だけ送れるよ)')
    conn = _cipher_mail_db()
    row = conn.execute("SELECT staff_id FROM qz_staff WHERE staff_id=? AND COALESCE(status,'active')='active'", (to_id,)).fetchone()
    if not row:
        conn.close()
        return err('その宛先は見つからないよ')
    import pytz as _p
    from datetime import datetime as _d
    conn.execute('INSERT INTO qz_cipher_mails (from_id, to_id, code, created_at) VALUES (?,?,?,?)',
                 (sid, to_id, code, _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')))
    conn.commit()
    conn.close()
    return ok(message='暗号メールを送ったよ📮')

@bp.route('/api/staff/cipher/mail/inbox', methods=['GET'])
def api_cipher_mail_inbox():
    # 自分宛の暗号メール一覧(暗号文のまま返す=解読は権限チェック付きの別API)
    sid = session.get('staff_id')
    if not sid:
        return err('ログインしてね', 401)
    conn = _cipher_mail_db()
    rows = conn.execute('SELECT id, from_id, code, created_at, read_flag FROM qz_cipher_mails WHERE to_id=? ORDER BY id DESC LIMIT 50', (sid,)).fetchall()
    conn.execute('UPDATE qz_cipher_mails SET read_flag=1 WHERE to_id=?', (sid,))
    conn.commit()
    conn.close()
    return ok(mails=[{'id': r[0], 'from_id': r[1], 'code': r[2], 'created_at': r[3], 'unread': r[4] == 0} for r in rows])

@bp.route('/api/staff/cipher/mail/delete', methods=['POST'])
def api_cipher_mail_delete():
    sid = session.get('staff_id')
    if not sid:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    conn = _cipher_mail_db()
    conn.execute('DELETE FROM qz_cipher_mails WHERE id=? AND to_id=?', (int(data.get('id') or 0), sid))
    conn.commit()
    conn.close()
    return ok(message='消したよ')

@bp.route('/api/staff/list-for-mail', methods=['GET'])
def api_staff_list_for_mail():
    # 宛先プルダウン用(一般スタッフも使える。IDと名前だけ)
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute("SELECT staff_id, name FROM qz_staff WHERE COALESCE(status,'active')='active' ORDER BY staff_id").fetchall()
    conn.close()
    me = session.get('staff_id')
    return ok(staff=[{'id': r[0], 'name': dec(r[1] or '')} for r in rows if r[0] != me])

@bp.route('/api/staff/cipher/decrypt', methods=['POST'])
def api_cipher_decrypt():
    # 🔓復号: そのキーの使える人だけが平文を受け取れる
    sid = session.get('staff_id')
    if not sid:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    message_id = int(data.get('message_id') or 0)
    conn = _cipher_db()
    row = conn.execute('SELECT body, cipher_key_id FROM qz_messages WHERE id=?', (message_id,)).fetchone()
    if not row or not row[1]:
        conn.close()
        return err('その暗号文は見つからないよ', 404)
    allowed = conn.execute("""SELECT 1 FROM qz_cipher_members
                              WHERE key_id=? AND (staff_id=? OR staff_id='*')""",
                           (row[1], sid)).fetchone()
    conn.close()
    if not allowed:
        return err('このキーを使う権限がないよ', 403)
    return ok(plain=dec(row[0]))

@bp.route('/api/staff/messages/<int:message_id>/hide', methods=['POST'])
def api_staff_msg_hide(message_id):
    # 削除: 自分の画面からだけ消す(削除歴なし)。複数人が別々に削除できるよう,をつけて追記
    sid = session.get('staff_id', '')
    if not sid:
        return err('ログインしてね', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT hidden_by FROM qz_messages WHERE id=?', (message_id,)).fetchone()
    if not row:
        conn.close()
        return err('そのメッセージはないよ')
    hidden = row[0] or ''
    if (',' + hidden + ',').find(',' + sid + ',') == -1:  # まだ隠してなければ追記
        hidden = (hidden + ',' + sid).strip(',')
        conn.execute('UPDATE qz_messages SET hidden_by=? WHERE id=?', (hidden, message_id))
        conn.commit()
    conn.close()
    return ok(hidden=True)

@bp.route('/api/staff/messages/<int:message_id>/unsend', methods=['POST'])
def api_staff_msg_unsend(message_id):
    # 送信取り消し: 全員の画面から消えて「取り消されました」ログが残る
    sid = session.get('staff_id', '')
    if not sid:
        return err('ログインしてね', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT staff_id, unsent FROM qz_messages WHERE id=?', (message_id,)).fetchone()
    if not row:
        conn.close()
        return err('そのメッセージはないよ')
    if str(row[0]).startswith('line_'):
        conn.close()
        return err('LINEから届いたメッセージは取り消せないよ', 403)  # 仕様③
    if row[0] != sid:
        conn.close()
        return err('自分が送ったメッセージしか取り消せないよ', 403)  # 仕様④
    if row[1]:
        conn.close()
        return err('もう取り消し済みだよ')  # ログは二重取り消し不可
    # 本文・画像・ファイル・スタンプを消して、取り消しログに変える
    conn.execute("UPDATE qz_messages SET body='', title='', image_data=NULL, file_data=NULL, file_name=NULL, stamp_id=NULL, unsent=1 WHERE id=?", (message_id,))
    conn.commit(); conn.close()
    return ok(unsent=True)

@bp.route('/staff/board')
def page_staff_board():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_board.html', staff_name=session.get('staff_name'), staff_id=session.get('staff_id'), is_admin=staff_is_admin())

@bp.route('/api/staff/logout', methods=['POST'])
def api_staff_logout():
    session.pop('staff_id', None)
    session.pop('staff_name', None)
    return ok(message='ログアウトしました')

@bp.route('/api/staff/messages', methods=['GET'])
def api_staff_messages_get():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    channel_id = request.args.get('channel_id', '1')
    limit = min(200, max(1, int(request.args.get('limit') or 30)))
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    # 重いデータ(画像・ファイルの中身)は返さず「有無」だけ返す。中身は別URLで配る
    _sid = session.get('staff_id', '')
    rows = conn.execute(
        'SELECT id, staff_name, title, body, created_at, stamp_id, is_system, reply_to, '
        '(image_data IS NOT NULL) AS has_image, (file_data IS NOT NULL) AS has_file, file_name, '
        'unsent, staff_id '
        "FROM qz_messages WHERE channel_id=? AND (',' || COALESCE(hidden_by,'') || ',') NOT LIKE ? "
        'ORDER BY id DESC LIMIT ?', (channel_id, '%,' + _sid + ',%', limit + 1)).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    my_staff_id = session.get('staff_id')
    ids = [r[0] for r in rows]
    # リアクションは1回のSQLでまとめて取る(前は1件ごとで遅かった)
    reactions_map = {}
    if ids:
        marks = ','.join(['?'] * len(ids))
        for mid, emoji, sid in conn.execute('SELECT message_id, emoji, staff_id FROM qz_reactions WHERE message_id IN (' + marks + ')', ids).fetchall():
            summary = reactions_map.setdefault(mid, {})
            info = summary.setdefault(emoji, {'count': 0, 'mine': False})
            info['count'] += 1
            if sid == my_staff_id:
                info['mine'] = True
    # 返信プレビューもまとめて取る
    reply_ids = [r[7] for r in rows if r[7]]
    reply_map = {}
    if reply_ids:
        marks = ','.join(['?'] * len(reply_ids))
        for rid, rname, rbody in conn.execute('SELECT id, staff_name, body FROM qz_messages WHERE id IN (' + marks + ')', reply_ids).fetchall():
            reply_map[rid] = {'staff_name': dec(rname or ''), 'body': (dec(rbody or '') or '')[:60]}
    # 暗号メッセージの情報をまとめて取る(本文はブラウザに渡さない=覗いても読めない)
    cipher_map = {}
    if ids:
        marks = ','.join(['?'] * len(ids))
        for mid, ckey in conn.execute('SELECT id, cipher_key_id FROM qz_messages WHERE id IN (' + marks + ') AND cipher_key_id IS NOT NULL', ids).fetchall():
            cipher_map[mid] = ckey
    cipher_names = {}
    if cipher_map:
        for kid, kname in conn.execute('SELECT id, key_name FROM qz_cipher_keys').fetchall():
            cipher_names[kid] = kname
    # 既読情報を取得
    read_map = {}
    if ids:
        _rm = ','.join(['?'] * len(ids))
        for _mid, _sn in conn.execute(f'SELECT message_id, staff_name FROM qz_message_reads WHERE message_id IN ({_rm})', ids).fetchall():
            read_map.setdefault(_mid, []).append(_sn)
    messages = []
    for r in rows:
        is_cipher = r[0] in cipher_map
        messages.append({
            'id': r[0], 'staff_name': dec(r[1]), 'title': dec(r[2]),
            'body': '' if (is_cipher or r[11]) else dec(r[3]),
            'is_cipher': is_cipher,
            'cipher_key_name': cipher_names.get(cipher_map.get(r[0]), '暗号') if is_cipher else None,
            'created_at': r[4], 'stamp_id': r[5],
            'is_system': bool(r[6]),
            'reply_preview': reply_map.get(r[7]) if r[7] else None,
            'has_image': bool(r[8]), 'has_file': bool(r[9]),
            'file_name': dec(r[10]) if r[10] else None,
            'read_by': read_map.get(r[0], []),
            'reactions': reactions_map.get(r[0], {}),
            'unsent': int(r[11] or 0),
            'staff_id': r[12],
            'is_line': str(r[12] or '').startswith('line_'),
        })
    conn.close()
    return ok(messages=messages, has_more=has_more)

@bp.route('/api/staff/messages', methods=['POST'])
def api_staff_messages_post():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    if not rate_limit('board_post:' + session.get('staff_id',''), 20):
        return err('投稿が速すぎるよ。少し待ってね')
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    body = (data.get('body') or '').strip()
    image_data = data.get('image_data')
    stamp_id = (data.get('stamp_id') or '').strip()
    file_data = data.get('file_data')
    file_name = data.get('file_name', '')
    if not body and not image_data and not stamp_id and not file_data:
        return err('内容を入力してね')
    # LINE連携チャンネルは画像・ファイル・スタンプ禁止(LINE側に届かず片方しか見えなくなるため)
    if str(data.get('channel_id', 1)) == '22' and (image_data or file_data or stamp_id):
        return err('添付できませんでした')
    if image_data and len(image_data) > 6_000_000:
        return err('画像が大きすぎるよ')
    if file_data and len(file_data) > 8_000_000:
        return err('ファイルが大きすぎるよ(5MBまで)')
    reply_to = data.get('reply_to')
    cipher_key_id = data.get('cipher_key_id')  # 暗号キー(なければ普通の投稿)
    if cipher_key_id:
        my_keys = [k['id'] for k in _my_cipher_keys()]
        if int(cipher_key_id) not in my_keys:
            return err('そのキーで送る権限がないよ', 403)
        cipher_key_id = int(cipher_key_id)
    else:
        cipher_key_id = None
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    channel_id = data.get('channel_id', 1)
    import pytz as _pytz_jst
    from datetime import datetime as _dt_jst
    _jst_now_str = _dt_jst.now(_pytz_jst.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('INSERT INTO qz_messages (staff_id, staff_name, title, body, image_data, stamp_id, channel_id, file_data, file_name, reply_to, created_at, cipher_key_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                 (session.get('staff_id'), enc(session.get('staff_name')), enc(title), enc(body), image_data, stamp_id, channel_id, file_data, enc(file_name) if file_name else None, reply_to, _jst_now_str, cipher_key_id))
    conn.commit()
    # LINE転送(チャンネル22の投稿をLINEグループへ)
    if str(channel_id) == '22':
        try:
            line_send_to_group(session.get('staff_name', '') + ': ' + (body or title or '(スタンプ)'))
        except Exception as _e:
            print('[LINE転送エラー]', _e)
    conn.close()
    return ok(message='投稿しました')

def _docx_to_html(data_url):
    # Wordファイル(.docx)の中身をHTMLに変換してプレビューできるようにする
    try:
        import mammoth, base64 as _b64, io
        raw = _b64.b64decode(data_url.split(',', 1)[1])
        result = mammoth.convert_to_html(io.BytesIO(raw))
        return ('<div style="background:white; border:1px solid #e3ddd0; border-radius:10px; '
                'padding:24px; line-height:1.8; color:#1a1a1a;">' + result.value + '</div>')
    except Exception as e:
        return ('<p style="color:#8a8270;">Wordのプレビューに失敗したよ(' + str(e)[:80] +
                ')。ダウンロードして開いてね。</p>')

def _preview_body(mime, fname, blob_url, data_url):
    # ファイルの種類に合わせてプレビューのHTMLを作る(共通部品)
    from markupsafe import escape
    if mime.startswith('image/'):
        return '<img src="' + blob_url + '" style="max-width:100%; border-radius:10px;">'
    if mime == 'application/pdf' or mime.startswith('text/'):
        return ('<iframe src="' + blob_url + '" style="width:100%; height:80vh; '
                'border:1px solid #e3ddd0; border-radius:10px; background:white;"></iframe>')
    if mime.startswith('audio/'):
        return '<audio controls src="' + blob_url + '" style="width:100%;"></audio>'
    if mime.startswith('video/'):
        return '<video controls src="' + blob_url + '" style="max-width:100%; border-radius:10px;"></video>'
    if 'wordprocessingml' in mime or fname.lower().endswith('.docx'):
        return _docx_to_html(data_url)
    return ('<p style="color:#8a8270;">この形式(' + str(escape(mime or '不明')) +
            ')はブラウザでプレビューできないよ。「ダウンロード」ボタンで保存して開いてね。</p>')

@bp.route('/api/staff/files/<int:file_id>/blob', methods=['GET'])
def api_staff_file_blob(file_id):
    # ファイル置き場のファイルの中身を返す(?dl=1でダウンロード)
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq, base64 as _b64
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT file_name, file_data FROM qz_files WHERE id=?', (file_id,)).fetchone()
    conn.close()
    if not row or not row[1]:
        return err('見つからないよ', 404)
    try:
        header, b64 = row[1].split(',', 1)
        mime = header.split(':', 1)[1].split(';', 1)[0]
        raw = _b64.b64decode(b64)
    except Exception:
        return err('データの形式がおかしいよ', 500)
    from flask import Response
    from urllib.parse import quote as _quote
    resp = Response(raw, mimetype=mime or 'application/octet-stream')
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    fname = dec(row[0]) if row[0] else 'file'
    disp = 'attachment' if request.args.get('dl') else 'inline'
    resp.headers['Content-Disposition'] = disp + "; filename*=UTF-8''" + _quote(fname)
    return resp

@bp.route('/staff/files/view/<int:file_id>')
def page_staff_file_view_storage(file_id):
    # ファイル置き場の専用プレビューページ
    if not session.get('staff_id'):
        return redirect('/staff/login')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT file_name, file_data FROM qz_files WHERE id=?', (file_id,)).fetchone()
    conn.close()
    if not row or not row[1]:
        return 'ファイルが見つからないよ', 404
    from markupsafe import escape
    fname = dec(row[0]) if row[0] else 'ファイル'
    mime = ''
    try:
        mime = row[1].split(':', 1)[1].split(';', 1)[0]
    except Exception:
        pass
    blob_url = '/api/staff/files/' + str(file_id) + '/blob'
    body = _preview_body(mime, fname, blob_url, row[1])
    return ('<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + str(escape(fname)) + '</title></head>'
            '<body style="font-family:sans-serif; background:#fdfbf6; padding:20px; max-width:900px; margin:0 auto;">'
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:8px;">'
            '<h3 style="margin:0; color:#14213d; word-break:break-all;">🗂️ ' + str(escape(fname)) + '</h3>'
            '<span style="flex-shrink:0;">'
            '<a href="' + blob_url + '?dl=1" style="background:#14213d; color:white; padding:8px 14px; border-radius:8px; text-decoration:none; font-size:13px;">⬇ ダウンロード</a> '
            '<a href="/staff/files" style="background:#e3ddd0; color:#14213d; padding:8px 14px; border-radius:8px; text-decoration:none; font-size:13px;">← ファイルページへ</a>'
            '</span></div>'
            + body + '</body></html>')

@bp.route('/staff/file/<int:message_id>')
def page_staff_file_view(message_id):
    # 添付ファイルの専用プレビューページ
    if not session.get('staff_id'):
        return redirect('/staff/login')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT file_name, file_data FROM qz_messages WHERE id=?', (message_id,)).fetchone()
    conn.close()
    if not row or not row[1]:
        return 'ファイルが見つからないよ', 404
    from markupsafe import escape
    fname = dec(row[0]) if row[0] else 'ファイル'
    # data:image/png;base64,... の形式からファイルの種類を取り出す
    mime = ''
    try:
        mime = row[1].split(':', 1)[1].split(';', 1)[0]
    except Exception:
        pass
    blob_url = '/api/staff/messages/' + str(message_id) + '/blob?kind=file'
    # 種類ごとのプレビューは共通部品(_preview_body)に任せる。Wordにも対応!
    body = _preview_body(mime, fname, blob_url, row[1])
    return ('<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + str(escape(fname)) + ' - Q\'z</title></head>'
            '<body style="font-family:sans-serif; background:#fdfbf6; padding:20px; max-width:900px; margin:0 auto;">'
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:8px;">'
            '<h3 style="margin:0; color:#14213d; word-break:break-all;">🗂️ ' + str(escape(fname)) + '</h3>'
            '<span style="flex-shrink:0;">'
            '<a href="' + blob_url + '&dl=1" style="background:#14213d; color:white; padding:8px 14px; border-radius:8px; text-decoration:none; font-size:13px;">⬇ ダウンロード</a> '
            '<a href="/staff/board" style="background:#e3ddd0; color:#14213d; padding:8px 14px; border-radius:8px; text-decoration:none; font-size:13px;">← 掲示板へ</a>'
            '</span></div>'
            + body + '</body></html>')

@bp.route('/api/staff/messages/<int:message_id>/blob', methods=['GET'])
def api_staff_message_blob(message_id):
    # 画像やファイルの中身だけを返す(ブラウザがキャッシュできるから2回目以降は一瞬)
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    kind = request.args.get('kind', 'image')
    col = 'file_data' if kind == 'file' else 'image_data'
    import sqlite3 as _sq, base64 as _b64
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT ' + col + ', file_name FROM qz_messages WHERE id=?', (message_id,)).fetchone()
    conn.close()
    if not row or not row[0]:
        return err('データが見つからないよ', 404)
    # 「data:image/png;base64,〜」形式を分解して本物のデータに戻す
    try:
        header, b64 = row[0].split(',', 1)
        mime = header.split(':', 1)[1].split(';', 1)[0]
        raw = _b64.b64decode(b64)
    except Exception:
        return err('データの形式がおかしいよ', 500)
    from flask import Response
    resp = Response(raw, mimetype=mime or 'application/octet-stream')
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    if kind == 'file':
        from urllib.parse import quote as _quote
        fname = dec(row[1]) if row[1] else 'file'
        # ?dl=1がついていたらダウンロード、なければブラウザで表示
        disp = 'attachment' if request.args.get('dl') else 'inline'
        resp.headers['Content-Disposition'] = disp + "; filename*=UTF-8''" + _quote(fname)
    return resp

@bp.route('/api/staff/reactions', methods=['POST'])
def api_staff_reaction_toggle():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    message_id = data.get('message_id')
    emoji = (data.get('emoji') or '').strip()
    if not message_id or emoji not in ['👍','❤️','😂','😮','👏']:
        return err('不正なリクエストだよ')
    staff_id = session.get('staff_id')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    existing = conn.execute('SELECT id FROM qz_reactions WHERE message_id=? AND staff_id=? AND emoji=?',
                            (message_id, staff_id, emoji)).fetchone()
    if existing:
        conn.execute('DELETE FROM qz_reactions WHERE id=?', (existing[0],))
        action = 'removed'
    else:
        conn.execute('INSERT INTO qz_reactions (message_id, staff_id, emoji) VALUES (?,?,?)', (message_id, staff_id, emoji))
        action = 'added'
    conn.commit()
    conn.close()
    return ok(action=action)

@bp.route('/api/staff/channels', methods=['GET'])
def api_staff_channels_get():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    my_id = session.get('staff_id')
    import sqlite3 as _sq, json as _json
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, channel_type, name, members, is_public FROM qz_channels ORDER BY id').fetchall()
    joined = []
    available = []
    for r in rows:
        members = _json.loads(r[3] or '[]')
        is_member = my_id in members
        if r[1] == 'dm':
            if not is_member:
                continue
            other_staff = [m for m in members if m != my_id]
            display_name = r[2]
            if other_staff:
                other_row = conn.execute('SELECT name FROM qz_staff WHERE staff_id=?', (other_staff[0],)).fetchone()
                if other_row:
                    display_name = dec(other_row[0])
            last_msg = conn.execute('SELECT body, stamp_id, image_data, created_at FROM qz_messages WHERE channel_id=? ORDER BY id DESC LIMIT 1', (r[0],)).fetchone()
            preview = ''
            if last_msg:
                if last_msg[0]: preview = dec(last_msg[0])[:30]
                elif last_msg[1]: preview = '🎨 スタンプ'
                elif last_msg[2]: preview = '📷 画像'
            unread_row = conn.execute('SELECT last_read_msg_id FROM qz_read_status WHERE channel_id=? AND staff_id=?', (r[0], my_id)).fetchone()
            last_read = unread_row[0] if unread_row else 0
            unread_count = conn.execute('SELECT COUNT(*) FROM qz_messages WHERE channel_id=? AND id>? AND staff_id!=?', (r[0], last_read, my_id)).fetchone()[0]
            joined.append({'id': r[0], 'type': r[1], 'name': display_name, 'preview': preview, 'last_time': last_msg[3] if last_msg else None, 'unread': unread_count})
        else:
            last_msg = conn.execute('SELECT body, stamp_id, image_data, created_at FROM qz_messages WHERE channel_id=? ORDER BY id DESC LIMIT 1', (r[0],)).fetchone()
            preview = ''
            if last_msg:
                if last_msg[0]: preview = dec(last_msg[0])[:30]
                elif last_msg[1]: preview = '🎨 スタンプ'
                elif last_msg[2]: preview = '📷 画像'
            unread_row = conn.execute('SELECT last_read_msg_id FROM qz_read_status WHERE channel_id=? AND staff_id=?', (r[0], my_id)).fetchone()
            last_read = unread_row[0] if unread_row else 0
            unread_count = conn.execute('SELECT COUNT(*) FROM qz_messages WHERE channel_id=? AND id>? AND staff_id!=?', (r[0], last_read, my_id)).fetchone()[0]
            item = {'id': r[0], 'type': r[1], 'name': r[2], 'preview': preview, 'last_time': last_msg[3] if last_msg else None, 'member_count': len(members), 'unread': unread_count}
            if is_member:
                joined.append(item)
            elif len(r) > 4 and r[4] == 1:
                # 公開グループのみ未参加者に表示
                available.append(item)
    conn.close()
    return ok(channels=joined, available_groups=available)

@bp.route('/api/staff/channels/join', methods=['POST'])
def api_staff_channel_join():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    channel_id = data.get('channel_id')
    my_id = session.get('staff_id')
    my_name = session.get('staff_name')
    import sqlite3 as _sq, json as _json
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT members, channel_type FROM qz_channels WHERE id=?', (channel_id,)).fetchone()
    if not row:
        conn.close()
        return err('チャンネルが見つからないよ', 404)
    if row[1] != 'group':
        conn.close()
        return err('グループチャンネルだけ参加できるよ')
    members = _json.loads(row[0] or '[]')
    if my_id not in members:
        members.append(my_id)
        conn.execute('UPDATE qz_channels SET members=? WHERE id=?', (_json.dumps(members), channel_id))
        import pytz as _pytz_jst
        from datetime import datetime as _dt_jst
        _jst_now_str = _dt_jst.now(_pytz_jst.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('INSERT INTO qz_messages (staff_id, staff_name, title, body, channel_id, is_system, created_at) VALUES (?,?,?,?,?,1,?)',
                     (my_id, enc(my_name), '', enc(my_name + 'さんが参加しました'), channel_id, _jst_now_str))
        conn.commit()
    conn.close()
    return ok(message='参加しました')

@bp.route('/api/staff/channels/leave', methods=['POST'])
def api_staff_channel_leave():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    channel_id = data.get('channel_id')
    if str(channel_id) == '22':
        return err('このチャンネルからは退出できないよ(LINE連携チャンネル)')
    my_id = session.get('staff_id')
    my_name = session.get('staff_name')
    import sqlite3 as _sq, json as _json
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT members, channel_type FROM qz_channels WHERE id=?', (channel_id,)).fetchone()
    if not row:
        conn.close()
        return err('チャンネルが見つからないよ', 404)
    if row[1] != 'group':
        conn.close()
        return err('グループチャンネルだけ退出できるよ')
    members = _json.loads(row[0] or '[]')
    disbanded = False
    if my_id in members:
        members.remove(my_id)
        import pytz as _pytz_jst
        from datetime import datetime as _dt_jst
        _jst_now_str = _dt_jst.now(_pytz_jst.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('INSERT INTO qz_messages (staff_id, staff_name, title, body, channel_id, is_system, created_at) VALUES (?,?,?,?,?,1,?)',
                     (my_id, enc(my_name), '', enc(my_name + 'さんが退出しました'), channel_id, _jst_now_str))
        if len(members) <= 1:
            # 残り1人以下なら自動解散
            conn.execute('DELETE FROM qz_messages WHERE channel_id=?', (channel_id,))
            conn.execute('DELETE FROM qz_channels WHERE id=?', (channel_id,))
            disbanded = True
        else:
            conn.execute('UPDATE qz_channels SET members=? WHERE id=?', (_json.dumps(members), channel_id))
        conn.commit()
    conn.close()
    return ok(message='退出しました', disbanded=disbanded)

@bp.route('/api/staff/list', methods=['GET'])
def api_staff_list():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    my_id = session.get('staff_id')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT staff_id, name, status, active_from FROM qz_staff WHERE staff_id != ?', (my_id,)).fetchall()
    conn.close()
    # 在籍中の人だけリストに出す(退社済み・承認待ち・入社前は出さない)
    return ok(staff=[{'staff_id': r[0], 'name': dec(r[1])} for r in rows if staff_can_chat(r[2], r[3])])

@bp.route('/api/staff/channels', methods=['POST'])
def api_staff_channels_create():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    channel_type = data.get('type', 'group')
    name = (data.get('name') or '').strip()
    member_id = (data.get('member_id') or '').strip()
    import sqlite3 as _sq, json as _json
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    if channel_type == 'group':
        if not name:
            conn.close()
            return err('チャンネル名を入力してね')
        is_public = 1 if data.get('is_public', True) else 0
        my_id = session.get('staff_id')
        conn.execute("INSERT INTO qz_channels (channel_type, name, members, is_public) VALUES ('group', ?, ?, ?)",
                     (name, _json.dumps([my_id]), is_public))
        conn.commit()
        new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    else:
        if not member_id:
            conn.close()
            return err('相手を選んでね')
        target = conn.execute('SELECT staff_id, status, active_from FROM qz_staff WHERE staff_id=?', (member_id,)).fetchone()
        if not target:
            conn.close()
            return err('その社員は見つからないよ')
        if not staff_can_chat(target[1], target[2]):
            conn.close()
            return err('その人は今は在籍していないよ(退社済みか入社前)')
        my_id = session.get('staff_id')
        members = sorted([my_id, member_id])
        existing = conn.execute("SELECT id FROM qz_channels WHERE channel_type='dm' AND members=?", (_json.dumps(members),)).fetchone()
        if existing:
            conn.close()
            return ok(channel_id=existing[0], already_exists=True)
        conn.execute("INSERT INTO qz_channels (channel_type, name, members) VALUES ('dm', ?, ?)", (member_id, _json.dumps(members)))
        conn.commit()
        new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.close()
    return ok(channel_id=new_id)

@bp.route('/api/staff/channels/invite', methods=['POST'])
def api_staff_channel_invite():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    channel_id = data.get('channel_id')
    invite_id = (data.get('staff_id') or '').strip()
    my_id = session.get('staff_id')
    import sqlite3 as _sq, json as _json
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT members, channel_type FROM qz_channels WHERE id=?', (channel_id,)).fetchone()
    if not row:
        conn.close()
        return err('チャンネルが見つからないよ', 404)
    if row[1] != 'group':
        conn.close()
        return err('グループだけ招待できるよ')
    members = _json.loads(row[0] or '[]')
    if my_id not in members:
        conn.close()
        return err('参加していないグループには招待できないよ', 403)
    target = conn.execute('SELECT staff_id, status, active_from FROM qz_staff WHERE staff_id=?', (invite_id,)).fetchone()
    if target and not staff_can_chat(target[1], target[2]):
        conn.close()
        return err('その人は今は在籍していないよ(退社済みか入社前)')
    if not target:
        conn.close()
        return err('その社員は見つからないよ')
    if invite_id not in members:
        members.append(invite_id)
        conn.execute('UPDATE qz_channels SET members=? WHERE id=?', (_json.dumps(members), channel_id))
        conn.commit()
    conn.close()
    return ok(message='招待しました')

@bp.route('/api/staff/channels/members', methods=['GET'])
def api_staff_channel_members():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    channel_id = request.args.get('channel_id')
    import sqlite3 as _sq, json as _json
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT members FROM qz_channels WHERE id=?', (channel_id,)).fetchone()
    if not row:
        conn.close()
        return err('見つからないよ', 404)
    members = _json.loads(row[0] or '[]')
    result = []
    for m in members:
        srow = conn.execute('SELECT name FROM qz_staff WHERE staff_id=?', (m,)).fetchone()
        result.append({'staff_id': m, 'name': dec(srow[0]) if srow else m})
    conn.close()
    return ok(members=result)

@bp.route('/staff/profile')
def page_staff_profile():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_profile.html', staff_id=session.get('staff_id'), staff_name=session.get('staff_name'))

@bp.route('/api/staff/profile/update', methods=['POST'])
def api_staff_profile_update():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    new_id = (data.get('staff_id') or '').strip()
    new_name = (data.get('name') or '').strip()
    new_password = data.get('password', '')
    current_id = session.get('staff_id')
    if not new_id or not new_name:
        return err('IDと名前を入力してね')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    if new_id != current_id:
        existing = conn.execute('SELECT id FROM qz_staff WHERE staff_id=?', (new_id,)).fetchone()
        if existing:
            conn.close()
            return err('そのIDは既に使われているよ')
    if new_password:
        pw_hash = hash_password(new_password)
        conn.execute('UPDATE qz_staff SET staff_id=?, name=?, password_hash=? WHERE staff_id=?',
                     (new_id, enc(new_name), pw_hash, current_id))
    else:
        conn.execute('UPDATE qz_staff SET staff_id=?, name=? WHERE staff_id=?',
                     (new_id, enc(new_name), current_id))
    if new_id != current_id:
        # 関連テーブルの古いIDも書き換える
        conn.execute('UPDATE qz_messages SET staff_id=? WHERE staff_id=?', (new_id, current_id))
        conn.execute('UPDATE qz_reactions SET staff_id=? WHERE staff_id=?', (new_id, current_id))
        import json as _json
        ch_rows = conn.execute('SELECT id, members FROM qz_channels').fetchall()
        for ch_id, members_json in ch_rows:
            members = _json.loads(members_json or '[]')
            if current_id in members:
                members = [new_id if m == current_id else m for m in members]
                conn.execute('UPDATE qz_channels SET members=? WHERE id=?', (_json.dumps(members), ch_id))
    conn.commit()
    conn.close()
    session['staff_id'] = new_id
    session['staff_name'] = new_name
    return ok(message='更新しました')

@bp.route('/api/staff/channels/read', methods=['POST'])
def api_staff_channel_read():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    channel_id = data.get('channel_id')
    my_id = session.get('staff_id')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    last_msg = conn.execute('SELECT MAX(id) FROM qz_messages WHERE channel_id=?', (channel_id,)).fetchone()[0] or 0
    conn.execute('INSERT INTO qz_read_status (channel_id, staff_id, last_read_msg_id) VALUES (?,?,?) ON CONFLICT(channel_id, staff_id) DO UPDATE SET last_read_msg_id=?',
                 (channel_id, my_id, last_msg, last_msg))
    conn.commit()
    conn.close()
    return ok()

@bp.route('/staff/files')
def page_staff_files():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_files.html', staff_name=session.get('staff_name'))

@bp.route('/api/staff/files', methods=['GET'])
def api_staff_files_get():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, staff_name, file_name, description, created_at FROM qz_files ORDER BY id DESC').fetchall()
    conn.close()
    return ok(files=[{'id':r[0],'staff_name':dec(r[1]),'file_name':dec(r[2]),'description':dec(r[3]),'created_at':r[4]} for r in rows])

@bp.route('/api/staff/files', methods=['POST'])
def api_staff_files_post():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    file_name = (data.get('file_name') or '').strip()
    file_data = data.get('file_data')
    description = (data.get('description') or '').strip()
    if not file_name or not file_data:
        return err('ファイルを選んでね')
    if len(file_data) > 18_000_000:
        return err('ファイルが大きすぎるよ（15MBまで）')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    import pytz as _pytz_jst
    from datetime import datetime as _dt_jst
    _jst_now_str = _dt_jst.now(_pytz_jst.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('INSERT INTO qz_files (staff_id, staff_name, file_name, file_data, description, created_at) VALUES (?,?,?,?,?,?)',
                 (session.get('staff_id'), enc(session.get('staff_name')), enc(file_name), file_data, enc(description), _jst_now_str))
    conn.commit()
    conn.close()
    return ok(message='アップロードしました')

@bp.route('/api/staff/files/<int:file_id>', methods=['GET'])
def api_staff_file_download(file_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT file_name, file_data FROM qz_files WHERE id=?', (file_id,)).fetchone()
    conn.close()
    if not row:
        return err('見つからないよ', 404)
    return ok(file_name=dec(row[0]), file_data=row[1])

@bp.route('/api/staff/files/<int:file_id>/delete', methods=['POST'])
def api_staff_file_delete(file_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM qz_files WHERE id=?', (file_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/staff/calendar')
def page_staff_calendar():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_calendar.html', staff_name=session.get('staff_name'))

@bp.route('/api/staff/events', methods=['GET'])
def api_staff_events_get():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, staff_name, title, description, event_date, event_time, color FROM qz_events ORDER BY event_date').fetchall()
    conn.close()
    return ok(events=[{'id':r[0],'staff_name':dec(r[1]),'title':dec(r[2]),'description':dec(r[3]),'date':r[4],'time':r[5],'color':r[6]} for r in rows])

@bp.route('/api/staff/events', methods=['POST'])
def api_staff_events_post():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    event_date = (data.get('date') or '').strip()
    event_time = (data.get('time') or '').strip()
    color = data.get('color', '#fb6f5b')
    if not title or not event_date:
        return err('タイトルと日付を入力してね')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    import pytz as _pytz_jst
    from datetime import datetime as _dt_jst
    _jst_now_str = _dt_jst.now(_pytz_jst.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('INSERT INTO qz_events (staff_id, staff_name, title, description, event_date, event_time, color, created_at) VALUES (?,?,?,?,?,?,?,?)',
                 (session.get('staff_id'), enc(session.get('staff_name')), enc(title), enc(description), event_date, event_time, color, _jst_now_str))
    conn.commit()
    conn.close()
    return ok(message='追加しました')

@bp.route('/api/staff/events/<int:event_id>/delete', methods=['POST'])
def api_staff_events_delete(event_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM qz_events WHERE id=?', (event_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/staff/tasks')
def page_staff_tasks():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_tasks.html', staff_name=session.get('staff_name'), my_id=session.get('staff_id'))

@bp.route('/api/staff/tasks', methods=['GET'])
def api_staff_tasks_get():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, staff_name, title, description, assignee_id, due_date, status, created_at FROM qz_tasks ORDER BY id DESC').fetchall()
    result = []
    for r in rows:
        assignee_name = ''
        if r[4]:
            ar = conn.execute('SELECT name FROM qz_staff WHERE staff_id=?', (r[4],)).fetchone()
            assignee_name = dec(ar[0]) if ar else r[4]
        result.append({'id':r[0],'staff_name':dec(r[1]),'title':dec(r[2]),'description':dec(r[3]),'assignee_id':r[4],'assignee_name':assignee_name,'due_date':r[5],'status':r[6],'created_at':r[7]})
    conn.close()
    return ok(tasks=result)

@bp.route('/api/staff/tasks', methods=['POST'])
def api_staff_tasks_post():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    assignee_id = data.get('assignee_id') or None
    due_date = data.get('due_date') or None
    if not title:
        return err('タスク名を入力してね')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    import pytz as _pytz_jst
    from datetime import datetime as _dt_jst
    _jst_now_str = _dt_jst.now(_pytz_jst.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('INSERT INTO qz_tasks (staff_id, staff_name, title, description, assignee_id, due_date, created_at) VALUES (?,?,?,?,?,?,?)',
                 (session.get('staff_id'), enc(session.get('staff_name')), enc(title), enc(description), assignee_id, due_date, _jst_now_str))
    conn.commit()
    conn.close()
    return ok(message='追加しました')

@bp.route('/api/staff/tasks/<int:task_id>/status', methods=['POST'])
def api_staff_tasks_status(task_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    status = data.get('status', 'todo')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE qz_tasks SET status=? WHERE id=?', (status, task_id))
    conn.commit()
    conn.close()
    return ok(message='更新しました')

@bp.route('/api/staff/tasks/<int:task_id>/delete', methods=['POST'])
def api_staff_tasks_delete(task_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM qz_tasks WHERE id=?', (task_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/staff/dashboard')
def page_staff_dashboard():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_dashboard.html', staff_name=session.get('staff_name'))

@bp.route('/api/staff/dashboard', methods=['GET'])
def api_staff_dashboard():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    total_groups = conn.execute('SELECT COUNT(*) FROM groups').fetchone()[0]
    total_quizzes = conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0]
    total_attempts = conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
    total_events = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    school_groups = conn.execute('SELECT COUNT(*) FROM groups WHERE school_mode=1').fetchone()[0]
    official_groups = conn.execute('SELECT COUNT(*) FROM groups WHERE is_official=1').fetchone()[0]
    recent_groups = conn.execute("SELECT name, datetime(created_at, '+9 hours') FROM groups ORDER BY created_at DESC LIMIT 5").fetchall()
    import pytz as _pytz_dash
    from datetime import datetime as _dt_dash
    today_jst = _dt_dash.now(_pytz_dash.timezone('Asia/Tokyo')).strftime('%Y-%m-%d')
    today_attempts = conn.execute('SELECT COUNT(*) FROM attempts WHERE date(created_at)=?', (today_jst,)).fetchone()[0]
    recent_quizzes = conn.execute("SELECT question, datetime(created_at, '+9 hours') FROM quizzes ORDER BY created_at DESC LIMIT 5").fetchall()
    recent_attempts = conn.execute('''SELECT q.question, datetime(a.created_at, '+9 hours') FROM attempts a
        JOIN quizzes q ON a.quiz_id = q.id ORDER BY a.created_at DESC LIMIT 5''').fetchall()
    conn.close()
    return ok(
        total_groups=total_groups,
        total_quizzes=total_quizzes,
        total_attempts=total_attempts,
        total_events=total_events,
        school_groups=school_groups,
        official_groups=official_groups,
        today_attempts=today_attempts,
        recent_groups=[{'name': dec(r[0]), 'created_at': r[1]} for r in recent_groups],
        recent_quizzes=[{'question': dec(r[0])[:40], 'created_at': r[1]} for r in recent_quizzes],
        recent_attempts=[{'question': dec(r[0])[:40], 'created_at': r[1]} for r in recent_attempts],
    )
