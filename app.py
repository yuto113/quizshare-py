import sys
# # pykakasiはPython3.10用のuserパスに入っている
sys.path.insert(0, '/home/yuto113/.local/lib/python3.10/site-packages')
# -*- coding: utf-8 -*-
# ====================================================================
# 暗号化ヘルパー(問題文・答え・グループ名・作者名・タグなどを暗号化)
# ====================================================================
# ====================================================================
# クイズシェア (Flask版)
# ぜんぶのサーバーのしごとがこのファイルにまとまっているよ。
# ====================================================================

import os
import json
import time
import hmac
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, abort, g,
)

# 本番で .env がなくても大丈夫なように try
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ====================================================================
# 1. Flaskアプリを作る
# ====================================================================
# Flask は「サーバー」を作るための道具箱。app がその本体。
app = Flask(__name__)

# セッション(ログイン状態を覚えておく入れ物)の暗号化キー
# これがないと他の人になりすまされちゃう。
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-' + secrets.token_hex(16))

# セッションのクッキーを安全にする設定
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # JavaScriptから読めないようにする
    SESSION_COOKIE_SAMESITE='Lax',  # 他のサイトから送られないようにする
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_DEBUG', '0') != '1',  # HTTPSの時だけクッキーを送る(本番用)
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # 大きすぎるデータ(20MB超)は拒否
)


# ====================================================================
# 共通部品はqz_common.pyに引っ越した(enc/dec/DB/採点/ok/errなど)
# ====================================================================
from qz_common import (
    get_fernet, enc, dec,
    PEPPER, hash_group_id, hash_password, verify_password,
    DATABASE_URL, USE_POSTGRES, get_db, make_cursor, PH, q,
    init_db, new_id,
    current_group, require_group, admin_logged_in_for,
    rate_limit, client_ip,
    normalize_answer, SYNONYMS, edit_distance, is_synonym, smart_check, check_answer,
    ok, err,
)

# ====================================================================
# 8. HTMLページのルーティング(どのURLで何を表示するか)
# ====================================================================


@app.route('/')
def page_home():
    # トップページ。ログイン済みならグループへ、まだならエントリー画面へ。
    if current_group():
        return redirect(url_for('page_group'))
    return render_template('entry.html')


@app.route('/create')
def page_create():
    # 新しいグループを作るページ
    return render_template('create_group.html')


@app.route('/group/guide')
def page_group_guide():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('group.html', group=grp, group_id=session.get('group_id'), show_guide=True)

@app.route('/group')
def page_group():
    # グループに入ったあとのメイン画面(クイズ一覧)
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    # イベントグループなら管理ページへリダイレクト
    import sqlite3 as _sq_grp
    _conn_grp = _sq_grp.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    _ev = _conn_grp.execute('SELECT event_key FROM events WHERE group_id=? AND is_published=1', (str(grp['id']),)).fetchone()
    _conn_grp.close()
    if _ev:
        return redirect('/event/' + _ev[0] + '/manage/')
    return render_template('group.html', group=grp, group_id=session.get('group_id'))


@app.route('/answer/<quiz_id>')
def page_answer(quiz_id):
    # クイズに答えるページ
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    # クイズが本当にそのグループのものかチェック
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT id, author_name, class_name, question, answer_options, has_options, tags, created_at
            FROM quizzes WHERE id = %s AND group_id = %s
        '''), (quiz_id, grp['id']))
        quiz = cur.fetchone()
    if not quiz:
        return redirect(url_for('page_group'))
    quiz = dict(quiz)
    # 調査中のクイズは解けないようにする(利用規約違反の疑いを調べている間)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT under_review, review_reason FROM quizzes WHERE id = %s'), (quiz_id,))
        r = cur.fetchone()
    if r and dict(r).get('under_review'):
        from markupsafe import escape
        reason = dec(dict(r).get('review_reason') or '')
        if not reason:
            reason = '利用規約違反の可能性があるため調査中です。'
        return ('<html><head><meta charset="utf-8"><title>調査中</title></head>'
                '<body style="font-family:sans-serif;text-align:center;'
                'padding-top:80px;background:#f0f4f8;">'
                '<div style="background:white;display:inline-block;'
                'padding:40px 60px;border-radius:12px;max-width:600px;">'
                '<h2>このクイズはいま解けません</h2>'
                '<p style="white-space:pre-wrap;text-align:left;">' + str(escape(reason)) + '</p>'
                '<p><a href="' + url_for('page_group') + '">クイズ一覧にもどる</a></p>'
                '</div></body></html>')
    # JSONっぽく解釈(SQLiteはJSONをTEXTで保存してる)
    opts = quiz.get('answer_options')
    if isinstance(opts, str) and opts:
        try:
            quiz['answer_options'] = json.loads(opts)
        except Exception:
            quiz['answer_options'] = None
    quiz['tags'] = [t for t in (quiz.get('tags') or '').split(',') if t]
    # APIキーが設定されていればai_scoringをTrueで渡す
    import sqlite3 as _sq_ans
    _conn_ans = _sq_ans.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    _row_ans = _conn_ans.execute('SELECT ai_api_key, cf_api_token FROM groups WHERE id=?', (grp['id'],)).fetchone()
    _conn_ans.close()
    _has_key = bool(_row_ans and (_row_ans[0] or _row_ans[1]))
    return render_template('answer.html', quiz=quiz, group=grp, group_id=session.get('group_id'),
                           ai_scoring=bool(grp.get('ai_scoring', 0)) or _has_key)


@app.route('/setting/')
def page_admin_top():
    # 管理者トップページ（グループ一覧）
    return render_template('admin_top.html')

@app.route('/api/admin/groups', methods=['GET'])
def api_admin_groups():
    # 全グループ一覧を返す（管理者パスワード必須）
    pw = request.args.get('pw', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT g.id, g.name, g.color, g.view_only, g.group_id_hash,
                   (SELECT COUNT(*) FROM quizzes WHERE group_id=g.id) as quiz_count,
                   g.created_at, g.is_official, g.school_mode
            FROM groups g ORDER BY g.created_at DESC
        '''))
        rows = [dict(r) for r in cur.fetchall()]
    # group_id_hashからグループIDは復元できないので、
    # セッションから取れるグループIDをsetting URLとして返す
    for r in rows:
        r['id'] = str(r['id'])
        r['created_at'] = str(r['created_at'])
        r['view_only'] = bool(r['view_only'])
        r['is_official'] = bool(r.get('is_official', 0))
        r['school_mode'] = bool(r.get('school_mode', 0))
        r.pop('group_id_hash', None)
    return ok(groups=rows)

@app.route('/<group_id>/setting/')
def page_admin_entry(group_id):
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT id, name, view_only
            FROM groups WHERE group_id_hash = %s
        '''), (hash_group_id(group_id),))
        row = cur.fetchone()
    group_info = dict(row) if row else None
    # イベントグループか確認
    event = None
    if group_info:
        import sqlite3 as _sq_adm
        _conn_adm = _sq_adm.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
        ev_row = _conn_adm.execute('SELECT id,event_key,title,start_date,end_date,result_date,ip_restrict FROM events WHERE group_id=?',
                                    (str(group_info['id']),)).fetchone()
        _conn_adm.close()
        if ev_row:
            event = {'id':ev_row[0],'event_key':ev_row[1],'title':ev_row[2],
                     'start_date':ev_row[3],'end_date':ev_row[4],'result_date':ev_row[5],'ip_restrict':bool(ev_row[6]) if len(ev_row)>6 else False}
    return render_template(
        'admin.html',
        group_id=group_id,
        group_info=group_info,
        logged_in=bool(group_info) and admin_logged_in_for(group_info['id']),
        event=event,
    )


@app.route('/terms')
def page_terms():
    # 利用規約ページ
    return render_template('terms.html')


@app.route('/privacy')
def page_privacy():
    # プライバシーポリシーページ
    return render_template('privacy.html')


@app.route('/help')
def page_help():
    # ヘルプページ
    return render_template('help.html')


# ====================================================================
# 9. API(JavaScriptから呼ばれる、画面をリロードせずに動かすやつ)
# ====================================================================

@app.route('/api/session', methods=['GET'])
def api_session():
    # いまの自分の状態を返す
    grp = current_group()
    if not grp:
        return ok(logged_in=False)
    return ok(
        logged_in=True,
        group={
            'id': str(grp['id']),
            'name': grp['name'],
            'color': grp['color'],
            'view_only': bool(grp['view_only']),
            'has_admin': bool(grp.get('admin_password_hash')),
        },
        group_id=session.get('group_id'),
    )


@app.route('/api/groups', methods=['POST'])
def api_create_group():
    # 新しいグループを作る
    if not rate_limit(f'create:{client_ip()}', 10):
        return err('リクエストが多すぎるよ。少し待ってね。', 429)

    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    name = (data.get('name') or '').strip()
    color = data.get('color') or '#E85A8A'
    admin_password = data.get('admin_password') or ''
    agreed = bool(data.get('agreed_terms'))
    not_illegal = bool(data.get('confirm_not_illegal'))

    # 利用規約の同意チェック(同意してない人は作らせない)
    if not agreed:
        return err('利用規約への同意が必要です', 400)
    if not not_illegal:
        return err('法律や他人の権利を守ることへの確認が必要です', 400)

    # 入力のチェック
    import re
    if not re.match(r'^[a-zA-Z0-9_-]{6,32}$', group_id):
        return err('グループIDは半角英数字・ハイフン・アンダースコアで6〜32文字にしてね')
    if not name or len(name) > 50:
        return err('グループ名は1〜50文字で入力してね')
    if not re.match(r'^#[0-9A-Fa-f]{6}$', color):
        return err('色の形式が正しくないよ')

    pw_hash = None
    if admin_password:
        if len(admin_password) < 6 or len(admin_password) > 100:
            return err('管理者パスワードは6〜100文字で入力してね')
        pw_hash = hash_password(admin_password)

    gid_hash = hash_group_id(group_id)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (gid_hash,))
        if cur.fetchone():
            return err('このグループIDはもう使われているよ。違うIDにしてね。', 409)

        if USE_POSTGRES:
            gp_hash = None
            group_password = data.get('group_password') or ''
            if group_password:
                if len(group_password) < 4:
                    pass
                else:
                    gp_hash = hash_password(group_password)
            cur.execute(q('''
                INSERT INTO groups (group_id_hash, name, color, admin_password_hash, group_password_hash)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            '''), (gid_hash, name, color, pw_hash, gp_hash))
            new_group_id = cur.fetchone()['id']
        else:
            gp_hash = None
            group_password = data.get('group_password') or ''
            if group_password and len(group_password) >= 4:
                gp_hash = hash_password(group_password)
            new_group_id = new_id()
            cur.execute(q('''
                INSERT INTO groups (id, group_id_hash, name, color, admin_password_hash, group_password_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
            '''), (new_group_id, gid_hash, name, color, pw_hash, gp_hash))

    # ログイン状態にする
    session.clear()
    session['group_id'] = group_id
    return ok(redirect='/group')


@app.route('/api/login', methods=['POST'])
def api_login():
    if not rate_limit(f'login:{client_ip()}', 20):
        return err('ログインが多すぎるよ。少し待ってね。', 429)
    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    group_password = data.get('group_password') or ''
    if not group_id:
        return err('グループIDを入力してね')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, group_password_hash FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループIDが正しくないよ', 403)
    row = dict(row)
    stored_gp = row.get('group_password_hash')
    if stored_gp:
        if not group_password:
            return err('パスワードが必要だよ', 401)
        if not verify_password(group_password, stored_gp):
            return err('パスワードが違うよ', 401)
    session.clear()
    session['group_id'] = group_id
    # イベントグループならイベントページへリダイレクト
    import sqlite3 as _sq_ev
    _conn_ev = _sq_ev.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    _ev_row = _conn_ev.execute('SELECT event_key FROM events WHERE group_id=? AND is_published=1', 
                                (str(row['id']),)).fetchone()
    _conn_ev.close()
    _event_redirect = '/event/' + _ev_row[0] + '/manage/' if _ev_row else None
    _grp_id_for_event = str(dict(row)['id'])
    # アクセスログ記録
    try:
        _grp_row = dict(row)
        _ua = request.headers.get('User-Agent', '')
        if 'Windows' in _ua: _dev = 'Windows PC'
        elif 'Mac' in _ua: _dev = 'Mac'
        elif 'iPhone' in _ua: _dev = 'iPhone'
        elif 'Android' in _ua: _dev = 'Android'
        elif 'iPad' in _ua: _dev = 'iPad'
        else: _dev = 'その他'
        import hashlib as _hl
        _ip_hash = _hl.sha256(client_ip().encode()).hexdigest()[:16]
        import sqlite3 as _sqlog
        _lconn = _sqlog.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
        _lconn.execute(
            'INSERT INTO access_logs (group_id, nickname, device, ip_hash) VALUES (?, ?, ?, ?)',
            (_grp_row.get('id', ''), '（ログイン）', _dev, _ip_hash)
        )
        _lconn.commit()
        _lconn.close()
    except Exception as _le:
        print(f'アクセスログエラー: {_le}')
    return ok(redirect=_event_redirect or '/group')


@app.route('/api/groups/check', methods=['POST'])
def api_check_group():
    data = request.get_json(silent=True) or {}
    group_id = (data.get('group_id') or '').strip()
    if not group_id:
        return err('グループIDを入力してね')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, group_password_hash FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループIDが正しくないよ', 403)
    row = dict(row)
    return ok(has_password=bool(row.get('group_password_hash')))


@app.route('/api/logout', methods=['POST'])
def api_logout():
    # ログアウトする(セッションを消す)
    session.clear()
    return ok(redirect='/')


@app.route('/api/group', methods=['PATCH'])
def api_update_group():
    # グループ名や色を変える
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    data = request.get_json(silent=True) or {}
    updates = []
    values = []

    name = data.get('name')
    color = data.get('color')

    if name is not None:
        name = name.strip()
        if not name or len(name) > 50:
            return err('グループ名は1〜50文字で入力してね')
        updates.append('name = %s')
        values.append(name)

    if color is not None:
        import re
        if not re.match(r'^#[0-9A-Fa-f]{6}$', color):
            return err('色の形式が正しくないよ')
        updates.append('color = %s')
        values.append(color)

    if not updates:
        return ok()

    values.append(grp['id'])
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q(f'UPDATE groups SET {", ".join(updates)} WHERE id = %s'), values)

    return ok()


@app.route('/api/quizzes', methods=['GET'])
def api_list_quizzes():
    # クイズの一覧を、統計ふきんきにしてかえす
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT q.id, q.author_name AS name, q.class_name, q.question,
                   q.answer_options, q.has_options, q.tags, q.created_at,
                   CASE WHEN q.edit_password_hash IS NOT NULL THEN 1 ELSE 0 END AS has_edit_pw,
                   (SELECT COUNT(*) FROM attempts WHERE quiz_id = q.id) AS attempts,
                   (SELECT COUNT(*) FROM attempts WHERE quiz_id = q.id AND correct = 1) AS corrects,
                   (SELECT COALESCE(AVG(difficulty), 0) FROM feedbacks WHERE quiz_id = q.id) AS avg_difficulty,
                   (SELECT COUNT(*) FROM feedbacks WHERE quiz_id = q.id) AS feedback_count
            FROM quizzes q
            WHERE q.group_id = %s
            ORDER BY q.created_at DESC
        '''), (grp['id'],))
        rows = cur.fetchall()

    quizzes = []
    for r in rows:
        row = dict(r)
        # 選択肢を配列に戻す
        opts = row.get('answer_options')
        if isinstance(opts, str) and opts:
            try:
                row['answer_options'] = json.loads(opts)
            except Exception:
                row['answer_options'] = None
        row['tags'] = [t for t in (row.get('tags') or '').split(',') if t]
        row['id'] = str(row['id'])
        row['attempts'] = int(row.get('attempts') or 0)
        row['corrects'] = int(row.get('corrects') or 0)
        row['avg_difficulty'] = float(row.get('avg_difficulty') or 0)
        row['feedback_count'] = int(row.get('feedback_count') or 0)
        row['has_options'] = bool(row.get('has_options'))
        row['has_edit_pw'] = bool(row.get('has_edit_pw'))
        row['created_at'] = str(row['created_at'])
        quizzes.append(row)

    return ok(quizzes=quizzes, view_only=bool(grp['view_only']))


@app.route('/api/quizzes', methods=['POST'])
def api_create_quiz():
    # 新しいクイズを追加する
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    class_name = (data.get('class_name') or '').strip()
    question = (data.get('question') or '').strip()
    answer = (data.get('answer') or '').strip()
    options = data.get('answer_options')
    tags = data.get('tags') or []

    # 違法行為がないかの最終確認
    if not data.get('confirm_not_illegal'):
        return err('法律・他人の権利を守ったクイズであることの確認が必要だよ', 400)

    if not name or len(name) > 30:
        return err('名前は1〜30文字で入力してね')
    if not question or len(question) > 500:
        return err('問題は1〜500文字で入力してね')
    if not answer or len(answer) > 500:
        return err('答えは1〜500文字で入力してね')

    has_options = False
    options_json = None
    if isinstance(options, list) and len(options) >= 2:
        if len(options) > 6:
            return err('選択肢は2〜6個までだよ')
        cleaned = [str(o).strip() for o in options]
        cleaned = [o for o in cleaned if 0 < len(o) <= 100]
        if len(cleaned) < 2:
            return err('選択肢は2つ以上の有効な項目が必要だよ')
        if not any(normalize_answer(o) == normalize_answer(answer) for o in cleaned):
            return err('正解が選択肢の中に入ってないよ')
        has_options = True
        options_json = json.dumps(cleaned, ensure_ascii=False)

    # タグをきれいにする
    if not isinstance(tags, list):
        tags = []
    clean_tags = []
    for t in tags:
        t = str(t).strip()
        if 0 < len(t) <= 20 and t not in clean_tags:
            clean_tags.append(t)
    clean_tags = clean_tags[:10]
    tags_str = ','.join(clean_tags)

    # 複数正解の処理
    data_answers = data.get('answers') or []
    if isinstance(data_answers, list) and len(data_answers) > 0:
        clean_answers = [str(a).strip() for a in data_answers if str(a).strip()]
        if answer not in clean_answers:
            clean_answers.insert(0, answer)
        answers_json_str = json.dumps(clean_answers, ensure_ascii=False)
    else:
        answers_json_str = json.dumps([answer], ensure_ascii=False)

    # 編集パスワード
    edit_pw = data.get('edit_password') or ''
    edit_pw_hash = hash_password(edit_pw) if edit_pw else None
    explanation = (data.get('explanation') or '').strip()[:1000]
    hint = (data.get('hint') or '').strip()[:300]

    with get_db() as conn:
        cur = make_cursor(conn)
        if USE_POSTGRES:
            cur.execute(q('''
                INSERT INTO quizzes (group_id, author_name, class_name, question, answer,
                                     answer_options, has_options, tags, answers, edit_password_hash, explanation, hint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at
            '''), (grp['id'], name, class_name, question, answer,
                   options_json, 1 if has_options else 0, tags_str,
                   answers_json_str, edit_pw_hash, explanation, hint))
            row = cur.fetchone()
            new_quiz_id = str(row['id'])
            created_at = str(row['created_at'])
        else:
            new_quiz_id = new_id()
            cur.execute(q('''
                INSERT INTO quizzes (id, group_id, author_name, class_name, question, answer,
                                     answer_options, has_options, tags, answers, edit_password_hash, explanation, hint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''), (new_quiz_id, grp['id'], name, class_name, question, answer,
                   options_json, 1 if has_options else 0, tags_str,
                   answers_json_str, edit_pw_hash, explanation, hint))
            import pytz as _pytz
            _jst = _pytz.timezone('Asia/Tokyo')
            created_at = datetime.now(_jst).strftime('%Y-%m-%d %H:%M:%S')

    return ok(quiz={
        'id': new_quiz_id,
        'name': name,
        'class_name': class_name,
        'question': question,
        'answer_options': json.loads(options_json) if options_json else None,
        'has_options': has_options,
        'has_edit_pw': bool(edit_pw_hash),
        'tags': clean_tags,
        'created_at': created_at,
        'attempts': 0, 'corrects': 0,
        'avg_difficulty': 0, 'feedback_count': 0,
    })


@app.route('/api/quizzes/<quiz_id>', methods=['DELETE'])
def api_delete_quiz(quiz_id):
    # クイズを消す
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    with get_db() as conn:
        cur = make_cursor(conn)
        # 他のグループのクイズは消せないようにする
        cur.execute(q('DELETE FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if cur.rowcount == 0:
            return err('そのクイズは見つからないよ', 404)
        # クイズを消したら、それに関する挑戦記録と感想もきれいに消す
        cur.execute(q('DELETE FROM attempts WHERE quiz_id = %s'), (quiz_id,))
        cur.execute(q('DELETE FROM feedbacks WHERE quiz_id = %s'), (quiz_id,))
    return ok()


@app.route('/api/quizzes/<quiz_id>/answer', methods=['POST'])
def api_answer_quiz(quiz_id):
    # クイズの答え合わせをする(サーバーで判定するから、答えがブラウザに漏れない)
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    data = request.get_json(silent=True) or {}
    user_answer = str(data.get('user_answer', ''))[:500]
    time_ms = int(data.get('time_ms') or 0)
    # タイムは0〜2時間まで
    time_ms = max(0, min(7200000, time_ms))

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT answer, answers, explanation, hint, under_review, review_reason FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
        if not row:
            return err('クイズが見つからないよ', 404)
        if dict(row).get('under_review'):
            reason = dec(dict(row).get('review_reason') or '')
            msg = 'このクイズは調査中だよ' + ('。理由: ' + reason if reason else '')
            return err(msg, 403)
        row_dict = dict(row)
        correct_answer = dec(row_dict.get('answer') or '')
        answers_raw = row_dict.get('answers')
        if answers_raw:
            try:
                ans_list = json.loads(answers_raw)
                row_dict['answers'] = json.dumps([dec(a) for a in ans_list], ensure_ascii=False)
            except Exception:
                pass
        row_dict['answer'] = correct_answer
        is_correct = check_answer(user_answer, row_dict)
        # 採点詳細を取得
        match_reason = 'wrong'
        ua = normalize_answer(user_answer)
        answers_json2 = row_dict.get('answers')
        try:
            answers2 = json.loads(answers_json2) if answers_json2 else [correct_answer]
        except:
            answers2 = [correct_answer]
        for a2 in answers2:
            _, reason2 = smart_check(ua, normalize_answer(dec(a2)))
            if reason2 != 'wrong':
                match_reason = reason2
                break

        if USE_POSTGRES:
            cur.execute(q('INSERT INTO attempts (quiz_id, correct, time_ms) VALUES (%s, %s, %s)'),
                        (quiz_id, 1 if is_correct else 0, time_ms))
        else:
            cur.execute(q('INSERT INTO attempts (id, quiz_id, correct, time_ms) VALUES (%s, %s, %s, %s)'),
                        (new_id(), quiz_id, 1 if is_correct else 0, time_ms))

    reason_msg = {
        'exact': None,
        'synonym': '💡 類義語として正解！',
        'typo': '💡 タイポを許容して正解！',
        'partial': '💡 部分一致で正解！',
        'wrong': None,
    }.get(match_reason)
    return ok(correct=is_correct, correct_answer=correct_answer, time_ms=time_ms,
              explanation=dec(row_dict.get('explanation') or ''),
              hint=dec(row_dict.get('hint') or ''),
              match_reason=match_reason, match_msg=reason_msg)


@app.route('/api/quizzes/<quiz_id>/feedback', methods=['POST'])
def api_feedback(quiz_id):
    # 感想と難易度を送る
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    data = request.get_json(silent=True) or {}
    try:
        difficulty = int(data.get('difficulty'))
    except Exception:
        return err('難易度は1〜5の整数で指定してね')
    if difficulty < 1 or difficulty > 5:
        return err('難易度は1〜5で指定してね')

    comment = str(data.get('comment') or '').strip()
    if len(comment) > 500:
        return err('感想は500文字以内で入力してね')

    if not data.get('confirm_not_illegal'):
        return err('感想が他人をきずつける内容でないことの確認が必要だよ', 400)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)

        if USE_POSTGRES:
            cur.execute(q('INSERT INTO feedbacks (quiz_id, difficulty, comment) VALUES (%s, %s, %s)'),
                        (quiz_id, difficulty, comment))
        else:
            cur.execute(q('INSERT INTO feedbacks (id, quiz_id, difficulty, comment) VALUES (%s, %s, %s, %s)'),
                        (new_id(), quiz_id, difficulty, comment))

    return ok()


@app.route('/api/quizzes/<quiz_id>/stats', methods=['GET'])
def api_stats(quiz_id):
    # 統計情報(挑戦数、正解率、難易度など)を返す
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)

        cur.execute(q('''
            SELECT
              (SELECT COUNT(*)               FROM attempts WHERE quiz_id = %s) AS attempts,
              (SELECT COUNT(*)               FROM attempts WHERE quiz_id = %s AND correct = 1) AS corrects,
              (SELECT COALESCE(AVG(time_ms),0) FROM attempts WHERE quiz_id = %s) AS avg_time_ms,
              (SELECT COALESCE(MIN(time_ms),0) FROM attempts WHERE quiz_id = %s AND correct = 1) AS fastest_ms,
              (SELECT COALESCE(AVG(difficulty),0) FROM feedbacks WHERE quiz_id = %s) AS avg_difficulty,
              (SELECT COUNT(*)               FROM feedbacks WHERE quiz_id = %s) AS feedback_count
        '''), (quiz_id, quiz_id, quiz_id, quiz_id, quiz_id, quiz_id))
        s = dict(cur.fetchone())

        cur.execute(q('''
            SELECT difficulty, comment, created_at FROM feedbacks
            WHERE quiz_id = %s AND comment <> ''
            ORDER BY created_at DESC LIMIT 10
        '''), (quiz_id,))
        recent = [dict(r) for r in cur.fetchall()]
        for r in recent:
            r['created_at'] = str(r['created_at'])

    return ok(
        stats={
            'attempts': int(s['attempts']),
            'corrects': int(s['corrects']),
            'avg_time_ms': int(s['avg_time_ms']),
            'fastest_ms': int(s['fastest_ms']),
            'avg_difficulty': float(s['avg_difficulty']),
            'feedback_count': int(s['feedback_count']),
        },
        recent_feedbacks=recent,
    )


# ====================================================================
# 10. 管理者(あなた)用API
# ====================================================================

@app.route('/api/admin/login/<group_id>', methods=['POST'])
def api_admin_login(group_id):
    # 管理者のパスワードチェック
    if not rate_limit(f'adminlogin:{client_ip()}', 10):
        return err('ログイン試行が多すぎるよ。少し待ってね。', 429)

    data = request.get_json(silent=True) or {}
    password = data.get('password') or ''
    if not password:
        return err('パスワードを入力してね')

    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_password:
        return err('管理者パスワードが設定されていないよ', 403)
    if not hmac.compare_digest(password, admin_password):
        return err('パスワードが違うよ', 401)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id(group_id),))
        row = cur.fetchone()

    if not row:
        return err('グループが見つからないよ', 404)

    session['admin'] = {
        'group_uuid': str(row['id']),
        'expires_at': time.time() + 3600,
    }
    return ok()


@app.route('/api/admin/logout/<group_id>', methods=['POST'])
def api_admin_logout(group_id):
    # 管理者としてのログアウト(通常のグループログインはそのまま)
    session.pop('admin', None)
    return ok()


@app.route('/api/admin/view-only/<group_id>', methods=['POST'])
def api_admin_view_only(group_id):
    # 閲覧のみモードのON/OFFを切り替える
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループが見つからないよ', 404)
    if not admin_logged_in_for(row['id']):
        return err('管理者としてログインしてね', 401)

    data = request.get_json(silent=True) or {}
    view_only = 1 if data.get('view_only') else 0

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('UPDATE groups SET view_only = %s WHERE id = %s'), (view_only, row['id']))

    return ok(view_only=bool(view_only))


@app.route('/api/admin/delete-group/<group_id>', methods=['POST'])
def api_admin_delete_group(group_id):
    # グループを完全に削除する
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループが見つからないよ', 404)
    group_uuid = row['id']
    if not admin_logged_in_for(group_uuid):
        return err('管理者としてログインしてね', 401)

    data = request.get_json(silent=True) or {}
    if (data.get('confirm_group_id') or '').strip() != group_id:
        return err('確認用のグループIDが一致しないよ', 400)

    with get_db() as conn:
        cur = make_cursor(conn)
        # 関係ある情報を全部消す(クイズ→挑戦・感想は外部キーで連鎖削除)
        cur.execute(q('DELETE FROM attempts WHERE quiz_id IN (SELECT id FROM quizzes WHERE group_id = %s)'), (group_uuid,))
        cur.execute(q('DELETE FROM feedbacks WHERE quiz_id IN (SELECT id FROM quizzes WHERE group_id = %s)'), (group_uuid,))
        cur.execute(q('DELETE FROM quizzes WHERE group_id = %s'), (group_uuid,))
        cur.execute(q('DELETE FROM groups WHERE id = %s'), (group_uuid,))

    # ログイン情報もクリア
    session.clear()
    return ok()



@app.route("/api/admin/quizzes/<group_id>", methods=["GET"])
def api_admin_quizzes(group_id):
    # 管理者がクイズ一覧を取得する
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("SELECT id FROM groups WHERE group_id_hash = %s"), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err("グループが見つからないよ", 404)
    if not admin_logged_in_for(row["id"]):
        return err("管理者としてログインしてね", 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("SELECT id, author_name, question, created_at, under_review, review_reason FROM quizzes WHERE group_id = %s ORDER BY created_at DESC"), (row["id"],))
        quizzes = [dict(r) for r in cur.fetchall()]
    for q2 in quizzes:
        q2["id"]          = str(q2["id"])
        q2["created_at"]  = str(q2["created_at"])
        # # 暗号化されたフィールドを復号して返す
        q2["author_name"] = dec(q2.get("author_name") or "")
        q2["question"]    = dec(q2.get("question") or "")
        q2["under_review"] = 1 if q2.get("under_review") else 0
        q2["review_reason"] = dec(q2.get("review_reason") or "")
    return ok(quizzes=quizzes)

@app.route("/api/admin/quizzes/<group_id>/<quiz_id>", methods=["DELETE"])
def api_admin_delete_quiz(group_id, quiz_id):
    # 管理者がクイズを削除する
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("SELECT id FROM groups WHERE group_id_hash = %s"), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err("グループが見つからないよ", 404)
    if not admin_logged_in_for(row["id"]):
        return err("管理者としてログインしてね", 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("DELETE FROM attempts WHERE quiz_id = %s"), (quiz_id,))
        cur.execute(q("DELETE FROM feedbacks WHERE quiz_id = %s"), (quiz_id,))
        cur.execute(q("DELETE FROM quizzes WHERE id = %s AND group_id = %s"), (quiz_id, row["id"]))
    return ok()

@app.route("/api/admin/quizzes/<group_id>/<quiz_id>/review", methods=["POST"])
def api_admin_review_quiz(group_id, quiz_id):
    # 管理者がクイズの調査中フラグと理由を設定する
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("SELECT id FROM groups WHERE group_id_hash = %s"), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err("グループが見つからないよ", 404)
    if not admin_logged_in_for(row["id"]):
        return err("管理者としてログインしてね", 401)
    data = request.get_json(silent=True) or {}
    flag = 1 if data.get("under_review") else 0
    reason = str(data.get("reason") or "")[:200]
    enc_reason = enc(reason) if (flag and reason) else None
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q("UPDATE quizzes SET under_review = %s, review_reason = %s WHERE id = %s AND group_id = %s"),
                    (flag, enc_reason, quiz_id, row["id"]))
    return ok()

@app.route('/api/quizzes/<quiz_id>/ranking', methods=['POST'])
def api_register_ranking(quiz_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    display_name = (data.get('display_name') or '').strip()
    time_ms = int(data.get('time_ms') or 0)
    if not display_name or len(display_name) > 20:
        return err('表示名は1〜20文字で入力してね')
    if time_ms <= 0:
        return err('タイムが正しくないよ')
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)
        if USE_POSTGRES:
            cur.execute(q('INSERT INTO rankings (quiz_id, display_name, time_ms) VALUES (%s, %s, %s)'),
                        (quiz_id, display_name, time_ms))
        else:
            cur.execute(q('INSERT INTO rankings (id, quiz_id, display_name, time_ms) VALUES (%s, %s, %s, %s)'),
                        (new_id(), quiz_id, display_name, time_ms))
    return ok()


@app.route('/api/group/rankings', methods=['GET'])
def api_group_rankings():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''
            SELECT q.id, q.question, q.author_name,
                   (SELECT COUNT(*) FROM rankings WHERE quiz_id = q.id) AS ranking_count
            FROM quizzes q WHERE q.group_id = %s
            ORDER BY q.created_at DESC
        '''), (grp['id'],))
        quizzes = [dict(r) for r in cur.fetchall()]
    for q2 in quizzes:
        q2['id'] = str(q2['id'])
        q2['ranking_count'] = int(q2.get('ranking_count') or 0)
    return ok(quizzes=quizzes)


@app.route('/api/quizzes/<quiz_id>/rankings', methods=['GET'])
def api_quiz_rankings(quiz_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)
        cur.execute(q('''
            SELECT display_name, time_ms, created_at
            FROM rankings WHERE quiz_id = %s
            ORDER BY time_ms ASC LIMIT 20
        '''), (quiz_id,))
        rankings = [dict(r) for r in cur.fetchall()]
    for r in rankings:
        r['created_at'] = str(r['created_at'])
    return ok(rankings=rankings)


@app.route('/ranking')
def page_ranking():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('ranking.html', group=grp, group_id=session.get('group_id'))


@app.route('/api/admin/enter/<group_id>', methods=['POST'])
def api_admin_enter(group_id):
    # 管理者がグループに入る(パスワードなしで入れる)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループが見つからないよ', 404)
    if not admin_logged_in_for(row['id']):
        return err('管理者としてログインしてね', 401)
    session['group_id'] = group_id
    return ok(redirect='/group')


@app.route('/api/quizzes/<quiz_id>', methods=['GET'])
def api_get_quiz(quiz_id):
    # クイズ1件の詳細を取得(編集用)
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('''SELECT id, author_name, class_name, question, answer,
                          answer_options, has_options, tags, answers,
                          CASE WHEN edit_password_hash IS NOT NULL THEN 1 ELSE 0 END AS has_edit_pw
                   FROM quizzes WHERE id = %s AND group_id = %s'''),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    quiz = dict(row)
    opts = quiz.get('answer_options')
    if isinstance(opts, str) and opts:
        try:
            quiz['answer_options'] = json.loads(opts)
        except Exception:
            quiz['answer_options'] = None
    quiz['tags'] = [t for t in (quiz.get('tags') or '').split(',') if t]
    ans = quiz.get('answers')
    if isinstance(ans, str) and ans:
        try:
            quiz['answers'] = json.loads(ans)
        except Exception:
            quiz['answers'] = [quiz['answer']]
    else:
        quiz['answers'] = [quiz['answer']]
    quiz['id'] = str(quiz['id'])
    quiz['has_options'] = bool(quiz.get('has_options'))
    quiz['has_edit_pw'] = bool(quiz.get('has_edit_pw'))
    return ok(quiz=quiz)


@app.route('/api/quizzes/<quiz_id>/edit', methods=['POST'])
def api_edit_quiz(quiz_id):
    # クイズ編集: パスワードが設定されてないクイズは編集できない
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('このグループは閲覧のみモードだよ', 403)

    data = request.get_json(silent=True) or {}

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT edit_password_hash FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    row = dict(row)
    stored = row.get('edit_password_hash')
    if not stored:
        return err('このクイズは編集できないよ', 403)

    pw = data.get('edit_password') or ''
    if not verify_password(pw, stored):
        return err('編集パスワードが違うよ', 401)

    question = (data.get('question') or '').strip()
    answer = (data.get('answer') or '').strip()
    if not question or len(question) > 500:
        return err('問題は1〜500文字で入力してね')
    if not answer:
        return err('答えを入力してね')

    data_answers = data.get('answers') or []
    if isinstance(data_answers, list) and data_answers:
        clean_answers = [str(a).strip() for a in data_answers if str(a).strip()]
        answers_json_str = json.dumps(clean_answers, ensure_ascii=False)
    else:
        answers_json_str = json.dumps([answer], ensure_ascii=False)

    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('UPDATE quizzes SET question=%s, answer=%s, answers=%s WHERE id=%s AND group_id=%s'),
                    (question, answer, answers_json_str, quiz_id, grp['id']))
    return ok()


@app.route('/api/quizzes/<quiz_id>/upload', methods=['POST'])
def api_upload_image(quiz_id):
    # 画像をアップロードする(3枚まで)
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT 1 FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        if not cur.fetchone():
            return err('クイズが見つからないよ', 404)
        cur.execute(q('SELECT COUNT(*) as cnt FROM quiz_images WHERE quiz_id = %s'), (quiz_id,))
        cnt = dict(cur.fetchone())['cnt']
        if int(cnt) >= 3:
            return err('画像は3枚までだよ', 400)

    if 'file' not in request.files:
        return err('ファイルがないよ', 400)
    f = request.files['file']
    if not f.filename:
        return err('ファイル名がないよ', 400)
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in {'png', 'jpg', 'jpeg', 'gif', 'webp'}:
        return err('png/jpg/gif/webpのみアップロードできるよ', 400)
    if len(f.read()) > 5 * 1024 * 1024:
        return err('ファイルは5MB以下にしてね', 400)
    f.seek(0)

    upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = new_id() + '.' + ext
    f.save(os.path.join(upload_dir, filename))

    with get_db() as conn:
        cur = make_cursor(conn)
        if USE_POSTGRES:
            cur.execute(q('INSERT INTO quiz_images (quiz_id, filename) VALUES (%s, %s)'),
                        (quiz_id, filename))
        else:
            cur.execute(q('INSERT INTO quiz_images (id, quiz_id, filename) VALUES (%s, %s, %s)'),
                        (new_id(), quiz_id, filename))
    return ok(filename=filename, url='/static/uploads/' + filename)


@app.route('/api/quizzes/<quiz_id>/images', methods=['GET'])
def api_get_images(quiz_id):
    # クイズの画像一覧を返す
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT filename FROM quiz_images WHERE quiz_id = %s ORDER BY created_at'),
                    (quiz_id,))
        images = [{'filename': r['filename'] if hasattr(r, '__getitem__') else r[0],
                   'url': '/static/uploads/' + (r['filename'] if hasattr(r, '__getitem__') else r[0])}
                  for r in cur.fetchall()]
    return ok(images=images)


@app.route('/api/quizzes/random', methods=['GET'])
def api_random_quiz():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import random
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM quizzes WHERE group_id = %s'), (grp['id'],))
        ids = [str(dict(r)['id']) for r in cur.fetchall()]
    if not ids:
        return err('クイズがないよ', 404)
    return ok(quiz_id=random.choice(ids))


@app.route('/api/sets', methods=['GET'])
def api_list_sets():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, name, quiz_ids, created_at FROM quiz_sets WHERE group_id = %s ORDER BY created_at DESC'), (grp['id'],))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r['id'] = str(r['id'])
        r['quiz_ids'] = (r.get('quiz_ids') or '').split(',') if r.get('quiz_ids') else []
        r['count'] = len(r['quiz_ids'])
        r['created_at'] = str(r['created_at'])
    return ok(sets=rows)


@app.route('/api/sets', methods=['POST'])
def api_create_set():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    if grp['view_only']:
        return err('閲覧モードでは作れないよ', 403)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    quiz_ids = data.get('quiz_ids') or []
    if not name or len(name) > 50:
        return err('セット名を1〜50文字で入力してね')
    if not isinstance(quiz_ids, list) or len(quiz_ids) < 1:
        return err('クイズを1つ以上選んでね')
    quiz_ids_str = ','.join([str(x) for x in quiz_ids])
    with get_db() as conn:
        cur = make_cursor(conn)
        if USE_POSTGRES:
            cur.execute(q('INSERT INTO quiz_sets (group_id, name, quiz_ids) VALUES (%s, %s, %s)'),
                        (grp['id'], name, quiz_ids_str))
        else:
            cur.execute(q('INSERT INTO quiz_sets (id, group_id, name, quiz_ids) VALUES (%s, %s, %s, %s)'),
                        (new_id(), grp['id'], name, quiz_ids_str))
    return ok()


@app.route('/api/sets/<set_id>', methods=['DELETE'])
def api_delete_set(set_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('DELETE FROM quiz_sets WHERE id = %s AND group_id = %s'), (set_id, grp['id']))
    return ok()


@app.route('/sets')
def page_sets():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('sets.html', group=grp, group_id=session.get('group_id'))


@app.route('/api/quizzes/<quiz_id>/hint', methods=['GET'])
def api_get_hint(quiz_id):
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT hint FROM quizzes WHERE id = %s AND group_id = %s'),
                    (quiz_id, grp['id']))
        row = cur.fetchone()
    if not row:
        return err('クイズが見つからないよ', 404)
    return ok(hint=dec(dict(row).get('hint') or ''))


@app.route('/api/group/stats', methods=['GET'])
def api_group_stats():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT COUNT(*) AS c FROM quizzes WHERE group_id = %s'), (grp['id'],))
        total_quizzes = int(dict(cur.fetchone())['c'])
        cur.execute(q('''SELECT COUNT(*) AS c FROM attempts WHERE quiz_id IN
                        (SELECT id FROM quizzes WHERE group_id = %s)'''), (grp['id'],))
        total_attempts = int(dict(cur.fetchone())['c'])
        cur.execute(q('''SELECT COUNT(*) AS c FROM attempts WHERE correct = 1 AND quiz_id IN
                        (SELECT id FROM quizzes WHERE group_id = %s)'''), (grp['id'],))
        total_corrects = int(dict(cur.fetchone())['c'])
        cur.execute(q('''SELECT q.id, q.question, q.author_name AS name,
                        (SELECT COUNT(*) FROM attempts WHERE quiz_id = q.id) AS attempts
                        FROM quizzes q WHERE q.group_id = %s
                        ORDER BY attempts DESC LIMIT 5'''), (grp['id'],))
        popular = [dict(r) for r in cur.fetchall()]
    for p in popular:
        p['id'] = str(p['id'])
        p['attempts'] = int(p['attempts'])
    return ok(total_quizzes=total_quizzes, total_attempts=total_attempts,
              total_corrects=total_corrects, popular=popular)


@app.route('/groupstats')
def page_group_stats():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('group_stats.html', group=grp, group_id=session.get('group_id'))

# ====================================================================
# 11. ヘルスチェック(Railwayが「サーバー動いてる?」って確認するため)
# ====================================================================
@app.route('/api/health')
def api_health():
    return ok()


# ====================================================================
# 12. エラーハンドラ
# ====================================================================
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return err('見つからないよ', 404)
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return err('サーバーエラーが起きたよ', 500)
    return render_template('500.html'), 500


# ====================================================================
# 13. テンプレートで使える便利な関数(Jinjaで{{ }} の中で使える)
# ====================================================================
@app.context_processor
def inject_globals():
    # 全HTMLで session と current_group が使えるようにする
    grp = current_group()
    return {
        'current_group': grp,
        'current_group_id': session.get('group_id'),
    }


# ====================================================================
# 14. サーバー起動!
# ====================================================================
# Flaskのデフォルト開発サーバーの起動(本番は gunicorn が呼び出す)
init_db()


# ===== フィードバック機能 =====

@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    # フィードバックを送信するページ
    import datetime, pytz, sqlite3 as _sq
    msg = None
    if request.method == "POST":
        star     = request.form.get("star_rating", "3")
        category = request.form.get("category", "感想")
        message  = request.form.get("message", "").strip()
        # メッセージが空なら送信しない
        if not message:
            msg = "error"
        else:
            # 日本時間で今の時刻を取得
            jst = pytz.timezone("Asia/Tokyo")
            now = datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")
            # DBに直接つないで保存する
            db_path = os.environ.get("SQLITE_PATH", "/home/yuto113/quizshare.db")
            conn = _sq.connect(db_path)
            conn.execute(
                "INSERT INTO feedback (created_at, star_rating, category, message) VALUES (?, ?, ?, ?)",
                (now, int(star), category, message)
            )
            conn.commit()
            conn.close()
            msg = "ok"
    return render_template("feedback.html", msg=msg)

@app.route("/feedback/list")
def feedback_list():
    # 管理者だけが見られるフィードバック一覧ページ
    import sqlite3 as _sq
    admin_pw  = request.args.get("pw", "")
    correct_pw = os.environ.get("ADMIN_PASSWORD", "")
    if admin_pw != correct_pw:
        # パスワードが違ったら403エラー
        return "管理者パスワードが違います", 403
    # DBに直接つないで一覧を取得する
    db_path = os.environ.get("SQLITE_PATH", "/home/yuto113/quizshare.db")
    conn = _sq.connect(db_path)
    rows = conn.execute(
        "SELECT id, created_at, star_rating, category, message FROM feedback ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("feedback_list.html", feedbacks=rows)


# 機能系はbp_library.pyに引っ越した(ライブラリ・テーマ・AI採点)
from bp_library import bp as _bp_library
app.register_blueprint(_bp_library)

# 機能系はbp_features.pyに引っ越した(学校・先生・課題・イベント・対戦・タイピング)
from bp_features import bp as _bp_features
app.register_blueprint(_bp_features)

from bp_qzero import bp as _bp_qzero
app.register_blueprint(_bp_qzero)

@app.route('/homepage')
def page_homepage():
    # 「数字で見るQZERO」用に本物の統計をDBから取る(失敗しても表示は壊さない)
    stats = {'quiz_count': 0, 'group_count': 0, 'attempt_count': 0, 'event_count': 0}
    try:
        import sqlite3 as _sq
        conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
        stats['quiz_count'] = conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0]
        stats['group_count'] = conn.execute('SELECT COUNT(*) FROM groups').fetchone()[0]
        stats['attempt_count'] = conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        stats['event_count'] = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        conn.close()
    except Exception:
        pass
    return render_template('company.html', **stats)

# ====================================================================
# 社員システム(掲示板・人事・公安・暗号・LINE)はbp_staff.pyに引っ越した
# ====================================================================
from bp_staff import bp as _bp_staff
app.register_blueprint(_bp_staff)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)

# ===== AI使用量管理 =====

@app.route('/setting_ai/')
def page_setting_ai():
    return render_template('setting_ai.html')

@app.route('/api/ai_usage/summary')
def api_ai_usage_summary():
    pw = request.args.get('pw', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    # グループ別の使用量
    rows = conn.execute('''
        SELECT g.name, g.id, 
               COALESCE(SUM(u.tokens_used), 0) as total_tokens,
               COUNT(u.id) as total_calls,
               COALESCE((SELECT SUM(tokens_used) FROM ai_usage 
                         WHERE group_id = g.id 
                         AND date(created_at) = date('now','localtime')), 0) as today_tokens,
               COALESCE(l.daily_limit, 100) as daily_limit,
               COALESCE(l.total_limit, 10000) as total_limit,
               COALESCE(g.ai_scoring, 0) as ai_scoring
        FROM groups g
        LEFT JOIN ai_usage u ON u.group_id = g.id
        LEFT JOIN ai_limits l ON l.group_id = g.id
        GROUP BY g.id
        ORDER BY total_tokens DESC
    ''').fetchall()
    total_all = conn.execute('SELECT COALESCE(SUM(tokens_used),0) FROM ai_usage').fetchone()[0]
    conn.close()
    groups = []
    for r in rows:
        groups.append({
            'name': r[0],
            'id': r[1],
            'total_tokens': int(r[2]),
            'total_calls': int(r[3]),
            'today_tokens': int(r[4]),
            'daily_limit': int(r[5]),
            'total_limit': int(r[6]),
            'ai_scoring': bool(r[7]),
        })
    return ok(groups=groups, total_all=int(total_all), global_limit=100000)

@app.route('/api/ai_limits/<group_id>', methods=['POST'])
def api_set_ai_limit(group_id):
    data = request.get_json(silent=True) or {}
    pw = data.get('password', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    daily_limit = int(data.get('daily_limit', 100))
    total_limit = int(data.get('total_limit', 10000))
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('''INSERT OR REPLACE INTO ai_limits (group_id, daily_limit, total_limit)
                    VALUES (?, ?, ?)''', (group_id, daily_limit, total_limit))
    conn.commit()
    conn.close()
    return ok(message='制限を設定しました')

# ===== SEO =====
@app.route('/robots.txt')
def robots_txt():
    content = """User-agent: *
Allow: /
Allow: /help
Allow: /terms
Allow: /privacy
Disallow: /group
Disallow: /setting/
Disallow: /setting_ai/
Disallow: /api/
Disallow: /answer/
Disallow: /ranking
Disallow: /sets
Disallow: /library
Disallow: /theme
Disallow: /feedback

Sitemap: https://yuto113.pythonanywhere.com/sitemap.xml
"""
    from flask import Response
    return Response(content, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://yuto113.pythonanywhere.com/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://yuto113.pythonanywhere.com/help</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://yuto113.pythonanywhere.com/terms</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
  <url>
    <loc>https://yuto113.pythonanywhere.com/privacy</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
</urlset>"""
    from flask import Response
    return Response(content, mimetype='application/xml')

# ===== ページスケジュール管理 =====
@app.route('/api/page_schedules', methods=['GET'])
def api_get_page_schedules():
    pw = request.args.get('pw', '')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or pw != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id,page_key,page_name,url,schedules,is_active FROM page_schedules ORDER BY id').fetchall()
    conn.close()
    return ok(pages=[{'id':r[0],'key':r[1],'name':r[2],'url':r[3],'schedules':json.loads(r[4]),'is_active':bool(r[5])} for r in rows])

@app.route('/api/page_schedules/<page_key>', methods=['POST'])
def api_update_page_schedule(page_key):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    schedules = data.get('schedules', [])
    is_active = 1 if data.get('is_active', True) else 0
    page_name = (data.get('page_name') or '').strip()
    url = (data.get('url') or '').strip()
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('''INSERT INTO page_schedules (page_key,page_name,url,schedules,is_active)
        VALUES (?,?,?,?,?)
        ON CONFLICT(page_key) DO UPDATE SET
        schedules=excluded.schedules, is_active=excluded.is_active,
        page_name=excluded.page_name, url=excluded.url''',
        (page_key, page_name, url, json.dumps(schedules, ensure_ascii=False), is_active))
    conn.commit()
    conn.close()
    return ok(message='保存しました')

@app.route('/api/page_schedules/<page_key>/delete', methods=['POST'])
def api_delete_page_schedule(page_key):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM page_schedules WHERE page_key=?', (page_key,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@app.route('/api/page_schedules/<page_key>/check', methods=['GET'])
def api_check_page_schedule(page_key):
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT schedules,is_active FROM page_schedules WHERE page_key=?', (page_key,)).fetchone()
    conn.close()
    if not row:
        return ok(open=True)
    if not row[1]:
        return ok(open=False, reason='無効化されています')
    schedules = json.loads(row[0])
    import pytz as _pytz2
    from datetime import datetime
    now = datetime.now(_pytz2.timezone('Asia/Tokyo')).replace(tzinfo=None)
    for s in schedules:
        try:
            start = datetime.fromisoformat(s['start'].replace('T', ' '))
            end = datetime.fromisoformat(s['end'].replace('T', ' '))
            if start <= now <= end:
                return ok(open=True, label=s.get('label',''))
        except:
            pass
    return ok(open=False, reason='公開期間外です')

# ===== グループ別AI API設定 =====
@app.route('/api/group/ai_config', methods=['POST'])
def api_set_group_ai_config():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}

    # 管理者が別グループを指定している場合はそちらを使う
    target_group_id = data.get('target_group_id') or grp['id']

    # 公式グループは管理者のみ設定可能
    import sqlite3 as _sq
    _conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    _row = _conn.execute('SELECT is_official FROM groups WHERE id=?', (target_group_id,)).fetchone()
    _conn.close()
    is_official = bool(_row[0]) if _row else False

    # 公式グループの場合は管理者パスワードが必要
    if is_official:
        admin_pw = os.environ.get('ADMIN_PASSWORD', '')
        pw = data.get('admin_password', '')
        if pw != admin_pw:
            return err('公式グループはサイト管理者のみAI設定できます', 403)

    cf_account = (data.get('cf_account_id') or '').strip()
    cf_token = (data.get('cf_api_token') or '').strip()
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    ai_provider = (data.get('ai_provider') or 'cloudflare').strip()
    ai_api_key = (data.get('ai_api_key') or '').strip()
    daily_limit = int(data.get('daily_limit') or 1000)
    conn.execute('''UPDATE groups SET
        cf_account_id=?, cf_api_token=?, ai_scoring=1,
        ai_provider=?, ai_api_key=? WHERE id=?''',
        (cf_account or None, cf_token or None, ai_provider, ai_api_key or None, target_group_id))
    conn.commit()
    # ai_limitsテーブルに上限を保存
    conn.execute('''INSERT OR REPLACE INTO ai_limits (group_id, daily_limit, total_limit)
        VALUES (?, ?, COALESCE((SELECT total_limit FROM ai_limits WHERE group_id=?), 100000))''',
        (target_group_id, daily_limit, target_group_id))
    conn.commit()
    conn.close()
    return ok(message='AI設定を保存しました')

@app.route('/api/group/ai_config', methods=['GET'])
def api_get_group_ai_config():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT cf_account_id, cf_api_token, ai_scoring, ai_provider, ai_api_key, is_official FROM groups WHERE id=?',
                       (grp['id'],)).fetchone()
    conn.close()
    if not row:
        return ok(has_config=False)
    is_official = bool(row[5]) if row else False
    has_admin_config = bool(row[0] and row[1])
    has_group_config = bool(row[4])
    return ok(
        has_config=has_admin_config or has_group_config,
        has_admin_config=has_admin_config,
        has_group_config=has_group_config,
        ai_provider=row[3] or 'cloudflare',
        ai_scoring=bool(row[2]),
        is_official=is_official,
    )

# ===== 公式グループ設定API =====
@app.route('/api/admin/set_official', methods=['POST'])
def api_set_official():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    group_id = data.get('group_id', '')
    is_official = 1 if data.get('is_official') else 0
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('UPDATE groups SET is_official=? WHERE id=?', (is_official, group_id))
    conn.commit()
    conn.close()
    return ok(is_official=bool(is_official))

# ===== グループのAIトークン使用量 =====
@app.route('/api/group/ai_usage', methods=['GET'])
def api_group_ai_usage():
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    # 今日の使用量
    today_used = conn.execute(
        "SELECT COALESCE(SUM(tokens_used),0) FROM ai_usage WHERE group_id=? AND date(created_at)=date('now','localtime')",
        (grp['id'],)
    ).fetchone()[0]
    # グループの設定
    row = conn.execute(
        'SELECT ai_api_key, cf_api_token, cf_account_id, ai_provider FROM groups WHERE id=?',
        (grp['id'],)
    ).fetchone()
    # 上限
    limit_row = conn.execute(
        'SELECT daily_limit FROM ai_limits WHERE group_id=?', (grp['id'],)
    ).fetchone()
    conn.close()

    has_group_key = bool(row and (row[0]))
    has_admin_key = bool(row and (row[1] and row[2]))
    daily_limit = limit_row[0] if limit_row else -1
    if daily_limit == -1: daily_limit = 1000

    return ok(
        today_used=int(today_used),
        daily_limit=daily_limit,
        has_group_key=has_group_key,
        has_admin_key=has_admin_key,
        ai_provider=row[3] if row else 'cloudflare',
    )

import shutil

# ===== 1日1回の自動DBバックアップ =====
# アクセスが来たとき、今日の分がまだ無ければDBをコピーする
# 古いバックアップは14日分だけ残して削除する
import glob as _glob

@app.before_request
def daily_db_backup():
    try:
        _bk = '/home/yuto113/backups/db'
        import pytz as _p_bk
        from datetime import datetime as _d_bk
        _today = _d_bk.now(_p_bk.timezone('Asia/Tokyo')).strftime('%Y%m%d')
        _dst = _bk + '/quizshare_' + _today + '.db'
        if not os.path.exists(_dst):  # 今日の分がまだ無いときだけ動く
            os.makedirs(_bk, exist_ok=True)
            shutil.copy('/home/yuto113/quizshare.db', _dst)
            for _old in sorted(_glob.glob(_bk + '/quizshare_*.db'))[:-3]:
                os.remove(_old)  # 14日より古いのは消す
    except Exception:
        pass  # バックアップ失敗でもサイトは止めない
