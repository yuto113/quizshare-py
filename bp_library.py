# ====================================================================
# bp_library.py: ライブラリ・テーマ・AI採点
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

bp = Blueprint('library', __name__)

# ===== 引用ライブラリ =====

@bp.route('/setting_schedule/')
def page_setting_schedule():
    return render_template('setting_schedule.html')

@bp.route('/quizclub-2026')
def page_quizclub():
    return render_template('quizclub.html')

@bp.route('/quizclub-login-guide')
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

@bp.route('/quizclub-login')
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

@bp.route('/theme')
def page_theme():
    return render_template('theme.html')

def _admin_ok(pw=None):
    """管理者かどうか。管理センターでログインしている場合のみ許可する"""
    try:
        from flask import session as _ss
        sid = _ss.get('staff_id')
        if not sid:
            return False
        import sqlite3 as _sq
        _c = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
        r = _c.execute('SELECT role, status FROM qz_staff WHERE staff_id=?', (sid,)).fetchone()
        _c.close()
        return bool(r and r[0] == 'admin' and (r[1] or 'active') == 'active')
    except Exception:
        return False


@bp.route('/api/ai/score', methods=['POST'])
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
    # ===== AI採点: Voto 1(自作モデル) =====
    # 以前は外部API(Gemini / OpenAI / Claude / Cloudflare)を使っていたが、
    # 自作の Voto 1 に置き換えた。外部キー不要・無料・すべて自前で動く。
    try:
        import qstart_core as _qc_ai
        _v, _r = _qc_ai.voto_judge(question, correct, user_ans)
    except Exception as _e:
        print(f'Voto採点エラー: {_e}')
        _v, _r = None, None

    if not _v:
        return err('AI採点が一時的に利用できないよ。もう一度試してね', 503)

    _label = {'正解': 'correct', '部分正解': 'partial', '不正解': 'wrong'}.get(_v, 'unknown')
    _icon  = {'正解': '⭕', '部分正解': '🔺', '不正解': '❌'}.get(_v, '')
    _reason = _r or {'正解': '正解です。',
                     '部分正解': 'おしい！もう少しくわしく書けると良いです。',
                     '不正解': f'正しい答えは「{correct}」です。'}.get(_v, '')

    # 使用量の記録(トークン相当。Votoは自前なので概算)
    _tok = len(question) + len(correct) + len(user_ans) + len(_reason)
    try:
        import sqlite3 as _sqv
        from flask import session as _ss
        _cv = _sqv.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
        # グループ単位の記録(従来どおり)
        _cv.execute('INSERT INTO ai_usage (group_id, tokens_used) VALUES (?, ?)',
                    (grp['id'], _tok))
        # ★Qstartの使用量とも共有する(ログイン中のみ)
        _qu = _ss.get('qstart_user')
        if _qu:
            _cv.execute('''INSERT INTO qstart_api_usage(user_id, source, model, tokens_used)
                           VALUES(?, ?, ?, ?)''', (_qu, 'grade', 'voto', _tok))
        _cv.commit(); _cv.close()
    except Exception as rec_err:
        print(f'ai_usage記録エラー: {rec_err}')

    return ok(ai_result=_label, ai_reason=f'{_icon} {_reason}',
              verdict=_v, tokens_used=_tok, model='voto-1')

@bp.route('/api/admin/ai-scoring/<group_id>', methods=['POST'])
def api_admin_ai_scoring(group_id):
    data = request.get_json(silent=True) or {}
    # 管理者パスワードで認証（setting_ai/からの呼び出し）
    pw = data.get('password', '')
    if _admin_ok(pw):
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

@bp.route('/api/admin/check_password', methods=['POST'])
def api_check_admin_password():
    data = request.get_json(silent=True) or {}
    if _admin_ok(data.get('password')):
        return ok(valid=True)
    return err('パスワードが違うよ', 403)

@bp.route('/api/custom_themes', methods=['GET'])
def api_get_custom_themes():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id,key,name,bg,style,emoji FROM custom_themes ORDER BY id').fetchall()
    conn.close()
    return ok(themes=[{'id':r[0],'key':r[1],'name':r[2],'bg':r[3],'style':r[4],'emoji':r[5]} for r in rows])

@bp.route('/api/custom_themes', methods=['POST'])
def api_add_custom_theme():
    data = request.get_json(silent=True) or {}
    if not _admin_ok(data.get('password')):
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

@bp.route('/api/custom_themes/<int:theme_id>', methods=['DELETE','POST'])
def api_delete_custom_theme(theme_id):
    data = request.get_json(silent=True) or {}
    if not _admin_ok(data.get('password')):
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM custom_themes WHERE id=?', (theme_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/api/special_days', methods=['GET'])
def api_get_special_days():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    rows = conn.execute('SELECT id,month,day,name,theme,emoji FROM special_days ORDER BY month,day').fetchall()
    conn.close()
    return ok(days=[{'id':r[0],'month':r[1],'day':r[2],'name':r[3],'theme':r[4],'emoji':r[5]} for r in rows])

@bp.route('/api/special_days', methods=['POST'])
def api_add_special_day():
    pw = request.get_json(silent=True) or {}
    if not _admin_ok(pw.get('password')):
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

@bp.route('/api/special_days/<int:day_id>', methods=['DELETE','POST'])
def api_delete_special_day(day_id):
    data = request.get_json(silent=True) or {}
    if not _admin_ok(data.get('password')):
        return err('管理者パスワードが違うよ', 403)
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute('DELETE FROM special_days WHERE id=?', (day_id,))
    conn.commit()
    conn.close()
    return ok(message='削除しました')

@bp.route('/library')
def page_library():
    # 引用ライブラリ トップページ
    grp = current_group()
    return render_template('library_top.html', logged_in=(grp is not None))

@bp.route('/library/study')
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

@bp.route('/library/<lib_key>')
def page_library_generic(lib_key):
    if lib_key not in LIB_CONFIG:
        return redirect('/')
    cfg = LIB_CONFIG[lib_key]
    grp = current_group()
    return render_template('library_generic.html',
        lib_key=lib_key,
        lib_label=cfg['label'],
        logged_in=(grp is not None))

@bp.route('/api/library/<lib_key>/data')
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

@bp.route('/api/library/<lib_key>/import', methods=['POST'])
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

@bp.route('/library/all')
def page_library_all():
    grp = current_group()
    return render_template('library_all.html', logged_in=(grp is not None))

@bp.route('/api/library/all/data')
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

@bp.route('/api/library/all/import', methods=['POST'])
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

@bp.route('/library/university')
def page_library_university():
    grp = current_group()
    return render_template('library_university.html', logged_in=(grp is not None))

@bp.route('/api/library/university/data')
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

@bp.route('/api/library/university/import', methods=['POST'])
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

@bp.route('/library/game')
def page_library_game():
    # ゲームクイズライブラリ
    grp = current_group()
    return render_template('library_game.html', logged_in=(grp is not None))

@bp.route('/api/library/game/data')
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

@bp.route('/api/library/game/import', methods=['POST'])
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

@bp.route('/api/library/data')
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

@bp.route('/api/library/import', methods=['POST'])
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


