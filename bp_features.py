# ====================================================================
# bp_features.py: 学校・先生・課題・イベント・対戦・タイピング
# app.pyから引っ越し。@app.route→@bp.routeに変えただけ。
# ====================================================================
import os
import json
from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify

from qz_common import (
    enc, dec, ok, err, rate_limit, client_ip,
    get_db, make_cursor, q, new_id, hash_password, verify_password,
    hash_group_id, current_group, admin_logged_in_for,
    normalize_answer, check_answer, smart_check, USE_POSTGRES,
)

bp = Blueprint('features', __name__)

# ===== 学校モード =====

@bp.route('/api/admin/school-mode', methods=['POST'])
def api_admin_school_mode():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    pw = data.get('password', '')
    group_id = data.get('group_id', '')
    enabled = 1 if data.get('enabled') else 0
    if not pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('UPDATE groups SET school_mode = %s WHERE id = %s'), (enabled, group_id))
    return ok()

@bp.route('/api/group/access-logs')
def api_access_logs():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT nickname, device, ip_hash, created_at FROM access_logs WHERE group_id = %s ORDER BY created_at DESC LIMIT 100'), (grp['id'],))
        rows = [dict(r) for r in cur.fetchall()]
    return ok(logs=rows)


# ===== カスタムページ管理 =====

@bp.route('/api/custom_pages', methods=['GET'])
def api_get_custom_pages():
    pw = request.args.get('pw', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id,page_key,page_name,url,template_type,content,show_on_home,is_published FROM custom_pages ORDER BY id').fetchall()
    conn.close()
    return ok(pages=[{'id':r[0],'key':r[1],'name':r[2],'url':r[3],'template_type':r[4],'content':json.loads(r[5]),'show_on_home':bool(r[6]),'is_published':bool(r[7])} for r in rows])

@bp.route('/api/custom_pages', methods=['POST'])
def api_create_custom_page():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    page_key = (data.get('page_key') or '').strip()
    page_name = (data.get('page_name') or '').strip()
    url = (data.get('url') or '').strip()
    template_type = (data.get('template_type') or 'blank').strip()
    content = data.get('content') or {}
    show_on_home = 1 if data.get('show_on_home') else 0
    if not page_key or not page_name or not url:
        return err('key・name・urlは必須だよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    try:
        conn.execute('INSERT INTO custom_pages (page_key,page_name,url,template_type,content,show_on_home) VALUES (?,?,?,?,?,?)',
                     (page_key, page_name, url, template_type, json.dumps(content, ensure_ascii=False), show_on_home))
        conn.commit()
    except Exception as e:
        conn.close()
        return err(str(e))
    conn.close()
    return ok(message='作成しました')

@bp.route('/api/custom_pages/<page_key>', methods=['POST'])
def api_update_custom_page(page_key):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    content = data.get('content') or {}
    page_name = (data.get('page_name') or '').strip()
    show_on_home = 1 if data.get('show_on_home') else 0
    is_published = 1 if data.get('is_published', True) else 0
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE custom_pages SET content=?,page_name=?,show_on_home=?,is_published=? WHERE page_key=?',
                 (json.dumps(content, ensure_ascii=False), page_name, show_on_home, is_published, page_key))
    conn.commit()
    conn.close()
    return ok(message='保存しました')

@bp.route('/api/custom_pages/<page_key>/delete', methods=['POST'])
def api_delete_custom_page(page_key):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM custom_pages WHERE page_key=?', (page_key,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/api/custom_pages/home', methods=['GET'])
def api_home_custom_pages():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT page_key,page_name,url,template_type FROM custom_pages WHERE show_on_home=1 AND is_published=1 ORDER BY id').fetchall()
    conn.close()
    return ok(pages=[{'key':r[0],'name':r[1],'url':r[2],'type':r[3]} for r in rows])

@bp.route('/p/<page_key>')
def page_custom(page_key):
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT page_name,template_type,content,is_published FROM custom_pages WHERE page_key=?', (page_key,)).fetchone()
    conn.close()
    if not row or not row[3]:
        return render_template('404.html'), 404
    return render_template('custom_page.html',
        page_name=row[0], template_type=row[1],
        content=json.loads(row[2]), page_key=page_key)


# ===== 先生モード =====

def hash_teacher_password(pw):
    import hashlib, secrets
    salt = secrets.token_hex(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64)
    return f'scrypt$16384$8$1${salt}${dk.hex()}'

def verify_teacher_password(pw, stored):
    try:
        parts = stored.split('$')
        if len(parts) != 6: return False
        _, n, r, p, salt, expected = parts
        import hashlib
        dk = hashlib.scrypt(pw.encode(), salt=salt.encode(), n=int(n), r=int(r), p=int(p), dklen=64)
        import hmac as _hmac
        return _hmac.compare_digest(dk.hex(), expected)
    except: return False

@bp.route('/api/teacher/login', methods=['POST'])
def api_teacher_login():
    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    teacher_num = int(data.get('teacher_num') or 0)
    password = data.get('password') or ''
    if not group_id or not teacher_num or not password:
        return err('入力が不足しているよ')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s AND school_mode = 1'),
                    (hash_group_id(group_id),))
        grp = cur.fetchone()
    if not grp:
        return err('学校グループが見つからないよ', 403)
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn2.execute('SELECT id, name, password_hash FROM teachers WHERE group_id=? AND teacher_num=?',
                        (str(dict(grp)['id']), teacher_num)).fetchone()
    conn2.close()
    if not row:
        return err('先生番号が見つからないよ', 403)
    if not verify_teacher_password(password, row[2]):
        return err('パスワードが違うよ', 401)
    session['teacher'] = {'group_id': group_id, 'teacher_id': row[0], 'teacher_name': row[1], 'teacher_num': teacher_num}
    session['group_id'] = group_id
    return ok(redirect='/group', teacher_name=row[1])

@bp.route('/api/teacher/register_first', methods=['POST'])
def api_teacher_register_first():
    # 管理者が最初の先生を登録
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('admin_password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    group_id = (data.get('group_id') or '').strip()
    password = data.get('password') or ''
    name = (data.get('name') or '先生').strip()
    if not group_id or not password:
        return err('グループIDとパスワードは必須だよ')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s AND school_mode = 1'),
                    (hash_group_id(group_id),))
        grp = cur.fetchone()
    if not grp:
        return err('学校グループが見つからないよ', 403)
    group_uuid = str(dict(grp)['id'])
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    count = conn2.execute('SELECT COUNT(*) FROM teachers WHERE group_id=?', (group_uuid,)).fetchone()[0]
    pw_hash = hash_teacher_password(password)
    try:
        conn2.execute('INSERT INTO teachers (group_id, teacher_num, name, password_hash) VALUES (?, ?, ?, ?, ?, ?)',
                      (group_uuid, count+1, name, pw_hash))
        conn2.commit()
    except Exception as e:
        conn2.close()
        return err(str(e))
    conn2.close()
    return ok(teacher_num=count+1, message=f'T{count+1}として登録しました')

@bp.route('/api/teacher/add', methods=['POST'])
def api_teacher_add():
    # 先生が新しい先生を追加
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    data = request.get_json(silent=True) or {}
    password = data.get('password') or ''
    name = (data.get('name') or '先生').strip()
    if not password or len(password) < 4:
        return err('パスワードは4文字以上にしてね')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(teacher['group_id']),))
        grp = cur.fetchone()
    if not grp:
        return err('グループが見つからないよ', 404)
    group_uuid = str(dict(grp)['id'])
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    count = conn2.execute('SELECT COUNT(*) FROM teachers WHERE group_id=?', (group_uuid,)).fetchone()[0]
    pw_hash = hash_teacher_password(password)
    try:
        conn2.execute('INSERT INTO teachers (group_id, teacher_num, name, password_hash) VALUES (?,?,?,?)',
                      (group_uuid, count+1, name, pw_hash))
        conn2.commit()
    except Exception as e:
        conn2.close()
        return err(str(e))
    conn2.close()
    return ok(teacher_num=count+1, message=f'T{count+1}として登録しました')

@bp.route('/api/teacher/goal', methods=['POST'])
def api_teacher_goal():
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    data = request.get_json(silent=True) or {}
    date = (data.get('date') or '').strip()
    goal = (data.get('goal') or '').strip()
    if not date or not goal:
        return err('日付と目標は必須だよ')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(teacher['group_id']),))
        grp = cur.fetchone()
    group_uuid = str(dict(grp)['id'])
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn2.execute('INSERT OR REPLACE INTO teacher_goals (group_id, teacher_id, date, goal) VALUES (?,?,?,?)',
                  (group_uuid, teacher['teacher_id'], date, goal))
    conn2.commit()
    conn2.close()
    return ok(message='目標を設定しました')

@bp.route('/api/teacher/notice', methods=['POST'])
def api_teacher_notice():
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    body = (data.get('body') or '').strip()
    notice_date = (data.get('notice_date') or '').strip()
    if not title or not body or not notice_date:
        return err('タイトル・本文・日付は必須だよ')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(teacher['group_id']),))
        grp = cur.fetchone()
    group_uuid = str(dict(grp)['id'])
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn2.execute('INSERT INTO teacher_notices (group_id, teacher_id, teacher_name, title, body, notice_date) VALUES (?,?,?,?,?,?)',
                  (group_uuid, teacher['teacher_id'], teacher['teacher_name'], title, body, notice_date))
    conn2.commit()
    conn2.close()
    return ok(message='お知らせを送信しました')

@bp.route('/api/group/goals', methods=['GET'])
def api_group_goals():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    date = request.args.get('date', '')
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    if date:
        rows = conn2.execute('SELECT goal, date FROM teacher_goals WHERE group_id=? AND date=? ORDER BY id DESC',
                             (grp['id'], date)).fetchall()
    else:
        rows = conn2.execute('SELECT goal, date FROM teacher_goals WHERE group_id=? ORDER BY date DESC LIMIT 30',
                             (grp['id'],)).fetchall()
    conn2.close()
    return ok(goals=[{'goal':r[0],'date':r[1]} for r in rows])

@bp.route('/api/group/notices', methods=['GET'])
def api_group_notices():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    import pytz as _pytz
    from datetime import datetime as _dt
    now_str = _dt.now(_pytz.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M:%S')
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    # 未来の日時(予約投稿)は生徒には見せない
    rows = conn2.execute('SELECT id, teacher_name, title, body, notice_date, created_at FROM teacher_notices WHERE group_id=? AND notice_date<=? ORDER BY notice_date DESC, id DESC LIMIT 50',
                         (grp['id'], now_str)).fetchall()
    conn2.close()
    return ok(notices=[{'id':r[0],'teacher_name':r[1],'title':r[2],'body':r[3],'notice_date':r[4],'created_at':r[5]} for r in rows])

@bp.route('/api/teacher/status', methods=['GET'])
def api_teacher_status():
    teacher = session.get('teacher')
    if not teacher:
        return ok(is_teacher=False)
    return ok(is_teacher=True, teacher_name=teacher.get('teacher_name'), teacher_num=teacher.get('teacher_num'))


@bp.route('/api/teacher/change_password', methods=['POST'])
def api_teacher_change_password():
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    data = request.get_json(silent=True) or {}
    old_pw = data.get('old_password') or ''
    new_pw = data.get('new_password') or ''
    if not new_pw or len(new_pw) < 4:
        return err('新しいパスワードは4文字以上にしてね')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(teacher['group_id']),))
        grp = cur.fetchone()
    group_uuid = str(dict(grp)['id'])
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn2.execute('SELECT password_hash, is_initial FROM teachers WHERE id=?',
                        (teacher['teacher_id'],)).fetchone()
    if not row:
        conn2.close()
        return err('先生情報が見つからないよ', 404)
    if not verify_teacher_password(old_pw, row[0]):
        conn2.close()
        return err('現在のパスワードが違うよ', 401)
    new_hash = hash_teacher_password(new_pw)
    conn2.execute('UPDATE teachers SET password_hash=?, is_initial=0 WHERE id=?',
                  (new_hash, teacher['teacher_id']))
    conn2.commit()
    conn2.close()
    return ok(message='パスワードを変更しました')

@bp.route('/api/teacher/check_initial', methods=['GET'])
def api_teacher_check_initial():
    teacher = session.get('teacher')
    if not teacher:
        return ok(is_initial=False)
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn2.execute('SELECT is_initial FROM teachers WHERE id=?',
                        (teacher['teacher_id'],)).fetchone()
    conn2.close()
    is_initial = bool(row and row[0]) if row else False
    return ok(is_initial=is_initial)

@bp.route('/api/teacher/count', methods=['GET'])
def api_teacher_count():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    count = conn2.execute('SELECT COUNT(*) FROM teachers WHERE group_id=?',
                          (grp['id'],)).fetchone()[0]
    conn2.close()
    return ok(count=int(count))


@bp.route('/api/admin/teachers/<group_id>', methods=['GET'])
def api_admin_get_teachers(group_id):
    pw = request.args.get('pw', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, teacher_num, name, is_initial, is_admin FROM teachers WHERE group_id=? ORDER BY teacher_num',
                        (group_id,)).fetchall()
    conn.close()
    return ok(teachers=[{'id':r[0],'num':r[1],'name':r[2],'is_initial':bool(r[3]),'is_admin':bool(r[4])} for r in rows])

@bp.route('/api/admin/teacher/add', methods=['POST'])
def api_admin_add_teacher():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    group_id = data.get('group_id', '')
    name = (data.get('name') or '先生').strip()
    pw = data.get('teacher_password') or ''
    is_admin = 1 if data.get('is_admin') else 0
    if not pw or len(pw) < 4:
        return err('パスワードは4文字以上にしてね')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    if is_admin:
        count = conn.execute('SELECT COUNT(*) FROM teachers WHERE group_id=? AND is_admin=1', (group_id,)).fetchone()[0]
        if count > 0:
            conn.close()
            return err('管理者先生はすでに設定されています')
    count = conn.execute('SELECT COUNT(*) FROM teachers WHERE group_id=?', (group_id,)).fetchone()[0]
    pw_hash = hash_teacher_password(pw)
    try:
        conn.execute('INSERT INTO teachers (group_id, teacher_num, name, password_hash, is_admin) VALUES (?,?,?,?,?)',
                     (group_id, count+1, name, pw_hash, is_admin))
        conn.commit()
    except Exception as e:
        conn.close()
        return err(str(e))
    conn.close()
    return ok(teacher_num=count+1, message=f'T{count+1}「{name}」を追加しました')

@bp.route('/api/admin/teacher/delete', methods=['POST'])
def api_admin_delete_teacher():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    teacher_id = data.get('teacher_id')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM teachers WHERE id=?', (teacher_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/notices')
def page_notices():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    if not grp.get('school_mode'):
        return redirect(url_for('page_group'))
    return render_template('notices.html', group=grp, group_id=session.get('group_id'))

@bp.route('/api/teacher/notice/<int:notice_id>/delete', methods=['POST'])
def api_teacher_notice_delete(notice_id):
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT teacher_id FROM teacher_notices WHERE id=?', (notice_id,)).fetchone()
    if not row:
        conn.close()
        return err('お知らせが見つからないよ', 404)
    if row[0] != teacher['teacher_id']:
        conn.close()
        return err('自分のお知らせしか削除できないよ', 403)
    conn.execute('DELETE FROM teacher_notices WHERE id=?', (notice_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/api/teacher/notice/<int:notice_id>/edit', methods=['POST'])
def api_teacher_notice_edit(notice_id):
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    body = (data.get('body') or '').strip()
    if not title or not body:
        return err('タイトルと本文は必須だよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT teacher_id FROM teacher_notices WHERE id=?', (notice_id,)).fetchone()
    if not row:
        conn.close()
        return err('お知らせが見つからないよ', 404)
    if row[0] != teacher['teacher_id']:
        conn.close()
        return err('自分のお知らせしか編集できないよ', 403)
    conn.execute('UPDATE teacher_notices SET title=?, body=? WHERE id=?', (title, body, notice_id))
    conn.commit()
    conn.close()
    return ok(message='編集しました')

# ===== 課題機能 =====

@bp.route('/api/tasks', methods=['GET'])
def api_get_tasks():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, teacher_name, title, description, due_date, created_at FROM tasks WHERE group_id=? ORDER BY created_at DESC',
                        (grp['id'],)).fetchall()
    conn.close()
    return ok(tasks=[{'id':r[0],'teacher_name':r[1],'title':r[2],'description':r[3],'due_date':r[4],'created_at':r[5]} for r in rows])

@bp.route('/api/tasks', methods=['POST'])
def api_create_task():
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    due_date = (data.get('due_date') or '').strip()
    if not title or not description:
        return err('タイトルと説明は必須だよ')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(teacher['group_id']),))
        grp = cur.fetchone()
    if not grp:
        return err('グループが見つからないよ', 404)
    group_uuid = str(dict(grp)['id'])
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('INSERT INTO tasks (group_id, teacher_id, teacher_name, title, description, due_date) VALUES (?,?,?,?,?,?)',
                 (group_uuid, teacher['teacher_id'], teacher['teacher_name'], title, description, due_date or None))
    conn.commit()
    conn.close()
    return ok(message='課題を作成しました')

@bp.route('/api/tasks/<int:task_id>', methods=['GET'])
def api_get_task(task_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT id, teacher_name, title, description, due_date, created_at FROM tasks WHERE id=? AND group_id=?',
                       (task_id, grp['id'])).fetchone()
    if not row:
        conn.close()
        return err('課題が見つからないよ', 404)
    subs = conn.execute('SELECT id, author_name, question, answer, created_at FROM task_submissions WHERE task_id=? ORDER BY created_at DESC',
                        (task_id,)).fetchall()
    conn.close()
    return ok(
        task={'id':row[0],'teacher_name':row[1],'title':row[2],'description':row[3],'due_date':row[4],'created_at':row[5]},
        submissions=[{'id':s[0],'author_name':s[1],'question':s[2],'answer':s[3],'created_at':s[4]} for s in subs]
    )

@bp.route('/api/tasks/<int:task_id>/submit', methods=['POST'])
def api_submit_task(task_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    author_name = (data.get('author_name') or '').strip()
    question = (data.get('question') or '').strip()
    answer = (data.get('answer') or '').strip()
    if not author_name or not question or not answer:
        return err('名前・問題・答えは必須だよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT id FROM tasks WHERE id=? AND group_id=?', (task_id, grp['id'])).fetchone()
    if not row:
        conn.close()
        return err('課題が見つからないよ', 404)
    conn.execute('INSERT INTO task_submissions (task_id, group_id, author_name, question, answer) VALUES (?,?,?,?,?)',
                 (task_id, grp['id'], author_name, question, answer))
    conn.commit()
    conn.close()
    return ok(message='提出しました')

@bp.route('/api/tasks/<int:task_id>/delete', methods=['POST'])
def api_delete_task(task_id):
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT teacher_id FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        conn.close()
        return err('課題が見つからないよ', 404)
    if row[0] != teacher['teacher_id']:
        conn.close()
        return err('自分の課題しか削除できないよ', 403)
    conn.execute('DELETE FROM task_submissions WHERE task_id=?', (task_id,))
    conn.execute('DELETE FROM tasks WHERE id=?', (task_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/tasks')
def page_tasks():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    if not grp.get('school_mode'):
        return redirect(url_for('page_group'))
    return render_template('tasks.html', group=grp, group_id=session.get('group_id'))

@bp.route('/tasks/<int:task_id>')
def page_task_detail(task_id):
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    if not grp.get('school_mode'):
        return redirect(url_for('page_group'))
    return render_template('task_detail.html', group=grp, group_id=session.get('group_id'), task_id=task_id)

@bp.route('/api/tasks/<int:task_id>/edit', methods=['POST'])
def api_edit_task(task_id):
    teacher = session.get('teacher')
    if not teacher:
        return err('先生としてログインしてね', 401)
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    due_date = (data.get('due_date') or '').strip()
    if not title or not description:
        return err('タイトルと説明は必須だよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT teacher_id FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        conn.close()
        return err('課題が見つからないよ', 404)
    if row[0] != teacher['teacher_id']:
        conn.close()
        return err('自分の課題しか編集できないよ', 403)
    conn.execute('UPDATE tasks SET title=?, description=?, due_date=? WHERE id=?',
                 (title, description, due_date or None, task_id))
    conn.commit()
    conn.close()
    return ok(message='編集しました')

# ===== イベント機能 =====

@bp.route('/event/<event_key>')
def page_event(event_key):
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT * FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    conn.close()
    if not row:
        return render_template('404.html'), 404
    event = dict(zip([d[0] for d in conn.description if conn.description], row)) if False else None
    # dict変換
    conn2 = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row2 = conn2.execute('SELECT id,event_key,title,description,start_date,end_date,result_date,group_id,is_published FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    conn2.close()
    if not row2:
        return render_template('404.html'), 404
    event = {
        'id': row2[0], 'event_key': row2[1], 'title': row2[2],
        'description': row2[3], 'start_date': row2[4], 'end_date': row2[5],
        'result_date': row2[6], 'group_id': row2[7], 'is_published': row2[8]
    }
    return render_template('event.html', event=event)

@bp.route('/api/events', methods=['GET'])
def api_get_events():
    pw = request.args.get('pw', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id,event_key,title,description,start_date,end_date,result_date,group_id,is_published,created_at,ip_restrict FROM events ORDER BY id DESC').fetchall()
    conn.close()
    return ok(events=[{'id':r[0],'event_key':r[1],'title':r[2],'description':r[3],'start_date':r[4],'end_date':r[5],'result_date':r[6],'group_id':r[7],'is_published':bool(r[8]),'created_at':r[9],'ip_restrict':bool(r[10]) if len(r)>10 else False} for r in rows])

@bp.route('/api/events', methods=['POST'])
def api_create_event():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    event_key = (data.get('event_key') or '').strip()
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    start_date = (data.get('start_date') or '').strip()
    end_date = (data.get('end_date') or '').strip()
    result_date = (data.get('result_date') or '').strip()
    group_id = (data.get('group_id') or '').strip()
    if not event_key or not title or not group_id:
        return err('キー・タイトル・グループIDは必須だよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    try:
        conn.execute('INSERT INTO events (event_key,title,description,start_date,end_date,result_date,group_id) VALUES (?,?,?,?,?,?,?)',
                     (event_key, title, description, start_date, end_date, result_date, group_id))
        conn.commit()
    except Exception as e:
        conn.close()
        return err(str(e))
    conn.close()
    return ok(message='イベントを作成しました', url='/event/'+event_key)

@bp.route('/api/events/<int:event_id>/publish', methods=['POST'])
def api_publish_event(event_id):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    is_published = 1 if data.get('is_published') else 0
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE events SET is_published=? WHERE id=?', (is_published, event_id))
    conn.commit()
    conn.close()
    return ok(message='更新しました')

@bp.route('/api/server_time', methods=['GET'])
def api_server_time():
    # サーバーの現在時刻を返す(PCの時計に頼らないため)
    import time as _time
    return ok(now_ms=int(_time.time() * 1000))

@bp.route('/api/events/<event_key>/quizzes', methods=['GET'])
def api_event_quizzes(event_key):
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    event = conn.execute('SELECT id,group_id,end_date,start_date FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    conn.close()
    if not event:
        return err('イベントが見つからないよ', 404)
    event_id, group_id, end_date, start_date = event
    # 開催前は問題を渡さない(PCの時計をいじるズルを防ぐ。サーバーの時計で判定)
    if start_date:
        import pytz as _pytz
        from datetime import datetime as _dt
        now = _dt.now(_pytz.timezone('Asia/Tokyo')).replace(tzinfo=None)
        st = _dt.fromisoformat(start_date.replace('T', ' '))
        if now < st:
            return err('まだ開催前だよ。開始時間まで待ってね', 403)
    # グループのクイズを取得
    with get_db() as gconn:
        gcur = make_cursor(gconn)
        gcur.execute(q('SELECT id, question, has_options, answer_options FROM quizzes WHERE group_id = %s ORDER BY created_at'), (group_id,))
        rows = [dict(r) for r in gcur.fetchall()]
    return ok(quizzes=[{'id':str(r['id']),'question':r['question'],'has_options':bool(r['has_options'])} for r in rows])

@bp.route('/api/events/<event_key>/submit', methods=['POST'])
def api_event_submit(event_key):
    import sqlite3 as _sq, hashlib as _hl
    data = request.get_json(silent=True) or {}
    nickname = (data.get('nickname') or '').strip()
    if not nickname:
        return err('ニックネームを入力してね')
    fp = data.get('fingerprint', '')
    ip_hash = _hl.sha256(client_ip().encode()).hexdigest()[:16]
    # 参加券(fp)だけで判定する。IPを混ぜると同じWi-Fiの人を巻き込むから使わない
    combined = _hl.sha256(fp.encode()).hexdigest()[:16] if fp else ip_hash
    ip_hash = combined
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    event = conn.execute('SELECT id,end_date FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    if not event:
        conn.close()
        return err('イベントが見つからないよ', 404)
    event_id, end_date = event
    # 締切チェック
    if end_date:
        import pytz as _pytz
        from datetime import datetime as _dt
        now = _dt.now(_pytz.timezone('Asia/Tokyo')).replace(tzinfo=None)
        end = _dt.fromisoformat(end_date.replace('T',' '))
        if now > end:
            conn.close()
            return err('イベントの受付は終了しました', 403)
    # 2回目防止チェック
    existing = conn.execute('SELECT id FROM event_participants WHERE event_id=? AND ip_hash=?', (event_id, ip_hash)).fetchone()
    if existing:
        conn.close()
        return err('この端末からは既に参加済みです', 403)
    # 採点はブラウザの自己申告を一切使わず、サーバーの帳簿から集計する
    conn.execute('''CREATE TABLE IF NOT EXISTS event_answers (
        event_id INTEGER, token TEXT, quiz_id TEXT, correct INTEGER,
        time_ms INTEGER, hint_used INTEGER,
        UNIQUE(event_id, token, quiz_id))''')
    recorded = conn.execute('SELECT quiz_id, correct, time_ms, hint_used FROM event_answers WHERE event_id=? AND token=?',
                            (event_id, ip_hash)).fetchall()
    if not recorded:
        conn.close()
        return err('回答の記録が見つからないよ。もう一度最初から挑戦してね')
    total_correct = 0
    total_time = 0
    for quiz_id, correct, time_ms, hint_used in recorded:
        # ヒント使用時は2/3点(スコアを累積)
        if correct and hint_used:
            total_correct += 2/3
        else:
            total_correct += correct
        total_time += time_ms
            # IP制限チェック
    _ip_ev = client_ip()
    _ev_row_ip = conn.execute('SELECT ip_restrict FROM events WHERE event_key=?', (event_key,)).fetchone()
    if _ev_row_ip and _ev_row_ip[0]:
        _ip_hash_ev = __import__('hashlib').sha256(_ip_ev.encode()).hexdigest()[:16]
        _dup = conn.execute('SELECT COUNT(*) FROM event_submissions WHERE event_key=? AND ip_hash=?', (event_key, _ip_hash_ev)).fetchone()
        if _dup and _dup[0] > 0:
            return err('このネットワークからは既に回答されています(IP制限が有効です)')
    conn.execute('INSERT INTO event_attempts (event_id,nickname,ip_hash,quiz_id,correct,time_ms) VALUES (?,?,?,?,?,?)',
                     (event_id, nickname, ip_hash, quiz_id, correct, time_ms))
    total_correct_rounded = round(total_correct * 100) / 100
    # 参加者登録
    try:
        conn.execute('INSERT INTO event_participants (event_id,nickname,ip_hash,total_correct,total_time_ms,total_questions) VALUES (?,?,?,?,?,?)',
                     (event_id, nickname, ip_hash, total_correct_rounded, total_time, len(recorded)))
        conn.commit()
    except Exception as e:
        conn.close()
        return err('既に参加済みです', 403)
    conn.close()
    return ok(message='回答を記録しました', total_correct=total_correct_rounded, total=len(recorded))

@bp.route('/api/events/<event_key>/ranking', methods=['GET'])
def api_event_ranking(event_key):
    import sqlite3 as _sq
    from datetime import datetime as _dt
    import pytz as _pytz
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    event = conn.execute('SELECT id,result_date FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    if not event:
        conn.close()
        return err('イベントが見つからないよ', 404)
    event_id, result_date = event
    # 結果発表日チェック
    pw = request.args.get('pw', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    is_admin = pw == admin_pw
    if result_date and not is_admin:
        now = _dt.now(_pytz.timezone('Asia/Tokyo')).replace(tzinfo=None)
        rd = _dt.fromisoformat(result_date.replace('T',' '))
        if now < rd:
            conn.close()
            return ok(ranking=[], result_date=result_date, not_yet=True)
    rows = conn.execute('''SELECT nickname, total_correct, total_time_ms, total_questions
        FROM event_participants WHERE event_id=?
        ORDER BY total_correct DESC, total_time_ms ASC''', (event_id,)).fetchall()
    conn.close()
    ranking = [{'rank':i+1,'nickname':r[0],'correct':r[1],'time_ms':r[2],'total':r[3]} for i,r in enumerate(rows)]
    return ok(ranking=ranking, result_date=result_date, not_yet=False)

@bp.route('/api/events/<event_key>/check_ip', methods=['GET'])
def api_event_check_ip(event_key):
    import sqlite3 as _sq, hashlib as _hl
    fp = request.args.get('fp', '')
    ip_hash = _hl.sha256(client_ip().encode()).hexdigest()[:16]
    # 参加券(fp)だけで判定する。IPを混ぜると同じWi-Fiの人を巻き込むから使わない
    combined = _hl.sha256(fp.encode()).hexdigest()[:16] if fp else ip_hash
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    event = conn.execute('SELECT id FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    if not event:
        conn.close()
        return err('イベントが見つからないよ', 404)
    existing = conn.execute('SELECT nickname FROM event_participants WHERE event_id=? AND ip_hash=?', (event[0], combined)).fetchone()
    conn.close()
    return ok(already_participated=bool(existing), nickname=existing[0] if existing else None)


@bp.route('/api/event/ip_restrict', methods=['POST'])
def api_event_ip_restrict():
    data = request.get_json(silent=True) or {}
    event_id = data.get('event_id')
    enabled = data.get('enabled', False)
    if not event_id:
        return err('event_idが必要')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE events SET ip_restrict=? WHERE id=?', (1 if enabled else 0, event_id))
    conn.commit(); conn.close()
    return jsonify(ok=True, message='IP制限を' + ('有効' if enabled else '無効') + 'にしたよ')

@bp.route('/api/events/<int:event_id>/schedule', methods=['POST'])
def api_update_event_schedule(event_id):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    start_date = (data.get('start_date') or '').strip()
    end_date = (data.get('end_date') or '').strip()
    result_date = (data.get('result_date') or '').strip()
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE events SET start_date=?, end_date=?, result_date=? WHERE id=?',
                 (start_date or None, end_date or None, result_date or None, event_id))
    conn.commit()
    conn.close()
    return ok(message='日程を更新しました')

@bp.route('/api/events/<event_key>/add_quiz', methods=['POST'])
def api_event_add_quiz(event_key):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('admin_password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    event = conn.execute('SELECT group_id FROM events WHERE event_key=?', (event_key,)).fetchone()
    conn.close()
    if not event:
        return err('イベントが見つからないよ', 404)
    group_id = event[0]
    name = (data.get('name') or '主催者').strip()
    question = (data.get('question') or '').strip()
    answer = (data.get('answer') or '').strip()
    answers = data.get('answers') or [answer]
    hint = (data.get('hint') or '').strip()
    explanation = (data.get('explanation') or '').strip()
    answer_options = data.get('answer_options')
    has_options = 1 if answer_options else 0
    import json as _json
    options_json = _json.dumps(answer_options, ensure_ascii=False) if answer_options else None
    answers_json = _json.dumps(answers, ensure_ascii=False)
    if not question or not answer:
        return err('問題と答えは必須だよ')
    with get_db() as gconn:
        gcur = make_cursor(gconn)
        quiz_id = new_id()
        gcur.execute(q('INSERT INTO quizzes (id,group_id,author_name,class_name,question,answer,answers,hint,explanation,tags,has_options,answer_options) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
                     (quiz_id, group_id, name, '', question, answer, answers_json, hint, explanation, 'イベント', has_options, options_json))
    return ok(message='追加しました', quiz_id=quiz_id)

@bp.route('/api/events/<event_key>/answer', methods=['POST'])
def api_event_answer(event_key):
    import sqlite3 as _sq
    data = request.get_json(silent=True) or {}
    quiz_id = data.get('quiz_id')
    user_answer = str(data.get('user_answer', ''))[:500]
    time_ms = int(data.get('time_ms') or 0)
    time_ms = max(0, min(1800000, time_ms))  # 1問30分まで(ありえない値を防ぐ)
    hint_used = 1 if data.get('hint_used') else 0
    fp = data.get('fingerprint', '')

    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    event = conn.execute('SELECT group_id,start_date FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    conn.close()
    if not event:
        return err('イベントが見つからないよ', 404)
    # 開催前は答え合わせもさせない(サーバーの時計で判定)
    if event[1]:
        import pytz as _pytz
        from datetime import datetime as _dt
        now = _dt.now(_pytz.timezone('Asia/Tokyo')).replace(tzinfo=None)
        st = _dt.fromisoformat(event[1].replace('T', ' '))
        if now < st:
            return err('まだ開催前だよ', 403)
    event = (event[0],)

    with get_db() as gconn:
        gcur = make_cursor(gconn)
        gcur.execute(q('SELECT answer, answers, explanation FROM quizzes WHERE id = %s AND group_id = %s'),
                     (quiz_id, event[0]))
        row = gcur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)

    row_dict = dict(row)
    correct_answer = dec(row_dict.get('answer') or '')
    row_dict['answer'] = correct_answer
    # answersも復号
    import json as _json
    answers_raw = row_dict.get('answers')
    if answers_raw:
        try:
            ans_list = _json.loads(answers_raw)
            row_dict['answers'] = _json.dumps([dec(a) for a in ans_list], ensure_ascii=False)
        except: pass
    is_correct = check_answer(user_answer, row_dict)

    # 採点結果をサーバーの帳簿にも記録する(提出時の自己申告を信じないため)
    # 同じ問題は最初の1回だけ記録(答えを何度も送って正解を探るズルも防ぐ)
    import hashlib as _hl
    token = _hl.sha256(fp.encode()).hexdigest()[:16] if fp else _hl.sha256(client_ip().encode()).hexdigest()[:16]
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('''CREATE TABLE IF NOT EXISTS event_answers (
        event_id INTEGER, token TEXT, quiz_id TEXT, correct INTEGER,
        time_ms INTEGER, hint_used INTEGER,
        UNIQUE(event_id, token, quiz_id))''')
    ev = conn.execute('SELECT id FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    if ev:
        conn.execute('INSERT OR IGNORE INTO event_answers (event_id, token, quiz_id, correct, time_ms, hint_used) VALUES (?,?,?,?,?,?)',
                     (ev[0], token, str(quiz_id), 1 if is_correct else 0, time_ms, hint_used))
        conn.commit()
    conn.close()

    return ok(correct=is_correct, correct_answer=correct_answer,
              explanation=dec(row_dict.get('explanation') or ''), time_ms=time_ms)

@bp.route('/api/events/<event_key>/quizzes_detail', methods=['GET'])
def api_event_quizzes_detail(event_key):
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    event = conn.execute('SELECT id, group_id, result_date FROM events WHERE event_key=? AND is_published=1', (event_key,)).fetchone()
    conn.close()
    if not event:
        return err('イベントが見つからないよ', 404)
    # 結果発表日チェック
    import pytz as _pytz
    from datetime import datetime as _dt
    now = _dt.now(_pytz.timezone('Asia/Tokyo')).replace(tzinfo=None)
    if event[2]:
        rd = _dt.fromisoformat(event[2].replace('T',' '))
        if now < rd:
            return ok(quizzes=[], not_yet=True)
    with get_db() as gconn:
        gcur = make_cursor(gconn)
        gcur.execute(q('''SELECT q.id, q.author_name, q.question, q.answer, q.explanation,
            (SELECT COUNT(*) FROM attempts WHERE quiz_id=q.id) as attempts,
            (SELECT COUNT(*) FROM attempts WHERE quiz_id=q.id AND correct=1) as corrects,
            (SELECT COALESCE(AVG(difficulty),0) FROM feedbacks WHERE quiz_id=q.id) as avg_difficulty
            FROM quizzes q WHERE q.group_id=%s ORDER BY q.created_at'''), (event[1],))
        rows = [dict(r) for r in gcur.fetchall()]
    result = []
    for r in rows:
        result.append({
            'id': str(r['id']),
            'author_name': dec(r['author_name'] or ''),
            'question': dec(r['question'] or ''),
            'answer': dec(r['answer'] or ''),
            'explanation': dec(r['explanation'] or ''),
            'attempts': int(r['attempts'] or 0),
            'corrects': int(r['corrects'] or 0),
            'avg_difficulty': float(r['avg_difficulty'] or 0),
        })
    return ok(quizzes=result)

@bp.route('/api/banner', methods=['GET'])
def api_get_banner():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT message, color, is_active, link_url FROM site_banner WHERE id=1').fetchone()
    conn.close()
    if not row or not row[2]:
        return ok(active=False)
    return ok(active=True, message=row[0], color=row[1], link_url=row[3] or '')

@bp.route('/api/banner', methods=['POST'])
def api_set_banner():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    message = (data.get('message') or '').strip()
    color = (data.get('color') or '#667eea').strip()
    is_active = 1 if data.get('is_active') else 0
    link_url = (data.get('link_url') or '').strip()
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE site_banner SET message=?, color=?, is_active=?, link_url=?, updated_at=datetime("now","localtime") WHERE id=1',
                 (message, color, is_active, link_url))
    conn.commit()
    conn.close()
    return ok(message='更新しました')

@bp.route('/api/tags/suggest', methods=['GET'])
def api_tags_suggest():
    q_word = request.args.get('q', '').strip()
    group_id = request.args.get('group_id', '').strip()
    if not q_word:
        return ok(tags=[])
    with get_db() as conn:
        cur = make_cursor(conn)
        if group_id:
            cur.execute(q('SELECT tags FROM quizzes WHERE group_id=%s'), (group_id,))
        else:
            cur.execute(q('SELECT tags FROM quizzes'))
        rows = cur.fetchall()
    
    tag_count = {}
    for row in rows:
        tags_raw = dict(row).get('tags') or ''
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()]
        for tag in tags:
            tag_dec = dec(tag)
            if q_word.lower() in tag_dec.lower():
                tag_count[tag_dec] = tag_count.get(tag_dec, 0) + 1
    
    sorted_tags = sorted(tag_count.items(), key=lambda x: -x[1])[:10]
    return ok(tags=[{'name': t[0], 'count': t[1]} for t in sorted_tags])

@bp.route('/api/beta/status', methods=['GET'])
def api_beta_status():
    feature = request.args.get('feature', 'ai_scoring')
    import sqlite3 as _sq
    from datetime import datetime as _dt
    import pytz as _pytz
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT feature_key, feature_name, start_date, end_date, is_active, description FROM beta_features WHERE feature_key=?', (feature,)).fetchone()
    conn.close()
    if not row:
        return ok(active=False)
    now = _dt.now(_pytz.timezone('Asia/Tokyo')).replace(tzinfo=None)
    start = _dt.fromisoformat(row[2].replace('T',' ')) if row[2] else None
    end = _dt.fromisoformat(row[3].replace('T',' ')) if row[3] else None
    in_period = True
    if start and now < start: in_period = False
    if end and now > end: in_period = False
    active = bool(row[4]) and in_period
    return ok(active=active, feature_key=row[0], feature_name=row[1],
              start_date=row[2], end_date=row[3], is_active=bool(row[4]),
              in_period=in_period, description=row[5])

@bp.route('/api/beta/update', methods=['POST'])
def api_beta_update():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    feature = (data.get('feature_key') or 'ai_scoring').strip()
    start_date = (data.get('start_date') or '').strip() or None
    end_date = (data.get('end_date') or '').strip() or None
    is_active = 1 if data.get('is_active') else 0
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE beta_features SET start_date=?, end_date=?, is_active=? WHERE feature_key=?',
                 (start_date, end_date, is_active, feature))
    conn.commit()
    conn.close()
    return ok(message='更新しました')

def check_beta_active(feature_key='ai_scoring'):
    import sqlite3 as _sq
    from datetime import datetime as _dt
    import pytz as _pytz
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT start_date, end_date, is_active FROM beta_features WHERE feature_key=?', (feature_key,)).fetchone()
    conn.close()
    if not row or not row[2]: return False
    now = _dt.now(_pytz.timezone('Asia/Tokyo')).replace(tzinfo=None)
    start = _dt.fromisoformat(row[0].replace('T',' ')) if row[0] else None
    end = _dt.fromisoformat(row[1].replace('T',' ')) if row[1] else None
    if start and now < start: return False
    if end and now > end: return False
    return True

# ===== 対戦モード =====

@bp.route('/api/battle/create', methods=['POST'])
def api_battle_create():
    import sqlite3 as _sq, json as _json, random as _random
    data = request.get_json(silent=True) or {}
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    nickname = (data.get('nickname') or '').strip()
    quiz_count = int(data.get('quiz_count') or 5)
    max_players = int(data.get('max_players') or 2)
    if not nickname:
        return err('ニックネームを入力してね')
    if quiz_count < 1 or quiz_count > 20:
        return err('問題数は1〜20にしてね')
    if max_players < 2 or max_players > 5:
        return err('人数は2〜5人にしてね')
    with get_db() as gconn:
        gcur = make_cursor(gconn)
        gcur.execute(q('SELECT id FROM quizzes WHERE group_id=%s AND COALESCE(under_review,0)=0'), (grp['id'],))
        all_ids = [str(dict(r)['id']) for r in gcur.fetchall()]
    if len(all_ids) < quiz_count:
        return err('クイズが足りないよ（' + str(len(all_ids)) + '問しかない）')
    quiz_ids = _random.sample(all_ids, quiz_count)
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    for _ in range(10):
        code = str(_random.randint(1000, 9999))
        existing = conn.execute('SELECT id FROM battle_rooms WHERE room_code=? AND status!=?', (code, 'finished')).fetchone()
        if not existing:
            break
    players = _json.dumps([nickname])
    conn.execute('INSERT INTO battle_rooms (room_code, group_id, host_nickname, quiz_count, quiz_ids, max_players, players) VALUES (?,?,?,?,?,?,?)',
                 (code, str(grp['id']), nickname, quiz_count, _json.dumps(quiz_ids), max_players, players))
    conn.commit()
    conn.close()
    return ok(room_code=code, quiz_ids=quiz_ids)

@bp.route('/api/battle/join', methods=['POST'])
def api_battle_join():
    import sqlite3 as _sq, json as _json
    data = request.get_json(silent=True) or {}
    room_code = (data.get('room_code') or '').strip()
    nickname = (data.get('nickname') or '').strip()
    if not room_code or not nickname:
        return err('ルームコードとニックネームを入力してね')
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    room = conn.execute('SELECT id,host_nickname,status,quiz_count,quiz_ids,max_players,players FROM battle_rooms WHERE room_code=?', (room_code,)).fetchone()
    if not room:
        conn.close()
        return err('ルームが見つからないよ')
    if room[2] == 'playing':
        conn.close()
        return err('このルームはすでに始まっているよ')
    if room[2] == 'finished':
        conn.close()
        return err('このルームは終了しているよ')
    players = _json.loads(room[6] or '[]')
    max_players = room[5] or 2
    if nickname in players:
        conn.close()
        return err('同じニックネームはすでに参加しているよ')
    if len(players) >= max_players:
        conn.close()
        return err('このルームはすでに満員だよ（' + str(max_players) + '人）')
    players.append(nickname)
    conn.execute('UPDATE battle_rooms SET players=? WHERE room_code=?', (_json.dumps(players), room_code))
    conn.commit()
    quiz_ids = _json.loads(room[4])
    conn.close()
    return ok(room_code=room_code, host_nickname=room[1], quiz_count=room[3], quiz_ids=quiz_ids, players=players)

@bp.route('/api/battle/status', methods=['GET'])
def api_battle_status():
    import sqlite3 as _sq, json as _json
    room_code = request.args.get('room_code', '')
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    room = conn.execute('SELECT host_nickname,status,quiz_count,quiz_ids,max_players,players FROM battle_rooms WHERE room_code=?', (room_code,)).fetchone()
    if not room:
        conn.close()
        return err('ルームが見つからないよ', 404)
    quiz_ids = _json.loads(room[3])
    players = _json.loads(room[5] or '[]')
    conn.close()
    return ok(host=room[0], status=room[1], quiz_count=room[2], quiz_ids=quiz_ids, max_players=room[4], players=players)

@bp.route('/api/battle/start', methods=['POST'])
def api_battle_start():
    import sqlite3 as _sq
    data = request.get_json(silent=True) or {}
    room_code = (data.get('room_code') or '').strip()
    nickname = (data.get('nickname') or '').strip()
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    room = conn.execute('SELECT host_nickname, status FROM battle_rooms WHERE room_code=?', (room_code,)).fetchone()
    if not room:
        conn.close()
        return err('ルームが見つからないよ', 404)
    if room[0] != nickname:
        conn.close()
        return err('ホストだけがスタートできるよ')
    if room[1] == 'playing':
        conn.close()
        return err('すでに始まっているよ')
    conn.execute('UPDATE battle_rooms SET status=? WHERE room_code=?', ('playing', room_code))
    conn.commit()
    conn.close()
    return ok(message='スタートしました')

@bp.route('/api/battle/answer', methods=['POST'])
def api_battle_answer():
    import sqlite3 as _sq
    data = request.get_json(silent=True) or {}
    room_code = (data.get('room_code') or '').strip()
    nickname = (data.get('nickname') or '').strip()
    quiz_id = (data.get('quiz_id') or '').strip()
    user_answer = str(data.get('user_answer', ''))[:500]
    time_ms = int(data.get('time_ms') or 0)
    time_ms = max(0, min(7200000, time_ms))
    # ログイン中のグループのクイズしか採点しない(答えの漏洩を防ぐ)
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    # 採点
    with get_db() as gconn:
        gcur = make_cursor(gconn)
        gcur.execute(q('SELECT answer, answers, has_options FROM quizzes WHERE id=%s AND group_id=%s'), (quiz_id, grp['id']))
        row = gcur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    row_dict = dict(row)
    row_dict['answer'] = dec(row_dict.get('answer') or '')
    import json as _json
    answers_raw = row_dict.get('answers')
    if answers_raw:
        try:
            ans_list = _json.loads(answers_raw)
            row_dict['answers'] = _json.dumps([dec(a) for a in ans_list], ensure_ascii=False)
        except: pass
    is_correct = check_answer(user_answer, row_dict)
    # 回答を記録
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('INSERT INTO battle_answers (room_code,nickname,quiz_id,correct,time_ms) VALUES (?,?,?,?,?)',
                 (room_code, nickname, quiz_id, 1 if is_correct else 0, time_ms))
    conn.commit()
    conn.close()
    return ok(correct=is_correct, correct_answer=dec(row_dict.get('answer') or ''))

@bp.route('/api/battle/result', methods=['GET'])
def api_battle_result():
    import sqlite3 as _sq, json as _json
    room_code = request.args.get('room_code', '')
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    room = conn.execute('SELECT host_nickname,quiz_count,players FROM battle_rooms WHERE room_code=?', (room_code,)).fetchone()
    if not room:
        conn.close()
        return err('ルームが見つからないよ', 404)
    host, quiz_count, players_json = room
    players = _json.loads(players_json or '[]')
    # 全員の回答数を確認
    def get_score(nick):
        rows = conn.execute('SELECT correct, time_ms FROM battle_answers WHERE room_code=? AND nickname=?', (room_code, nick)).fetchall()
        correct = sum(r[0] for r in rows)
        total_time = sum(r[1] for r in rows)
        return {'nickname': nick, 'correct': correct, 'total_time': total_time, 'answered': len(rows)}
    scores = [get_score(p) for p in players]
    both_done = all(s['answered'] >= quiz_count for s in scores)
    # 順位付け
    scores.sort(key=lambda s: (-s['correct'], s['total_time']))
    winner = scores[0]['nickname'] if both_done and len(scores) > 0 else None
    if both_done:
        conn.execute('UPDATE battle_rooms SET status=? WHERE room_code=?', ('finished', room_code))
        conn.commit()
    conn.close()
    return ok(scores=scores, both_done=both_done, winner=winner, quiz_count=quiz_count)

@bp.route('/battle')
def page_battle():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('battle.html', group=grp)

@bp.route('/api/quizzes/<quiz_id>/info', methods=['GET'])
def api_quiz_info(quiz_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, question, answer, answers, has_options, answer_options, hint, explanation, under_review FROM quizzes WHERE id=%s AND group_id=%s'), (quiz_id, grp['id']))
        row = cur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    r = dict(row)
    # 調査中のクイズはバトルでも出さない
    if r.get('under_review'):
        return err('このクイズは調査中だよ', 403)
    # 画像も一緒に返す(バトルモードで表示するため)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT filename FROM quiz_images WHERE quiz_id = %s ORDER BY created_at'), (quiz_id,))
        imgs = ['/static/uploads/' + (r2['filename'] if hasattr(r2, 'keys') else r2[0]) for r2 in cur.fetchall()]
    import json as _json
    opts = None
    if r.get('answer_options'):
        try:
            raw = _json.loads(r['answer_options'])
            opts = [dec(o) for o in raw]
        except: pass
    return ok(
        id=str(r['id']),
        question=dec(r['question'] or ''),
        has_options=bool(r['has_options']),
        answer_options=opts,
        hint=dec(r['hint'] or '') if r.get('hint') else None,
        images=imgs,
    )

@bp.route('/typing')
def page_typing():
    # QZEROタイピング(誰でも遊べる)
    return render_template('typing.html')

def _typing_db():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute("""CREATE TABLE IF NOT EXISTS typing_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nickname TEXT, score INTEGER,
        kpm INTEGER, accuracy REAL, created_at TEXT, mode TEXT)""")
    try:
        conn.execute('ALTER TABLE typing_scores ADD COLUMN mode TEXT')
    except Exception:
        pass
    conn.execute("""CREATE TABLE IF NOT EXISTS typing_plays (
        token TEXT PRIMARY KEY, started_at REAL)""")
    return conn

@bp.route('/api/typing/start', methods=['POST'])
def api_typing_start():
    # プレイ券を発行(提出時にサーバーが経過時間を検証するため)
    if not rate_limit(f'typstart:{client_ip()}', 10):
        return err('少し待ってね')
    import secrets, time as _t
    token = secrets.token_hex(16)
    conn = _typing_db()
    conn.execute('INSERT INTO typing_plays (token, started_at) VALUES (?,?)', (token, _t.time()))
    # 古いプレイ券は掃除
    conn.execute('DELETE FROM typing_plays WHERE started_at < ?', (_t.time() - 3600,))
    conn.commit()
    conn.close()
    return ok(token=token)

@bp.route('/api/typing/submit', methods=['POST'])
def api_typing_submit():
    if not rate_limit(f'typsub:{client_ip()}', 6):
        return err('少し待ってね')
    data = request.get_json(silent=True) or {}
    token = str(data.get('token') or '')
    nickname = str(data.get('nickname') or '').strip()[:12] or 'ななしさん'
    score = int(data.get('score') or 0)
    kpm = int(data.get('kpm') or 0)
    accuracy = float(data.get('accuracy') or 0)
    mode = data.get('mode')
    if mode not in ['easy', 'normal', 'hard']:
        mode = 'normal'
    import time as _t
    conn = _typing_db()
    row = conn.execute('SELECT started_at FROM typing_plays WHERE token=?', (token,)).fetchone()
    if not row:
        conn.close()
        return err('プレイ券がないよ。最初から遊んでね')
    elapsed = _t.time() - row[0]
    # 60秒ゲームなのに速すぎ/遅すぎる提出は不正とみなす
    if elapsed < 55 or elapsed > 300:
        conn.close()
        return err('プレイ時間がおかしいよ')
    # 物理的にありえない数値は弾く(世界記録でもKPM900くらい)
    if kpm > 900 or score > 3000 or accuracy > 100:
        conn.close()
        return err('記録がおかしいよ')
    conn.execute('DELETE FROM typing_plays WHERE token=?', (token,))  # 使い捨て
    import pytz as _p
    from datetime import datetime as _d
    conn.execute('INSERT INTO typing_scores (nickname, score, kpm, accuracy, created_at, mode) VALUES (?,?,?,?,?,?)',
                 (enc(nickname), score, kpm, round(accuracy, 1),
                  _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M'), mode))
    conn.commit()
    conn.close()
    return ok(message='記録したよ!')

@bp.route('/api/typing/ranking', methods=['GET'])
def api_typing_ranking():
    conn = _typing_db()
    mode = request.args.get('mode')
    if mode not in ['easy', 'normal', 'hard']:
        mode = 'normal'
    rows = conn.execute("SELECT nickname, score, kpm, accuracy, created_at FROM typing_scores WHERE COALESCE(mode,'normal')=? ORDER BY score DESC, kpm DESC LIMIT 20", (mode,)).fetchall()
    conn.close()
    return ok(ranking=[{'nickname': dec(r[0] or ''), 'score': r[1], 'kpm': r[2],
                        'accuracy': r[3], 'created_at': str(r[4] or '')[:10]} for r in rows])

# ====================================================================
# QZERO関連はbp_qzero.pyに引っ越した(チャット・Mini・教室・パターン)
# ====================================================================
