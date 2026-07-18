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


# ===== 引用ライブラリ =====

@app.route('/setting_schedule/')
def page_setting_schedule():
    return render_template('setting_schedule.html')

@app.route('/quizclub-2026')
def page_quizclub():
    return render_template('quizclub.html')

@app.route('/quizclub-login-guide')
def page_quizclub_login_guide():
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, group_password_hash FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id('sawa-quiz2026'),))
        row = cur.fetchone()
    if not row:
        return redirect('/')
    row = dict(row)
    stored_gp = row.get('group_password_hash')
    pw = 'asao-14'
    if stored_gp and not verify_password(pw, stored_gp):
        return redirect('/')
    session['group_id'] = 'sawa-quiz2026'
    return redirect('/group/guide')

@app.route('/quizclub-login')
def page_quizclub_login():
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id, group_password_hash FROM groups WHERE group_id_hash = %s'),
                    (hash_group_id('sawa-quiz2026'),))
        row = cur.fetchone()
    if not row:
        return redirect('/')
    row = dict(row)
    stored_gp = row.get('group_password_hash')
    pw = 'asao-14'
    if stored_gp and not verify_password(pw, stored_gp):
        return redirect('/')
    session['group_id'] = 'sawa-quiz2026'
    return redirect('/group')

@app.route('/theme')
def page_theme():
    return render_template('theme.html')

@app.route('/api/ai/score', methods=['POST'])
def api_ai_score():
    import urllib.request as _req, json as _json, re as _re
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    # ベータ期間チェック
    if not check_beta_active('ai_scoring'):
        return err('AI採点はベータ期間外です', 403)
    # APIキーが設定されていればai_scoringがOFFでも使える
    import sqlite3 as _sq_chk
    _conn_chk = _sq_chk.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    _row_chk = _conn_chk.execute('SELECT ai_api_key, cf_api_token FROM groups WHERE id=?', (grp['id'],)).fetchone()
    _conn_chk.close()
    _has_api_key = bool(_row_chk and (_row_chk[0] or _row_chk[1]))
    if not grp.get('ai_scoring') and not _has_api_key:
        return err('このグループはAI採点が有効ではないよ', 403)
    # 1日の上限チェック
    import sqlite3 as _sq0
    conn0 = _sq0.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    today_used = conn0.execute(
        "SELECT COALESCE(SUM(tokens_used),0) FROM ai_usage WHERE group_id=? AND date(created_at)=date('now','localtime')",
        (grp['id'],)
    ).fetchone()[0]
    daily_limit = conn0.execute(
        "SELECT daily_limit FROM ai_limits WHERE group_id=?", (grp['id'],)
    ).fetchone()
    conn0.close()
    daily_limit = daily_limit[0] if daily_limit else -1
    # -1=未設定(デフォルト1000), 0=管理者が無効化
    if daily_limit == 0:
        return err('このグループはAI採点が無効化されています', 403)
    if daily_limit == -1:
        daily_limit = 1000  # デフォルト上限
    if today_used >= daily_limit:
        return err(f'今日の上限（{daily_limit}トークン）に達したよ。明日また使ってね', 429)
    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    correct = (data.get('correct_answer') or '').strip()
    user_ans = (data.get('user_answer') or '').strip()
    if not question or not correct or not user_ans:
        return err('必要なパラメータが不足しているよ')
    # グループのAI設定を取得（管理者設定を優先）
    import sqlite3 as _sq3
    _conn3 = _sq3.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    _row3 = _conn3.execute('SELECT ai_provider, ai_api_key, cf_account_id, cf_api_token, is_official FROM groups WHERE id=?', (grp['id'],)).fetchone()
    _conn3.close()
    _has_admin_config = bool(_row3[2] and _row3[3]) if _row3 else False
    if _has_admin_config:
        ai_provider = 'cloudflare'
        ai_api_key = (_row3[3] if _row3 else '') or ''
        cf_account = (_row3[2] if _row3 else '') or os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
        cf_token = (_row3[3] if _row3 else '') or os.environ.get('CLOUDFLARE_AI_TOKEN', '')
    else:
        ai_provider = (_row3[0] if _row3 and _row3[0] else 'cloudflare')
        ai_api_key = (_row3[1] if _row3 and _row3[1] else '') or ''
        cf_account = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
        cf_token = os.environ.get('CLOUDFLARE_AI_TOKEN', '')
    prompt = (
        'あなたはクイズ採点者です\n'
        f'問題: {question}\n'
        f'正解: {correct}\n'
        f'生徒の答え: {user_ans}\n'
        '採点してください\n'
        'correct=完全正解 partial=惜しい wrong=不正解\n'
        '{"result":"correct/partial/wrong","reason":"理由"} の形式のみで返してください'
    )
    try:
        if ai_provider == 'openai':
            if not ai_api_key:
                return err('OpenAI APIキーが設定されていないよ', 500)
            payload = _json.dumps({'model':'gpt-4o-mini','messages':[{'role':'system','content':'You are a quiz grader. Reply only in JSON format.'},{'role':'user','content':prompt}],'max_tokens':200}).encode('utf-8')
            req = _req.Request('https://api.openai.com/v1/chat/completions', data=payload, headers={'Authorization':f'Bearer {ai_api_key}','Content-Type':'application/json'})
            with _req.urlopen(req, timeout=15) as res:
                raw = _json.loads(res.read().decode('utf-8'))
            text = raw.get('choices',[{}])[0].get('message',{}).get('content','{}')
        elif ai_provider == 'gemini':
            if not ai_api_key:
                return err('Gemini APIキーが設定されていないよ', 500)
            payload = _json.dumps({'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'maxOutputTokens':200}}).encode('utf-8')
            req = _req.Request(f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={ai_api_key}',data=payload,headers={'Content-Type':'application/json'})
            with _req.urlopen(req, timeout=15) as res:
                raw = _json.loads(res.read().decode('utf-8'))
            text = raw.get('candidates',[{}])[0].get('content',{}).get('parts',[{}])[0].get('text','{}')
        elif ai_provider == 'anthropic':
            if not ai_api_key:
                return err('Anthropic APIキーが設定されていないよ', 500)
            payload = _json.dumps({'model':'claude-haiku-4-5-20251001','max_tokens':200,'messages':[{'role':'user','content':prompt}]}).encode('utf-8')
            req = _req.Request('https://api.anthropic.com/v1/messages',data=payload,headers={'x-api-key':ai_api_key,'anthropic-version':'2023-06-01','Content-Type':'application/json'})
            with _req.urlopen(req, timeout=15) as res:
                raw = _json.loads(res.read().decode('utf-8'))
            text = raw.get('content',[{}])[0].get('text','{}')
        else:
            if not cf_token or not cf_account:
                return err('Cloudflare AIが設定されていないよ', 500)
            payload = _json.dumps({'messages':[{'role':'system','content':'You are a quiz grader. Reply only in JSON format.'},{'role':'user','content':prompt}]}).encode('utf-8')
            url = f'https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/@cf/meta/llama-3-8b-instruct'
            req = _req.Request(url,data=payload,headers={'Authorization':f'Bearer {cf_token}','Content-Type':'application/json'})
            with _req.urlopen(req, timeout=15) as res:
                cf_data = _json.loads(res.read().decode('utf-8'))
            text = cf_data.get('result', {}).get('response', '{}')
        match = _re.search(r'\{[^}]+\}', text)
        if match:
            result = _json.loads(match.group())
        else:
            result = {'result': 'unknown', 'reason': text[:100]}
        # トークン使用量を記録
        tokens = len(prompt) // 4 + 50
        try:
            import sqlite3 as _sq2
            conn2 = _sq2.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
            conn2.execute(
                'INSERT INTO ai_usage (group_id, tokens_used) VALUES (?, ?)',
                (grp['id'], tokens)
            )
            conn2.commit()
            conn2.close()
        except Exception as rec_err:
            print(f'ai_usage記録エラー: {rec_err}')
        return ok(ai_result=result.get('result', 'unknown'), ai_reason=result.get('reason', ''), tokens_used=tokens)
    except Exception as e:
        return err(f'AI採点エラー: {str(e)}', 500)

@app.route('/api/admin/ai-scoring/<group_id>', methods=['POST'])
def api_admin_ai_scoring(group_id):
    data = request.get_json(silent=True) or {}
    # 管理者パスワードで認証（setting_ai/からの呼び出し）
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    pw = data.get('password', '')
    if pw and pw == admin_pw:
        # パスワード認証でUUID直接指定
        with get_db() as conn:
            cur = make_cursor(conn)
            cur.execute(q('SELECT id FROM groups WHERE id = %s'), (group_id,))
            row = cur.fetchone()
        if not row:
            return err('グループが見つからないよ', 404)
        ai_scoring = 1 if data.get('ai_scoring') else 0
        with get_db() as conn:
            cur = make_cursor(conn)
            cur.execute(q('UPDATE groups SET ai_scoring = %s WHERE id = %s'), (ai_scoring, group_id))
        return ok(ai_scoring=bool(ai_scoring))
    # 通常の管理者セッション認証
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('SELECT id FROM groups WHERE group_id_hash = %s'), (hash_group_id(group_id),))
        row = cur.fetchone()
    if not row:
        return err('グループが見つからないよ', 404)
    if not admin_logged_in_for(row['id']):
        return err('管理者としてログインしてね', 401)
    ai_scoring = 1 if data.get('ai_scoring') else 0
    with get_db() as conn:
        cur = make_cursor(conn)
        cur.execute(q('UPDATE groups SET ai_scoring = %s WHERE id = %s'), (ai_scoring, row['id']))
    return ok(ai_scoring=bool(ai_scoring))

@app.route('/api/admin/check_password', methods=['POST'])
def api_check_admin_password():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD','')
    if data.get('password') == admin_pw:
        return ok(valid=True)
    return err('パスワードが違うよ', 403)

@app.route('/api/custom_themes', methods=['GET'])
def api_get_custom_themes():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id,key,name,bg,style,emoji FROM custom_themes ORDER BY id').fetchall()
    conn.close()
    return ok(themes=[{'id':r[0],'key':r[1],'name':r[2],'bg':r[3],'style':r[4],'emoji':r[5]} for r in rows])

@app.route('/api/custom_themes', methods=['POST'])
def api_add_custom_theme():
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD','')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    key = (data.get('key') or '').strip().replace(' ','_')
    name = (data.get('name') or '').strip()
    bg = (data.get('bg') or '').strip()
    style = (data.get('style') or '').strip()
    emoji = (data.get('emoji') or '🎨').strip()
    if not key or not name or not bg:
        return err('key・name・bgは必須だよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    try:
        conn.execute('INSERT OR REPLACE INTO custom_themes (key,name,bg,style,emoji) VALUES (?,?,?,?,?)',
                     (key,name,bg,style,emoji))
        conn.commit()
    except Exception as e:
        conn.close()
        return err(str(e))
    conn.close()
    return ok(message='追加しました', key=key)

@app.route('/api/custom_themes/<int:theme_id>', methods=['DELETE','POST'])
def api_delete_custom_theme(theme_id):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD','')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM custom_themes WHERE id=?', (theme_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@app.route('/api/special_days', methods=['GET'])
def api_get_special_days():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id,month,day,name,theme,emoji FROM special_days ORDER BY month,day').fetchall()
    conn.close()
    return ok(days=[{'id':r[0],'month':r[1],'day':r[2],'name':r[3],'theme':r[4],'emoji':r[5]} for r in rows])

@app.route('/api/special_days', methods=['POST'])
def api_add_special_day():
    pw = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD','')
    if pw.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    data = pw
    month = int(data.get('month',0))
    day = int(data.get('day',0))
    name = (data.get('name') or '').strip()
    theme = (data.get('theme') or 'spring').strip()
    emoji = (data.get('emoji') or '🎉').strip()
    if not (1 <= month <= 12) or not (1 <= day <= 31) or not name:
        return err('入力が正しくないよ')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    try:
        conn.execute('INSERT OR REPLACE INTO special_days (month,day,name,theme,emoji) VALUES (?,?,?,?,?)', (month,day,name,theme,emoji))
        conn.commit()
        print('追加OK')
    except Exception as e:
        conn.close()
        return err(str(e))
    conn.close()
    return ok(message='追加しました')

@app.route('/api/special_days/<int:day_id>', methods=['DELETE','POST'])
def api_delete_special_day(day_id):
    data = request.get_json(silent=True) or {}
    admin_pw = os.environ.get('ADMIN_PASSWORD','')
    if data.get('password') != admin_pw:
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM special_days WHERE id=?', (day_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@app.route('/library')
def page_library():
    # 引用ライブラリ トップページ
    grp = current_group()
    return render_template('library_top.html', logged_in=(grp is not None))

@app.route('/library/study')
def page_library_study():
    # 学習用ライブラリ
    grp = current_group()
    return render_template('library.html', logged_in=(grp is not None))


# ========== 新ライブラリ共通ルート ==========
LIB_CONFIG = {
    'world':      {'table': 'world_quizzes',       'col': 'country',  'label': '世界の国・地理',   'tag': '世界'},
    'food':       {'table': 'food_quizzes',         'col': 'category', 'label': '料理・食べ物',     'tag': '料理'},
    'animal':     {'table': 'animal_quizzes',       'col': 'category', 'label': '動物・生き物',     'tag': '動物'},
    'sports':     {'table': 'sports_quizzes',       'col': 'category', 'label': 'スポーツ',         'tag': 'スポーツ'},
    'anime':      {'table': 'anime_quizzes',        'col': 'title',    'label': '映画・アニメ',     'tag': 'アニメ'},
    'science':    {'table': 'science_quizzes',      'col': 'category', 'label': '科学実験',         'tag': '科学'},
    'programming':{'table': 'programming_quizzes',  'col': 'language', 'label': 'プログラミング',   'tag': 'プログラミング'},
    'japan_pref': {'table': 'japan_pref_quizzes',   'col': 'region',   'label': '日本の都道府県',   'tag': '都道府県'},
    'japan_culture':{'table': 'japan_culture_quizzes','col':'category','label': '日本の文化・歴史', 'tag': '日本文化'},
    'riddle':     {'table': 'riddle_quizzes',       'col': 'category', 'label': 'なぞなぞ・クイズ', 'tag': 'なぞなぞ'},
    'person':     {'table': 'person_quizzes',       'col': 'category', 'label': '偉人・歴史人物',   'tag': '偉人'},
    'english':    {'table': 'english_quizzes',      'col': 'category', 'label': '英語・外国語',     'tag': '英語'},
}

@app.route('/library/<lib_key>')
def page_library_generic(lib_key):
    if lib_key not in LIB_CONFIG:
        return redirect('/')
    cfg = LIB_CONFIG[lib_key]
    grp = current_group()
    return render_template('library_generic.html',
        lib_key=lib_key,
        lib_label=cfg['label'],
        logged_in=(grp is not None))

@app.route('/api/library/<lib_key>/data')
def api_library_generic_data(lib_key):
    import sqlite3 as _sq
    if lib_key not in LIB_CONFIG:
        return err('不明なライブラリ')
    cfg = LIB_CONFIG[lib_key]
    table = cfg['table']
    col = cfg['col']
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    conn = _sq.connect(db_path)
    cat = request.args.get('category', '')
    grp = current_group()
    imported_questions = set()
    if grp:
        with get_db() as gconn:
            gcur = make_cursor(gconn)
            gcur.execute(q("SELECT question FROM quizzes WHERE group_id=%s AND author_name='引用ライブラリ'"), (grp['id'],))
            for row in gcur.fetchall():
                imported_questions.add(dict(row)['question'])
    if cat == '__all__':
        rows = conn.execute(
            f"SELECT id, question, answer, explanation, options FROM {table} ORDER BY id"
        ).fetchall()
        quizzes = [{'id': r[0], 'question': r[1], 'answer': r[2],
                    'explanation': r[3] or '', 'options': r[4],
                    'imported': r[1] in imported_questions} for r in rows]
        conn.close()
        return ok(quizzes=quizzes)
    elif cat:
        rows = conn.execute(
            f"SELECT id, question, answer, explanation, options FROM {table} WHERE {col}=? ORDER BY id",
            (cat,)
        ).fetchall()
        quizzes = [{'id': r[0], 'question': r[1], 'answer': r[2],
                    'explanation': r[3] or '', 'options': r[4],
                    'imported': r[1] in imported_questions} for r in rows]
        conn.close()
        return ok(quizzes=quizzes)
    cats = [r[0] for r in conn.execute(
        f"SELECT DISTINCT {col} FROM {table} ORDER BY {col}"
    ).fetchall()]
    conn.close()
    return ok(categories=cats)

@app.route('/api/library/<lib_key>/import', methods=['POST'])
def api_library_generic_import(lib_key):
    import sqlite3 as _sq, datetime, pytz
    if lib_key not in LIB_CONFIG:
        return err('不明なライブラリ')
    cfg = LIB_CONFIG[lib_key]
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    lib_id = data.get('quiz_id')
    if not lib_id:
        return err('quiz_idが必要です')
    table = cfg['table']
    col = cfg['col']
    tag_prefix = cfg['tag']
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    lib_conn = _sq.connect(db_path)
    row = lib_conn.execute(
        f"SELECT {col}, question, answer, explanation, options FROM {table} WHERE id=?",
        (lib_id,)
    ).fetchone()
    lib_conn.close()
    if not row:
        return err('問題が見つかりません')
    cat_val, question, answer, explanation, options_json = row
    has_opts = 1 if options_json else 0
    edit_password = data.get('edit_password', '')
    edit_pw_hash = hash_password(edit_password) if edit_password else None
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        cur = make_cursor(conn)
        quiz_id = new_id()
        tag_str = f'{tag_prefix} {cat_val}'
        try:
            cur.execute(
                q('INSERT INTO quizzes (id,group_id,author_name,class_name,question,answer,explanation,tags,has_options,answer_options,edit_password_hash,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
                (quiz_id, grp['id'], '引用ライブラリ', '', question, answer, explanation or '', tag_str, has_opts, options_json, edit_pw_hash, now)
            )
            conn.commit()
        except Exception as e:
            return err(f'追加失敗: {str(e)}')
    return ok(message='引用しました')

@app.route('/library/all')
def page_library_all():
    grp = current_group()
    return render_template('library_all.html', logged_in=(grp is not None))

@app.route('/api/library/all/data')
def api_library_all_data():
    import sqlite3 as _sq
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    conn = _sq.connect(db_path)
    tag_filter = request.args.get('tag', '')
    grp = current_group()
    imported_questions = set()
    if grp:
        with get_db() as gconn:
            gcur = make_cursor(gconn)
            gcur.execute(q("SELECT question FROM quizzes WHERE group_id=%s AND author_name='引用ライブラリ'"), (grp['id'],))
            for row in gcur.fetchall():
                imported_questions.add(dict(row)['question'])
    # 全テーブルから取得
    all_quizzes = []
    sources = [
        ('library_quizzes','subject','学習'),
        ('game_quizzes','game','ゲーム'),
        ('university_quizzes','field','大学'),
        ('world_quizzes','country','世界'),
        ('food_quizzes','category','料理'),
        ('animal_quizzes','category','動物'),
        ('sports_quizzes','category','スポーツ'),
        ('anime_quizzes','title','アニメ'),
        ('science_quizzes','category','科学'),
        ('programming_quizzes','language','プログラミング'),
        ('japan_pref_quizzes','region','都道府県'),
        ('japan_culture_quizzes','category','日本文化'),
        ('riddle_quizzes','category','なぞなぞ'),
        ('person_quizzes','category','偉人'),
        ('english_quizzes','category','英語'),
    ]
    # optionsカラムがないテーブル
    no_options_tables = {'library_quizzes', 'university_quizzes'}
    for table, col, tag in sources:
        if tag_filter and tag_filter != tag:
            continue
        try:
            if table in no_options_tables:
                if table == 'library_quizzes':
                    rows = conn.execute("SELECT id, grade, subject, question, answer, explanation FROM library_quizzes ORDER BY grade, subject, id").fetchall()
                    for r in rows:
                        all_quizzes.append({
                            'id': f'{table}:{r[0]}',
                            'table': table,
                            'db_id': r[0],
                            'tag': tag,
                            'grade': r[1],
                            'category': r[2],
                            'question': r[3],
                            'answer': r[4],
                            'explanation': r[5] or '',
                            'options': None,
                            'imported': r[3] in imported_questions,
                        })
                else:
                    rows = conn.execute(f"SELECT id, {col}, question, answer, explanation FROM {table} ORDER BY id").fetchall()
                    for r in rows:
                        all_quizzes.append({
                            'id': f'{table}:{r[0]}',
                            'table': table,
                            'db_id': r[0],
                            'tag': tag,
                            'grade': None,
                            'category': r[1],
                            'question': r[2],
                            'answer': r[3],
                            'explanation': r[4] or '',
                            'options': None,
                            'imported': r[2] in imported_questions,
                        })
            else:
                rows = conn.execute(f"SELECT id, {col}, question, answer, explanation, options FROM {table} ORDER BY id").fetchall()
                for r in rows:
                    all_quizzes.append({
                        'id': f'{table}:{r[0]}',
                        'table': table,
                        'db_id': r[0],
                        'tag': tag,
                        'grade': None,
                        'category': r[1],
                        'question': r[2],
                        'answer': r[3],
                        'explanation': r[4] or '',
                        'options': r[5],
                        'imported': r[2] in imported_questions,
                    })
        except Exception as e:
            print(f'Error in {table}: {e}')
    conn.close()
    tags = list(dict.fromkeys([s[2] for s in sources]))
    return ok(quizzes=all_quizzes, tags=tags)

@app.route('/api/library/all/import', methods=['POST'])
def api_library_all_import():
    import sqlite3 as _sq, datetime, pytz
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    items = data.get('items', [])
    if not items:
        return err('itemsが必要です')
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    lib_conn = _sq.connect(db_path)
    jst = pytz.timezone('Asia/Tokyo')
    edit_password = data.get('edit_password', '')
    edit_pw_hash = hash_password(edit_password) if edit_password else None
    ok_count = 0
    with get_db() as gconn:
        gcur = make_cursor(gconn)
        for item in items:
            table = item.get('table')
            db_id = item.get('db_id')
            tag = item.get('tag', '')
            col_map = {
                'library_quizzes':'grade','game_quizzes':'game',
                'university_quizzes':'field',
                'world_quizzes':'country','food_quizzes':'category',
                'animal_quizzes':'category','sports_quizzes':'category',
                'anime_quizzes':'title','science_quizzes':'category',
                'programming_quizzes':'language','japan_pref_quizzes':'region',
                'japan_culture_quizzes':'category','riddle_quizzes':'category',
                'person_quizzes':'category','english_quizzes':'category',
            }
            col = col_map.get(table)
            if not col:
                continue
            row = lib_conn.execute(f"SELECT {col}, question, answer, explanation, options FROM {table} WHERE id=?", (db_id,)).fetchone()
            if not row:
                continue
            cat_val, question, answer, explanation, options_json = row
            has_opts = 1 if options_json else 0
            now = datetime.datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')
            quiz_id = new_id()
            tag_str = f'{tag} {cat_val}'
            try:
                gcur.execute(
                    q('INSERT INTO quizzes (id,group_id,author_name,class_name,question,answer,explanation,tags,has_options,answer_options,edit_password_hash,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
                    (quiz_id, grp['id'], '引用ライブラリ', '', question, answer, explanation or '', tag_str, has_opts, options_json, edit_pw_hash, now)
                )
                ok_count += 1
            except:
                pass
        gconn.commit()
    lib_conn.close()
    return ok(message=f'{ok_count}問引用しました', count=ok_count)

@app.route('/library/university')
def page_library_university():
    grp = current_group()
    return render_template('library_university.html', logged_in=(grp is not None))

@app.route('/api/library/university/data')
def api_library_university_data():
    import sqlite3 as _sq
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    conn = _sq.connect(db_path)
    field = request.args.get('field', '')
    grp = current_group()
    imported_questions = set()
    if grp:
        with get_db() as gconn:
            gcur = make_cursor(gconn)
            gcur.execute(q("SELECT question FROM quizzes WHERE group_id=%s AND author_name='引用ライブラリ'"), (grp['id'],))
            for row in gcur.fetchall():
                imported_questions.add(dict(row)['question'])
    if field == '__all__':
        _db_path2 = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
        lib_conn2 = _sq.connect(_db_path2)
        rows = lib_conn2.execute(
            "SELECT id, question, answer, explanation FROM university_quizzes ORDER BY field, id"
        ).fetchall()
        lib_conn2.close()
        quizzes = [{'id': r[0], 'question': r[1], 'answer': r[2],
                    'explanation': r[3] or '', 'imported': r[1] in imported_questions} for r in rows]
        return ok(quizzes=quizzes)
    elif field:
        rows = conn.execute(
            "SELECT id, question, answer, explanation FROM university_quizzes WHERE field=? ORDER BY id",
            (field,)
        ).fetchall()
        quizzes = [{'id': r[0], 'question': r[1], 'answer': r[2], 'explanation': r[3] or '',
                    'imported': r[1] in imported_questions} for r in rows]
        conn.close()
        return ok(quizzes=quizzes)
    fields = [r[0] for r in conn.execute(
        "SELECT DISTINCT field FROM university_quizzes ORDER BY field"
    ).fetchall()]
    conn.close()
    return ok(fields=fields)

@app.route('/api/library/university/import', methods=['POST'])
def api_library_university_import():
    import sqlite3 as _sq, datetime, pytz
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    lib_id = data.get('quiz_id')
    if not lib_id:
        return err('quiz_idが必要です')
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    lib_conn = _sq.connect(db_path)
    row = lib_conn.execute(
        "SELECT field, question, answer, explanation FROM university_quizzes WHERE id=?",
        (lib_id,)
    ).fetchone()
    lib_conn.close()
    if not row:
        return err('問題が見つかりません')
    field, question, answer, explanation = row
    edit_password = data.get('edit_password', '')
    edit_pw_hash = hash_password(edit_password) if edit_password else None
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        cur = make_cursor(conn)
        quiz_id = new_id()
        tag_str = f'大学 {field}'
        try:
            cur.execute(
                q('INSERT INTO quizzes (id,group_id,author_name,class_name,question,answer,explanation,tags,has_options,edit_password_hash,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
                (quiz_id, grp['id'], '引用ライブラリ', '', question, answer, explanation or '', tag_str, 0, edit_pw_hash, now)
            )
            conn.commit()
        except Exception as e:
            return err(f'追加失敗: {str(e)}')
    return ok(message='引用しました')

@app.route('/library/game')
def page_library_game():
    # ゲームクイズライブラリ
    grp = current_group()
    return render_template('library_game.html', logged_in=(grp is not None))

@app.route('/api/library/game/data')
def api_library_game_data():
    import sqlite3 as _sq
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    conn = _sq.connect(db_path)
    game = request.args.get('game', '')

    # ログイン中グループの引用済み問題文を取得
    grp = current_group()
    imported_questions = set()
    if grp:
        with get_db() as gconn:
            gcur = make_cursor(gconn)
            gcur.execute(q("SELECT question FROM quizzes WHERE group_id=%s AND author_name='引用ライブラリ'"), (grp['id'],))
            for row in gcur.fetchall():
                imported_questions.add(dict(row)['question'])

    if game == '__all__':
        rows = conn.execute(
            "SELECT id, question, answer, explanation, options FROM game_quizzes ORDER BY game, id"
        ).fetchall()
        import json as _json
        quizzes = []
        for r in rows:
            opts = None
            if r[4]:
                try: opts = _json.loads(r[4])
                except: opts = None
            quizzes.append({'id': r[0], 'question': r[1], 'answer': r[2],
                           'explanation': r[3] or '', 'options': opts,
                           'imported': r[1] in imported_questions})
        conn.close()
        return ok(quizzes=quizzes)
    elif game:
        rows = conn.execute(
            "SELECT id, question, answer, explanation, options FROM game_quizzes WHERE game=? ORDER BY id",
            (game,)
        ).fetchall()
        import json as _json
        quizzes = []
        for r in rows:
            opts = None
            if r[4]:
                try: opts = _json.loads(r[4])
                except: opts = None
            quizzes.append({'id': r[0], 'question': r[1], 'answer': r[2],
                           'explanation': r[3] or '', 'options': opts,
                           'imported': r[1] in imported_questions})
        conn.close()
        return ok(quizzes=quizzes)

    games = [r[0] for r in conn.execute(
        "SELECT DISTINCT game FROM game_quizzes ORDER BY game"
    ).fetchall()]
    conn.close()
    return ok(games=games)

@app.route('/api/library/game/import', methods=['POST'])
def api_library_game_import():
    import sqlite3 as _sq, datetime, pytz
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)
    data = request.get_json(silent=True) or {}
    lib_id = data.get('quiz_id')
    if not lib_id:
        return err('quiz_idが必要です')
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    lib_conn = _sq.connect(db_path)
    row = lib_conn.execute(
        "SELECT game, question, answer, explanation FROM game_quizzes WHERE id=?",
        (lib_id,)
    ).fetchone()
    lib_conn.close()
    if not row:
        return err('問題が見つかりません')
    game, question, answer, explanation = row
    # 選択肢を取得
    _db_path2 = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    lib_conn2 = _sq.connect(_db_path2)
    opt_row = lib_conn2.execute("SELECT options FROM game_quizzes WHERE id=?", (lib_id,)).fetchone()
    lib_conn2.close()
    options_json = opt_row[0] if opt_row and opt_row[0] else None
    has_opts = 1 if options_json else 0

    edit_password = data.get('edit_password', '')
    edit_pw_hash = hash_password(edit_password) if edit_password else None
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        cur = make_cursor(conn)
        quiz_id = new_id()
        tag_str = f'ゲーム {game}'
        try:
            cur.execute(
                q('INSERT INTO quizzes (id,group_id,author_name,class_name,question,answer,explanation,tags,has_options,answer_options,edit_password_hash,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
                (quiz_id, grp['id'], '引用ライブラリ', '', question, answer, explanation or '', tag_str, has_opts, options_json, edit_pw_hash, now)
            )
            conn.commit()
        except Exception as e:
            return err(f'追加失敗: {str(e)}')
    return ok(message='引用しました')

@app.route('/api/library/data')
def api_library_data():
    # 学年・教科・問題一覧を返すAPI
    import sqlite3 as _sq
    db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    conn = _sq.connect(db_path)
    grade   = request.args.get('grade', '')
    subject = request.args.get('subject', '')

    # ログイン中グループの引用済み問題文セットを取得
    grp = current_group()
    imported_questions = set()
    if grp:
        with get_db() as gconn:
            gcur = make_cursor(gconn)
            gcur.execute(q("SELECT question FROM quizzes WHERE group_id=%s AND author_name='引用ライブラリ'"), (grp['id'],))
            for row in gcur.fetchall():
                imported_questions.add(dict(row)['question'])

    # 全件取得モード
    if grade == '__all__':
        rows = conn.execute(
            "SELECT id, grade, subject, question, answer, explanation FROM library_quizzes ORDER BY grade, subject, id"
        ).fetchall()
        quizzes = [{'id': r[0], 'grade': r[1], 'subject': r[2],
                    'question': r[3], 'answer': r[4], 'explanation': r[5] or '',
                    'imported': r[3] in imported_questions} for r in rows]
        conn.close()
        return ok(quizzes=quizzes)

    if grade and subject:
        # 学年+教科で問題一覧を返す
        rows = conn.execute(
            "SELECT id, question, answer, explanation FROM library_quizzes WHERE grade=? AND subject=? ORDER BY id",
            (grade, subject)
        ).fetchall()
        quizzes = [{'id': r[0], 'question': r[1], 'answer': r[2], 'explanation': r[3] or '',
                    'imported': r[1] in imported_questions} for r in rows]
        conn.close()
        return ok(quizzes=quizzes)

    if subject and not grade:
        # 教科だけで問題一覧を返す（学年情報も含める）
        rows = conn.execute(
            "SELECT id, question, answer, explanation, grade FROM library_quizzes WHERE subject=? ORDER BY grade, id",
            (subject,)
        ).fetchall()
        quizzes = [{'id': r[0], 'question': r[1], 'answer': r[2], 'explanation': r[3] or '', 'grade': r[4],
                    'imported': r[1] in imported_questions} for r in rows]
        conn.close()
        return ok(quizzes=quizzes)

    # 学年一覧と全教科一覧を返す
    grades = [r[0] for r in conn.execute(
        "SELECT DISTINCT grade FROM library_quizzes ORDER BY grade"
    ).fetchall()]

    # 全教科一覧（常に返す）
    all_subjects = [r[0] for r in conn.execute(
        "SELECT DISTINCT subject FROM library_quizzes ORDER BY subject"
    ).fetchall()]

    # 教科一覧（学年指定時）
    subjects = []
    if grade:
        subjects = [r[0] for r in conn.execute(
            "SELECT DISTINCT subject FROM library_quizzes WHERE grade=? ORDER BY subject",
            (grade,)
        ).fetchall()]
    else:
        subjects = all_subjects

    conn.close()
    return ok(grades=grades, subjects=subjects, all_subjects=all_subjects)

@app.route('/api/library/import', methods=['POST'])
def api_library_import():
    # ライブラリの問題をグループに引用するAPI（平文で保存→decのフォールバックで表示OK）
    import sqlite3 as _sq, datetime, pytz
    grp = current_group()
    if not grp:
        return err('ログインしてね', 401)

    data   = request.get_json(silent=True) or {}
    lib_id = data.get('quiz_id')
    if not lib_id:
        return err('quiz_idが必要です')

    # ライブラリから問題を取得
    _db_path = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')
    lib_conn = _sq.connect(_db_path)
    row = lib_conn.execute(
        "SELECT grade, subject, question, answer, explanation FROM library_quizzes WHERE id=?",
        (lib_id,)
    ).fetchone()
    lib_conn.close()

    if not row:
        return err('問題が見つかりません')

    grade, subject, question, answer, explanation = row
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')

    # グループのクイズとして追加
    # # ライブラリの問題は教育公開データなので平文で保存する（decのフォールバックで正常表示される）
    edit_password = data.get('edit_password', '')
    edit_pw_hash = hash_password(edit_password) if edit_password else None
    with get_db() as conn:
        cur = make_cursor(conn)
        quiz_id     = new_id()
        group_db_id = grp.get('id', '')
        tag_str     = f'{grade} {subject}'
        try:
            cur.execute(
                q('INSERT INTO quizzes (id,group_id,author_name,class_name,question,answer,explanation,tags,has_options,edit_password_hash,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
                (quiz_id, group_db_id, '引用ライブラリ', '',
                 question, answer, explanation or '', tag_str, 0, edit_pw_hash, now)
            )
            conn.commit()
        except Exception as e:
            return err(f'追加失敗: {str(e)}')

    return ok(message='引用しました')


# ===== 学校モード =====

@app.route('/api/admin/school-mode', methods=['POST'])
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

@app.route('/api/group/access-logs')
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

@app.route('/api/custom_pages', methods=['GET'])
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

@app.route('/api/custom_pages', methods=['POST'])
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

@app.route('/api/custom_pages/<page_key>', methods=['POST'])
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

@app.route('/api/custom_pages/<page_key>/delete', methods=['POST'])
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

@app.route('/api/custom_pages/home', methods=['GET'])
def api_home_custom_pages():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT page_key,page_name,url,template_type FROM custom_pages WHERE show_on_home=1 AND is_published=1 ORDER BY id').fetchall()
    conn.close()
    return ok(pages=[{'key':r[0],'name':r[1],'url':r[2],'type':r[3]} for r in rows])

@app.route('/p/<page_key>')
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

@app.route('/api/teacher/login', methods=['POST'])
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

@app.route('/api/teacher/register_first', methods=['POST'])
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

@app.route('/api/teacher/add', methods=['POST'])
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

@app.route('/api/teacher/goal', methods=['POST'])
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

@app.route('/api/teacher/notice', methods=['POST'])
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

@app.route('/api/group/goals', methods=['GET'])
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

@app.route('/api/group/notices', methods=['GET'])
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

@app.route('/api/teacher/status', methods=['GET'])
def api_teacher_status():
    teacher = session.get('teacher')
    if not teacher:
        return ok(is_teacher=False)
    return ok(is_teacher=True, teacher_name=teacher.get('teacher_name'), teacher_num=teacher.get('teacher_num'))


@app.route('/api/teacher/change_password', methods=['POST'])
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

@app.route('/api/teacher/check_initial', methods=['GET'])
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

@app.route('/api/teacher/count', methods=['GET'])
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


@app.route('/api/admin/teachers/<group_id>', methods=['GET'])
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

@app.route('/api/admin/teacher/add', methods=['POST'])
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

@app.route('/api/admin/teacher/delete', methods=['POST'])
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

@app.route('/notices')
def page_notices():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    if not grp.get('school_mode'):
        return redirect(url_for('page_group'))
    return render_template('notices.html', group=grp, group_id=session.get('group_id'))

@app.route('/api/teacher/notice/<int:notice_id>/delete', methods=['POST'])
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

@app.route('/api/teacher/notice/<int:notice_id>/edit', methods=['POST'])
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

@app.route('/api/tasks', methods=['GET'])
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

@app.route('/api/tasks', methods=['POST'])
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

@app.route('/api/tasks/<int:task_id>', methods=['GET'])
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

@app.route('/api/tasks/<int:task_id>/submit', methods=['POST'])
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

@app.route('/api/tasks/<int:task_id>/delete', methods=['POST'])
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

@app.route('/tasks')
def page_tasks():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    if not grp.get('school_mode'):
        return redirect(url_for('page_group'))
    return render_template('tasks.html', group=grp, group_id=session.get('group_id'))

@app.route('/tasks/<int:task_id>')
def page_task_detail(task_id):
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    if not grp.get('school_mode'):
        return redirect(url_for('page_group'))
    return render_template('task_detail.html', group=grp, group_id=session.get('group_id'), task_id=task_id)

@app.route('/api/tasks/<int:task_id>/edit', methods=['POST'])
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

@app.route('/event/<event_key>')
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

@app.route('/api/events', methods=['GET'])
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

@app.route('/api/events', methods=['POST'])
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

@app.route('/api/events/<int:event_id>/publish', methods=['POST'])
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

@app.route('/api/server_time', methods=['GET'])
def api_server_time():
    # サーバーの現在時刻を返す(PCの時計に頼らないため)
    import time as _time
    return ok(now_ms=int(_time.time() * 1000))

@app.route('/api/events/<event_key>/quizzes', methods=['GET'])
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

@app.route('/api/events/<event_key>/submit', methods=['POST'])
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

@app.route('/api/events/<event_key>/ranking', methods=['GET'])
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

@app.route('/api/events/<event_key>/check_ip', methods=['GET'])
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


@app.route('/api/event/ip_restrict', methods=['POST'])
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

@app.route('/api/events/<int:event_id>/schedule', methods=['POST'])
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

@app.route('/api/events/<event_key>/add_quiz', methods=['POST'])
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

@app.route('/api/events/<event_key>/answer', methods=['POST'])
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

@app.route('/api/events/<event_key>/quizzes_detail', methods=['GET'])
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

@app.route('/api/banner', methods=['GET'])
def api_get_banner():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT message, color, is_active, link_url FROM site_banner WHERE id=1').fetchone()
    conn.close()
    if not row or not row[2]:
        return ok(active=False)
    return ok(active=True, message=row[0], color=row[1], link_url=row[3] or '')

@app.route('/api/banner', methods=['POST'])
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

@app.route('/api/tags/suggest', methods=['GET'])
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

@app.route('/api/beta/status', methods=['GET'])
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

@app.route('/api/beta/update', methods=['POST'])
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

@app.route('/api/battle/create', methods=['POST'])
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

@app.route('/api/battle/join', methods=['POST'])
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

@app.route('/api/battle/status', methods=['GET'])
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

@app.route('/api/battle/start', methods=['POST'])
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

@app.route('/api/battle/answer', methods=['POST'])
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

@app.route('/api/battle/result', methods=['GET'])
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

@app.route('/battle')
def page_battle():
    grp = current_group()
    if not grp:
        return redirect(url_for('page_home'))
    return render_template('battle.html', group=grp)

@app.route('/api/quizzes/<quiz_id>/info', methods=['GET'])
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

@app.route('/typing')
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

@app.route('/api/typing/start', methods=['POST'])
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

@app.route('/api/typing/submit', methods=['POST'])
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

@app.route('/api/typing/ranking', methods=['GET'])
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

# ===== QZERO 社員システム =====

@app.route('/staff/login')
def page_staff_login():
    return render_template('staff_login.html')

@app.route('/api/staff/login', methods=['POST'])
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

@app.route('/api/staff/hr/list', methods=['GET'])
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

@app.route('/api/staff/hr/add', methods=['POST'])
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

@app.route('/api/staff/hr/decide', methods=['POST'])
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

@app.route('/api/staff/hr/remove', methods=['POST'])
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

@app.route('/api/staff/register', methods=['POST'])
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

@app.route('/staff/hr')
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
got_request_exception.connect(_on_request_exception, app)

@app.route('/api/error_report', methods=['POST'])
def api_error_report():
    # ブラウザ側のJavaScriptエラーを受け取って記録する
    # (いたずら防止で1分に5回まで)
    if not rate_limit(f'errrep:{client_ip()}', 5):
        return ok()
    data = request.get_json(silent=True) or {}
    _record_error('browser', data.get('page', ''), data.get('message', ''),
                  data.get('detail', ''), request.headers.get('User-Agent', ''))
    return ok()

@app.route('/api/staff/errors/list', methods=['GET'])
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

@app.route('/api/staff/errors/clear', methods=['POST'])
def api_staff_errors_clear():
    if not staff_is_admin():
        return err('管理者だけができるよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM error_logs')
    conn.commit()
    conn.close()
    return ok(message='全部消したよ')

@app.route('/staff/errors')
def page_staff_errors():
    # エラー一覧ページ(管理者だけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if not staff_is_admin():
        return redirect('/staff/board')
    return render_template('staff_errors.html')

@app.context_processor
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

@app.route('/api/staff/moderation/list', methods=['GET'])
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

@app.route('/api/staff/moderation/update', methods=['POST'])
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

@app.route('/staff/moderation')
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

@app.route('/api/staff/kouan/orders', methods=['GET'])
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

@app.route('/api/staff/kouan/orders', methods=['POST'])
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

@app.route('/api/staff/kouan/reply', methods=['POST'])
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

@app.route('/api/staff/kouan/tasks', methods=['GET'])
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

@app.route('/api/staff/kouan/tasks', methods=['POST'])
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

@app.route('/api/staff/kouan/tasks/done', methods=['POST'])
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

@app.route('/api/staff/hr/security', methods=['POST'])
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

@app.route('/api/line/webhook', methods=['POST'])
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

@app.route('/api/staff/kouan/members', methods=['GET'])
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

@app.route('/api/staff/kouan/grant', methods=['POST'])
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

@app.route('/api/staff/kouan/transfer', methods=['POST'])
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

@app.route('/api/staff/kouan/points', methods=['GET'])
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

@app.route('/staff/kp')
def page_staff_kp():
    # KP残高ページ(公安メンバーだけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if staff_kouan_role() == '':
        return redirect('/staff/board')
    return render_template('staff_kp.html')

@app.route('/staff/kouan')
def page_staff_kouan():
    # 公安ページ(任命された人だけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    role = staff_kouan_role()
    if role == '':
        return redirect('/staff/board')
    return render_template('staff_kouan.html', kouan_role=role)

@app.route('/staff/handbook')
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

@app.route('/api/staff/cipher/keys', methods=['GET'])
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

@app.route('/api/staff/cipher/keys/new', methods=['POST'])
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

@app.route('/api/staff/cipher/keys/member', methods=['POST'])
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

@app.route('/api/staff/cipher/mykeys', methods=['GET'])
def api_cipher_mykeys():
    # 自分が使えるキー一覧(掲示板の🔐ボタン用)
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    return ok(keys=_my_cipher_keys())

@app.route('/staff/cipher')
def page_staff_cipher():
    # 暗号ツールページ(スタッフなら誰でも開ける)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_cipher.html')

@app.route('/api/staff/cipher/encode', methods=['POST'])
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

@app.route('/api/staff/cipher/decode', methods=['POST'])
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

@app.route('/api/staff/list-simple', methods=['GET'])
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

@app.route('/api/staff/cipher/mail/send', methods=['POST'])
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

@app.route('/api/staff/cipher/mail/inbox', methods=['GET'])
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

@app.route('/api/staff/cipher/mail/delete', methods=['POST'])
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

@app.route('/api/staff/list-for-mail', methods=['GET'])
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

@app.route('/api/staff/cipher/decrypt', methods=['POST'])
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

@app.route('/api/staff/messages/<int:message_id>/hide', methods=['POST'])
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

@app.route('/api/staff/messages/<int:message_id>/unsend', methods=['POST'])
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

@app.route('/staff/board')
def page_staff_board():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_board.html', staff_name=session.get('staff_name'), staff_id=session.get('staff_id'), is_admin=staff_is_admin())

@app.route('/api/staff/logout', methods=['POST'])
def api_staff_logout():
    session.pop('staff_id', None)
    session.pop('staff_name', None)
    return ok(message='ログアウトしました')

@app.route('/api/staff/messages', methods=['GET'])
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

@app.route('/api/staff/messages', methods=['POST'])
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

@app.route('/api/staff/files/<int:file_id>/blob', methods=['GET'])
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

@app.route('/staff/files/view/<int:file_id>')
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

@app.route('/staff/file/<int:message_id>')
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

@app.route('/api/staff/messages/<int:message_id>/blob', methods=['GET'])
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

@app.route('/api/staff/reactions', methods=['POST'])
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

@app.route('/api/staff/channels', methods=['GET'])
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

@app.route('/api/staff/channels/join', methods=['POST'])
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

@app.route('/api/staff/channels/leave', methods=['POST'])
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

@app.route('/api/staff/list', methods=['GET'])
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

@app.route('/api/staff/channels', methods=['POST'])
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

@app.route('/api/staff/channels/invite', methods=['POST'])
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

@app.route('/api/staff/channels/members', methods=['GET'])
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

@app.route('/staff/profile')
def page_staff_profile():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_profile.html', staff_id=session.get('staff_id'), staff_name=session.get('staff_name'))

@app.route('/api/staff/profile/update', methods=['POST'])
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

@app.route('/api/staff/channels/read', methods=['POST'])
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

@app.route('/staff/files')
def page_staff_files():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_files.html', staff_name=session.get('staff_name'))

@app.route('/api/staff/files', methods=['GET'])
def api_staff_files_get():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, staff_name, file_name, description, created_at FROM qz_files ORDER BY id DESC').fetchall()
    conn.close()
    return ok(files=[{'id':r[0],'staff_name':dec(r[1]),'file_name':dec(r[2]),'description':dec(r[3]),'created_at':r[4]} for r in rows])

@app.route('/api/staff/files', methods=['POST'])
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

@app.route('/api/staff/files/<int:file_id>', methods=['GET'])
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

@app.route('/api/staff/files/<int:file_id>/delete', methods=['POST'])
def api_staff_file_delete(file_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM qz_files WHERE id=?', (file_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@app.route('/staff/calendar')
def page_staff_calendar():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_calendar.html', staff_name=session.get('staff_name'))

@app.route('/api/staff/events', methods=['GET'])
def api_staff_events_get():
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id, staff_name, title, description, event_date, event_time, color FROM qz_events ORDER BY event_date').fetchall()
    conn.close()
    return ok(events=[{'id':r[0],'staff_name':dec(r[1]),'title':dec(r[2]),'description':dec(r[3]),'date':r[4],'time':r[5],'color':r[6]} for r in rows])

@app.route('/api/staff/events', methods=['POST'])
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

@app.route('/api/staff/events/<int:event_id>/delete', methods=['POST'])
def api_staff_events_delete(event_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM qz_events WHERE id=?', (event_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@app.route('/staff/tasks')
def page_staff_tasks():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_tasks.html', staff_name=session.get('staff_name'), my_id=session.get('staff_id'))

@app.route('/api/staff/tasks', methods=['GET'])
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

@app.route('/api/staff/tasks', methods=['POST'])
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

@app.route('/api/staff/tasks/<int:task_id>/status', methods=['POST'])
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

@app.route('/api/staff/tasks/<int:task_id>/delete', methods=['POST'])
def api_staff_tasks_delete(task_id):
    if not session.get('staff_id'):
        return err('ログインしてね', 401)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM qz_tasks WHERE id=?', (task_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@app.route('/staff/dashboard')
def page_staff_dashboard():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    return render_template('staff_dashboard.html', staff_name=session.get('staff_name'))

@app.route('/api/staff/dashboard', methods=['GET'])
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
