# ====================================================================
# bp_qzero.py: QZERO関連の全ルート(チャット・Mini・教室・パターン)
# app.pyから引っ越してきた。@app.route→@bp.routeに変えただけで中身は同じ。
# ====================================================================
import os
import json
from flask import Blueprint, render_template, request, session, redirect, jsonify

from qz_common import (
    enc, dec, ok, err, rate_limit, client_ip,
    get_db, make_cursor, q, hash_password, verify_password,
)

bp = Blueprint('qzero', __name__)

# staff_is_adminはまだapp.py側にあるので、実行時に借りる(第3段で整理予定)
def staff_is_admin():
    from bp_staff import staff_is_admin as _f
    return _f()

import re as _re_qzero
from qz_qzero import brain as qzero_brain

def _qzero_db():
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_unknown (
        id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, answer TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_users (
        user_id TEXT PRIMARY KEY, password_hash TEXT, nickname TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, role TEXT, text TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_threads (
        thread_id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, title TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT, reply TEXT, created_at TEXT)""")
    # 古いhistoryにthread_id列がなければ足す
    try:
        conn.execute('ALTER TABLE qzero_history ADD COLUMN thread_id INTEGER')
    except Exception:
        pass
    return conn



# ===== QZERO AI 使用量制限(トークン制) =====
# ログイン=1日10,000 / 未ログイン(IP)=1日3,000。日本時間0時リセット
# 管理者はqzero_bonusで個人に追加できる
QZERO_DAILY_TOKENS = 10000
QZERO_DAILY_TOKENS_GUEST = 3000

def _qzero_usage_db():
    conn = _qzero_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_usage (
        owner TEXT, date TEXT, used INTEGER DEFAULT 0, UNIQUE(owner, date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qzero_bonus (
        owner TEXT PRIMARY KEY, extra INTEGER DEFAULT 0)""")
    return conn

def _qzero_usage_owner():
    u = session.get('qzero_user')
    return (u, True) if u else ('ip:' + client_ip(), False)

def _qzero_usage_state():
    # (owner, 今日使った量, 上限) を返す
    import pytz as _p
    from datetime import datetime as _d
    owner, logged_in = _qzero_usage_owner()
    today = _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d')
    conn = _qzero_usage_db()
    row = conn.execute('SELECT used FROM qzero_usage WHERE owner=? AND date=?', (owner, today)).fetchone()
    bonus = conn.execute('SELECT extra FROM qzero_bonus WHERE owner=?', (owner,)).fetchone()
    conn.close()
    base = QZERO_DAILY_TOKENS if logged_in else QZERO_DAILY_TOKENS_GUEST
    return owner, (row[0] if row else 0), base + (bonus[0] if bonus else 0), today

def _qzero_tokens_of(text):
    # ざっくりトークン換算(日本語はほぼ1文字1トークン)
    return max(1, len(str(text or '')))

def _qzero_usage_spend(tokens):
    # 使えるならTrue+消費記録。上限ならFalse
    owner, used, limit, today = _qzero_usage_state()
    if used >= limit:
        return False
    conn = _qzero_usage_db()
    conn.execute('INSERT INTO qzero_usage (owner, date, used) VALUES (?,?,?) '
                 'ON CONFLICT(owner, date) DO UPDATE SET used = used + ?',
                 (owner, today, int(tokens), int(tokens)))
    conn.commit()
    conn.close()
    return True

def _qzero_usage_err():
    return jsonify({'ok': False, 'usage_full': True,
                    'error': '使用量がいっぱいになりました。0時0分にリセットします。'}), 429


@bp.route('/api/qzero/patterns/list', methods=['GET'])
def api_qzero_patterns_list():
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    conn = _qzero_db()
    rows = conn.execute('SELECT id, trigger, reply, created_at FROM qzero_patterns ORDER BY id DESC LIMIT 200').fetchall()
    conn.close()
    return ok(patterns=[{'id': r[0], 'trigger': r[1], 'reply': dec(r[2]), 'created_at': r[3]} for r in rows])

@bp.route('/api/qzero/patterns/add', methods=['POST'])
def api_qzero_patterns_add():
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    trigger = str(data.get('trigger') or '').strip()[:200]
    reply = str(data.get('reply') or '').strip()[:1000]
    if not trigger or not reply:
        return err('「こう来たら」と「こう返す」の両方を入れてね')
    import pytz as _p
    from datetime import datetime as _d
    conn = _qzero_db()
    conn.execute('INSERT INTO qzero_patterns (trigger, reply, created_at) VALUES (?,?,?)',
                 (trigger, enc(reply), _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')))
    conn.commit()
    conn.close()
    return ok(message='QZEROが新しい返し方を覚えたよ!')

@bp.route('/api/qzero/patterns/delete', methods=['POST'])
def api_qzero_patterns_delete():
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    conn = _qzero_db()
    conn.execute('DELETE FROM qzero_patterns WHERE id=?', (int(data.get('id') or 0),))
    conn.commit()
    conn.close()
    return ok(message='忘れさせたよ')

@bp.route('/staff/qzero-patterns')
def page_qzero_patterns():
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if not staff_is_admin():
        return redirect('/staff/board')
    return render_template('qzero_patterns.html')

@bp.route('/staff/qzero-school')
def page_qzero_school():
    # QZERO教室(管理者だけ)
    if not session.get('staff_id'):
        return redirect('/staff/login')
    if not staff_is_admin():
        return redirect('/staff/board')
    return render_template('qzero_school.html')

@bp.route('/api/qzero/school/list', methods=['GET'])
def api_qzero_school_list():
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    conn = _qzero_db()
    unknown = [{'id': r[0], 'question': r[1], 'created_at': r[2]} for r in
               conn.execute('SELECT id, question, created_at FROM qzero_unknown ORDER BY id DESC LIMIT 100').fetchall()]
    memory = [{'id': r[0], 'question': r[1], 'answer': dec(r[2]), 'created_at': r[3]} for r in
              conn.execute('SELECT id, question, answer, created_at FROM qzero_memory ORDER BY id DESC LIMIT 100').fetchall()]
    conn.close()
    return ok(unknown=unknown, memory=memory)

@bp.route('/api/qzero/school/teach', methods=['POST'])
def api_qzero_school_teach():
    # 「この質問にはこう答えて」を教え込む
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    question = str(data.get('question') or '').strip()[:300]
    answer = str(data.get('answer') or '').strip()[:1000]
    unknown_id = data.get('unknown_id')
    if not question or not answer:
        return err('質問と答えの両方を入れてね')
    import pytz as _p
    from datetime import datetime as _d
    conn = _qzero_db()
    conn.execute('INSERT INTO qzero_memory (question, answer, created_at) VALUES (?,?,?)',
                 (question, enc(answer), _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')))
    # 教え終わった「わからなかった質問」は一覧から消す
    if unknown_id:
        conn.execute('DELETE FROM qzero_unknown WHERE id=?', (unknown_id,))
    conn.commit()
    conn.close()
    return ok(message='QZEROが1つ賢くなったよ!')

@bp.route('/api/qzero/school/forget', methods=['POST'])
def api_qzero_school_forget():
    # 覚えた答えが間違ってたとき、忘れさせる
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    conn = _qzero_db()
    conn.execute('DELETE FROM qzero_memory WHERE id=?', (int(data.get('id') or 0),))
    conn.commit()
    conn.close()
    return ok(message='忘れさせたよ')

@bp.route('/api/qzero/school/dismiss', methods=['POST'])
def api_qzero_school_dismiss():
    # 教えずに、わからない質問リストから消すだけ
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    conn = _qzero_db()
    conn.execute('DELETE FROM qzero_unknown WHERE id=?', (int(data.get('id') or 0),))
    conn.commit()
    conn.close()
    return ok(message='消したよ')

def _qzero_current():
    # 今ログインしているQZEROユーザーを返す(なければNone)
    return session.get('qzero_user')

@bp.route('/api/qzero/register', methods=['POST'])
def api_qzero_register():
    # QZERO独自アカウントの新規登録
    if not rate_limit(f'qzreg:{client_ip()}', 5):
        return err('少し待ってね')
    import re as _re
    data = request.get_json(silent=True) or {}
    user_id = str(data.get('user_id') or '').strip()
    nickname = str(data.get('nickname') or '').strip()
    password = str(data.get('password') or '')
    if not _re.fullmatch(r'[A-Za-z0-9_]{3,20}', user_id):
        return err('IDは半角英数字と_で3〜20文字にしてね')
    if not nickname or len(nickname) > 20:
        return err('ニックネームは1〜20文字にしてね')
    if len(password) < 6:
        return err('パスワードは6文字以上にしてね')
    conn = _qzero_db()
    if conn.execute('SELECT user_id FROM qzero_users WHERE user_id=?', (user_id,)).fetchone():
        conn.close()
        return err('そのIDはもう使われているよ')
    import pytz as _p
    from datetime import datetime as _d
    # hash_passwordがscrypt+一人別saltでパスワードを守る(戻せない一方通行)
    conn.execute('INSERT INTO qzero_users (user_id, password_hash, nickname, created_at) VALUES (?,?,?,?)',
                 (user_id, hash_password(password), enc(nickname),
                  _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')))
    conn.commit()
    conn.close()
    session['qzero_user'] = 'u:' + user_id
    session['qzero_nick'] = nickname
    return ok(nickname=nickname)

@bp.route('/api/qzero/login', methods=['POST'])
def api_qzero_login():
    # QZERO独自アカウントでログイン
    if not rate_limit(f'qzlogin:{client_ip()}', 10):
        return err('少し待ってね')
    data = request.get_json(silent=True) or {}
    user_id = str(data.get('user_id') or '').strip()
    password = str(data.get('password') or '')
    conn = _qzero_db()
    row = conn.execute('SELECT password_hash, nickname FROM qzero_users WHERE user_id=?', (user_id,)).fetchone()
    conn.close()
    if not row or not verify_password(password, row[0]):
        return err('IDまたはパスワードが違うよ', 401)
    session['qzero_user'] = 'u:' + user_id
    session['qzero_nick'] = dec(row[1])
    return ok(nickname=dec(row[1]))

@bp.route('/api/qzero/staff-login', methods=['POST'])
def api_qzero_staff_login():
    # 社員ID/PWでもQZEROにログインできる(右上の社員ログイン用)
    if not rate_limit(f'qzstaff:{client_ip()}', 10):
        return err('少し待ってね')
    data = request.get_json(silent=True) or {}
    staff_id = str(data.get('staff_id') or '').strip()
    password = str(data.get('password') or '')
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT password_hash, name, status FROM qz_staff WHERE staff_id=?', (staff_id,)).fetchone()
    conn.close()
    if not row or not verify_password(password, row[0]):
        return err('IDまたはパスワードが違うよ', 401)
    if (row[2] or 'active') != 'active':
        return err('このアカウントは今使えないよ', 403)
    session['qzero_user'] = 's:' + staff_id
    session['qzero_nick'] = dec(row[1]) + '(社員)'
    return ok(nickname=session['qzero_nick'])

@bp.route('/api/qzero/logout', methods=['POST'])
def api_qzero_logout():
    session.pop('qzero_user', None)
    session.pop('qzero_nick', None)
    return ok()

@bp.route('/api/qzero/me', methods=['GET'])
def api_qzero_me():
    # 今ログインしてるか、ニックネームは何かを返す
    u = _qzero_current()
    if not u:
        return ok(logged_in=False)
    return ok(logged_in=True, nickname=session.get('qzero_nick', ''))

@bp.route('/api/qzero/threads', methods=['GET'])
def api_qzero_threads():
    # 自分のスレッド一覧(新しい順)
    u = _qzero_current()
    if not u:
        return ok(threads=[])
    conn = _qzero_db()
    rows = conn.execute('SELECT thread_id, title, updated_at, url_key, mode FROM qzero_threads WHERE owner=? ORDER BY updated_at DESC', (u,)).fetchall()
    conn.close()
    return ok(threads=[{'thread_id': r[0], 'title': dec(r[1]) if r[1] else '新しい会話', 'updated_at': r[2], 'url_key': r[3] if len(r)>3 else '', 'mode': r[4] if len(r)>4 else 'core'} for r in rows])

@bp.route('/api/qzero/threads/new', methods=['POST'])
def api_qzero_thread_new():
    # 新しいスレッドを作る
    u = _qzero_current()
    if not u:
        return err('ログインしてね', 401)
    import pytz as _p
    from datetime import datetime as _d
    now = _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')
    conn = _qzero_db()
    cur = conn.execute('INSERT INTO qzero_threads (owner, title, created_at, updated_at, url_key, mode) VALUES (?,?,?,?)',
                       (u, None, now, now))
    tid = cur.lastrowid
    conn.commit()
    _uk = conn.execute('SELECT url_key FROM qzero_threads WHERE thread_id=?', (tid,)).fetchone()
    conn.close()
    return ok(thread_id=tid, url_key=_uk[0] if _uk else '')

@bp.route('/api/qzero/threads/<int:thread_id>', methods=['GET'])
def api_qzero_thread_get(thread_id):
    # そのスレッドの会話を取り出す(本人のスレッドだけ)
    u = _qzero_current()
    if not u:
        return err('ログインしてね', 401)
    conn = _qzero_db()
    own = conn.execute('SELECT owner FROM qzero_threads WHERE thread_id=?', (thread_id,)).fetchone()
    if not own or own[0] != u:
        conn.close()
        return err('その会話は見られないよ', 403)
    rows = conn.execute('SELECT role, text, created_at FROM qzero_history WHERE thread_id=? ORDER BY id ASC', (thread_id,)).fetchall()
    conn.close()
    return ok(history=[{'role': r[0], 'text': dec(r[1]), 'created_at': r[2]} for r in rows])

@bp.route('/api/qzero/threads/<int:thread_id>/delete', methods=['POST'])
def api_qzero_thread_delete(thread_id):
    u = _qzero_current()
    if not u:
        return err('ログインしてね', 401)
    conn = _qzero_db()
    own = conn.execute('SELECT owner FROM qzero_threads WHERE thread_id=?', (thread_id,)).fetchone()
    if not own or own[0] != u:
        conn.close()
        return err('その会話は消せないよ', 403)
    conn.execute('DELETE FROM qzero_history WHERE thread_id=?', (thread_id,))
    conn.execute('DELETE FROM qzero_threads WHERE thread_id=?', (thread_id,))
    conn.commit()
    conn.close()
    return ok(message='消したよ')

@bp.route('/api/qzero/history/save', methods=['POST'])
def api_qzero_history_save():
    # 会話の1往復を暗号化して保存(ログイン時だけ)
    u = _qzero_current()
    if not u:
        return ok(saved=False)  # 未ログインは保存しない(エラーにはしない)
    data = request.get_json(silent=True) or {}
    role = data.get('role')
    text = str(data.get('text') or '')[:2000]
    thread_id = data.get('thread_id')
    if role not in ('me', 'ai') or not text:
        return err('保存する内容がおかしいよ')
    import pytz as _p
    from datetime import datetime as _d
    now = _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')
    conn = _qzero_db()
    # スレッドが指定されてなければ新規作成
    if not thread_id:
        cur = conn.execute('INSERT INTO qzero_threads (owner, title, created_at, updated_at) VALUES (?,?,?,?)', (u, None, now, now))
        thread_id = cur.lastrowid
    else:
        # 本人のスレッドか確認
        own = conn.execute('SELECT owner, title FROM qzero_threads WHERE thread_id=?', (thread_id,)).fetchone()
        if not own or own[0] != u:
            conn.close()
            return err('そのスレッドには保存できないよ', 403)
    conn.execute('INSERT INTO qzero_history (owner, role, text, created_at, thread_id) VALUES (?,?,?,?,?)',
                 (u, role, enc(text), now, thread_id))
    # 最初のユーザー発言をスレッドのタイトルにする(未設定なら)
    cur2 = conn.execute('SELECT title FROM qzero_threads WHERE thread_id=?', (thread_id,)).fetchone()
    if role == 'me' and (not cur2 or not cur2[0]):
        conn.execute('UPDATE qzero_threads SET title=? WHERE thread_id=?', (enc(text[:30]), thread_id))
    conn.execute('UPDATE qzero_threads SET updated_at=? WHERE thread_id=?', (now, thread_id))
    conn.commit()
    conn.close()
    return ok(saved=True, thread_id=thread_id)

@bp.route('/api/qzero/history', methods=['GET'])
def api_qzero_history():
    # 自分の会話履歴だけを取り出す(本人しか読めない)
    u = _qzero_current()
    if not u:
        return ok(history=[])
    conn = _qzero_db()
    rows = conn.execute('SELECT role, text, created_at FROM qzero_history WHERE owner=? ORDER BY id ASC LIMIT 500', (u,)).fetchall()
    conn.close()
    return ok(history=[{'role': r[0], 'text': dec(r[1]), 'created_at': r[2]} for r in rows])

# Guideモードの案内リスト(公開ページ)
QZERO_GUIDE_PUBLIC = [
    {'keywords': ['クイズ', '作', '投稿', '問題'], 'name': 'クイズ投稿',
     'url': '/', 'howto': 'トップページでグループに入ってから「クイズを作る」ボタンを押してね。問題文・答え・ヒントを入れて、画像も付けられるよ。'},
    {'keywords': ['クイズ', '解', '挑戦', '遊'], 'name': 'クイズに挑戦',
     'url': '/', 'howto': 'グループの合言葉で入ると、みんなのクイズに挑戦できるよ。正解するとランキングにのるんだ。'},
    {'keywords': ['タイピング', 'タイプ', 'キーボード', '打つ'], 'name': "QZEROタイピング",
     'url': '/typing', 'howto': '難易度を選んでスタート!60秒でどれだけ打てるかチャレンジ。ローマ字はsi/shiどっちの打ち方でもOKだよ。'},
    {'keywords': ['ライブラリ', '図鑑', '公式', '教科'], 'name': '公式ライブラリ',
     'url': '/library', 'howto': '教科や学年べつに公式クイズがそろってるよ。好きな分野を選んで挑戦してみて。'},
    {'keywords': ['イベント', '大会', 'コンテスト'], 'name': 'イベント',
     'url': '/', 'howto': 'グループのイベントに参加すると、期間限定の大会で競えるよ。結果発表もお楽しみに。'},
    {'keywords': ['バトル', '対戦', 'たいせん'], 'name': 'バトルモード',
     'url': '/', 'howto': 'ルームコードを友達と共有すると、リアルタイムでクイズ対戦ができるよ。'},
    {'keywords': ['会社', 'Qz', '運営', 'ホームページ', 'について'], 'name': '会社ホームページ',
     'url': '/homepage', 'howto': '運営会社の紹介ページだよ。サービスの歴史や数字も見られる。'},
    {'keywords': ['規約', 'ルール', 'プライバシー', '利用'], 'name': '利用規約',
     'url': '/terms', 'howto': 'サービスを使うときの約束ごとが書いてあるよ。'},
    {'keywords': ['天気', 'てんき'], 'name': 'QZERO Searchモード',
     'url': '/qzero', 'howto': '右上のモードをSearchに切り替えて「東京の天気」みたいに聞くと、天気や調べものに答えるよ。'},
    {'keywords': ['フィードバック', '要望', 'バグ', '不具合', '報告'], 'name': 'フィードバック',
     'url': '/', 'howto': 'ページの下のほうにある「フィードバックを送る」から、気づいたことを送ってね。全部読んでるよ。'},
]
# 社員ログイン中だけ案内する社内ページ(公安は秘密なので載せない)
QZERO_GUIDE_STAFF = [
    {'keywords': ['掲示板', 'メッセージ', 'チャット', '連絡'], 'name': '社内掲示板',
     'url': '/staff/board', 'howto': 'チャンネルを選んでメッセージを送れるよ。画像・ファイル・スタンプ・返信も使える。'},
    {'keywords': ['暗号', 'ひみつ', '秘密'], 'name': '暗号ツール',
     'url': '/staff/cipher', 'howto': 'キーを選んで文章を暗号化→コピーして掲示板に貼ったり、暗号メールで直接送れるよ。受信箱で解読もできる。'},
    {'keywords': ['ハンドブック', 'マニュアル', 'ルール', '社員'], 'name': '社員ハンドブック',
     'url': '/staff/handbook', 'howto': '社員としての心がまえやルールがまとまってるよ。困ったらまずここを見てね。'},
    {'keywords': ['給料', 'KP', 'ポイント', '残高'], 'name': 'KP(社内ポイント)',
     'url': '/staff/board', 'howto': '掲示板のKPメニューから残高を確認したり、社員どうしで送りあえるよ。'},
]

def _qzero_mini_allowed(version=None):
    # Miniの公開ルール:
    #   v9以降(エッジ世代) → ログインしていれば誰でもOK
    #   v8以前(旧世代)     → 管理者(role=admin)だけ。ID文字列の一致では判定しない
    u = session.get('qzero_user') or ''
    if not u:
        return False  # 未ログインは全世代NG
    try:
        v = float(version) if version is not None else 9
    except (TypeError, ValueError):
        v = 9
    if v >= 9:
        return True
    # 旧世代: 社員ログイン(s:)かつDBのroleがadminの人だけ
    return u.startswith('s:') and staff_is_admin()



@bp.route('/api/qzero/mini/spend', methods=['POST'])
def api_qzero_mini_spend():
    # エッジ推論の先払い窓口: 1回100トークン。OKなら生成許可を返す
    if not _qzero_mini_allowed():
        return err('Miniは準備中だよ(ベータテスト中)', 403)
    if not rate_limit(f'qzminispend:{client_ip()}', 30):
        return err('少し待ってね')
    if not _qzero_usage_spend(100):
        return _qzero_usage_err()
    return ok(allowed=True)

@bp.route('/api/qzero/mini/brain', methods=['GET'])
def api_qzero_mini_brain():
    # v9の脳みそをブラウザに配る(エッジ推論用)。キャッシュ1時間
    if not _qzero_mini_allowed():
        return err('Miniは準備中だよ(ベータテスト中)', 403)
    _o, _used, _limit, _t = _qzero_usage_state()
    if _used >= _limit:
        return _qzero_usage_err()  # 上限の人には脳みそ自体を渡さない(二重ロック)
    from flask import Response
    try:
        raw = open('/home/yuto113/qzero_mini_brain_v9.json', encoding='utf-8').read()
    except Exception:
        return err('脳みそが見つからないよ', 500)
    resp = Response(raw, mimetype='application/json')
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    return resp

@bp.route('/api/qzero/mini/status', methods=['GET'])
def api_qzero_mini_status():
    u = session.get('qzero_user') or ''
    can_legacy = u.startswith('s:') and staff_is_admin()
    return ok(allowed=_qzero_mini_allowed(), legacy=can_legacy)

@bp.route('/api/qzero/mini', methods=['POST'])
def api_qzero_mini():
    _ver = (request.get_json(silent=True) or {}).get('version')
    if not _qzero_mini_allowed(_ver):
        return err('Miniは準備中だよ(ベータテスト中)', 403)
    if not rate_limit(f'qzmini:{client_ip()}', 20):
        return err('少し待ってね')
    if not _qzero_usage_spend(500):  # Miniは計算が重いので500換算
        return _qzero_usage_err()
    from qz_qzero import mini as qzero_mini
    data = request.get_json(silent=True) or {}
    text = str(data.get('text') or '').strip()[:100]
    try:
        version = (request.get_json(silent=True) or {}).get('version')
        result = qzero_mini.generate(text, version)
    except Exception as e:
        return err('生成に失敗したよ: ' + str(e)[:60])
    if not result['ok']:
        vocab = ' '.join(qzero_mini.vocabulary(version))
        if result.get('unknown'):
            n_vocab = len(qzero_mini.vocabulary(version))
            return ok(generated=False,
                      reply='ごめん、「' + ' '.join(result['unknown']) + '」はまだ知らない言葉なんだ。\n\n私が知ってる' + str(n_vocab) + '語はこれだよ:\n' + vocab + '\n\nこの言葉で「ねこが」みたいに書き出しをくれたら、続きを作るよ!')
        return ok(generated=False,
                  reply='「ねこ が」みたいに、単語をスペースで区切った短い書き出しをちょうだい(4語まで)。\n\n使える言葉:\n' + vocab)
    inf = qzero_mini.info(version)
    return ok(generated=True, reply='続きを作ったよ:\n\n「' + result['text'] + '」\n\n(語彙' + str(inf['vocab']) + '語・DIM' + str(inf['dim']) + 'の自作トランスフォーマー。まだ勉強中だから、へんな文もあるよ)')

@bp.route('/api/qzero/guide', methods=['POST'])
def api_qzero_guide():
    # Guideモード: やりたいことに合うページを探して、使い方つきで案内
    if not rate_limit(f'qzguide:{client_ip()}', 30):
        return err('少し待ってね')
    data_pre = request.get_json(silent=True) or {}
    _cost = _qzero_tokens_of(data_pre.get('text')) + 150  # 入力+返事の概算
    if not _qzero_usage_spend(_cost):
        return _qzero_usage_err()
    data = request.get_json(silent=True) or {}
    text = str(data.get('text') or '').strip()[:300]
    if not text:
        return err('やりたいことを教えてね')
    guides = list(QZERO_GUIDE_PUBLIC)
    # 社内ページの案内は「QZEROに社員としてログイン中」の人だけ
    # (掲示板などのスタッフセッションがあっても、QZERO未ログインなら案内しない)
    qz_user = session.get('qzero_user') or ''
    if qz_user.startswith('s:'):
        guides += QZERO_GUIDE_STAFF
    # キーワードの「一致率」でいちばん合うものを選ぶ(何%のキーワードが含まれてたか)
    best, best_rate = None, 0.0
    for g in guides:
        hit = sum(1 for kw in g['keywords'] if kw.lower() in text.lower())
        rate = hit / len(g['keywords'])
        if rate > best_rate:
            best_rate, best = rate, g
    pct = round(best_rate * 100)
    if not best or pct < 20:
        return ok(found=False,
                  reply='見つからないなぁ…。「クイズを作りたい」「タイピングしたい」みたいに言ってみて!')
    # 一致率で返事の自信を変える(AIが自信の度合いを正直に伝える)
    if pct >= 80:
        opening = 'それならこれだね!「' + best['name'] + '」だよ。'
    elif pct >= 60:
        opening = '「' + best['name'] + '」…これであってる？'
    elif pct >= 40:
        opening = 'これかな？「' + best['name'] + '」かも。'
    else:
        opening = 'それはないかもだけど、似ているページならあるよ!!「' + best['name'] + '」はどう？'
    return ok(found=True, name=best['name'], url=best['url'], howto=best['howto'], confidence=pct,
              reply=opening + '\n\n📖 使い方: ' + best['howto'])


@bp.route('/api/qzero/usage/me', methods=['GET'])
def api_qzero_usage_me():
    # 自分の今日の使用量(画面の%ゲージ用)
    owner, used, limit, today = _qzero_usage_state()
    return ok(used=used, limit=limit,
              percent=min(100, round(100 * used / limit, 1)) if limit > 0 else 100,
              full=(used >= limit))

@bp.route('/api/qzero/usage/list', methods=['GET'])
def api_qzero_usage_list():
    # 管理者用: 今日の全アカウント使用量+追加量
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    import pytz as _p
    from datetime import datetime as _d
    today = _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d')
    conn = _qzero_usage_db()
    rows = conn.execute('SELECT owner, used FROM qzero_usage WHERE date=? ORDER BY used DESC', (today,)).fetchall()
    bonuses = dict(conn.execute('SELECT owner, extra FROM qzero_bonus').fetchall())
    conn.close()
    users = [{'owner': r[0], 'used': r[1], 'extra': bonuses.get(r[0], 0)} for r in rows]
    shown = {u['owner'] for u in users}
    for owner, extra in bonuses.items():
        if owner not in shown:
            users.append({'owner': owner, 'used': 0, 'extra': extra})
    return ok(users=users, date=today, base=QZERO_DAILY_TOKENS, base_guest=QZERO_DAILY_TOKENS_GUEST)

@bp.route('/api/qzero/usage/bonus', methods=['POST'])
def api_qzero_usage_bonus():
    # 管理者用: 個人への追加トークン設定(マイナス不可)
    if not staff_is_admin():
        return err('管理者だけだよ', 403)
    data = request.get_json(silent=True) or {}
    owner = str(data.get('owner') or '').strip()
    extra = int(data.get('extra') or 0)
    if not owner:
        return err('アカウントを指定してね')
    if extra < 0:
        return err('マイナスは設定できないよ')
    extra = min(1000000, extra)
    conn = _qzero_usage_db()
    conn.execute('INSERT INTO qzero_bonus (owner, extra) VALUES (?,?) '
                 'ON CONFLICT(owner) DO UPDATE SET extra=?', (owner, extra, extra))
    conn.commit()
    conn.close()
    return ok(message=owner + ' に +' + str(extra) + ' トークン追加したよ')

@bp.route('/qzero')
@bp.route('/qzero/')
def page_qzero():
    return render_template('qzero.html')

@bp.route('/chat/<mode>/<url_key>')
@bp.route('/qzero/chat/<mode>/<url_key>')
def page_chat_url(mode, url_key):
    import sqlite3 as _sq
    conn = _sq.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    row = conn.execute('SELECT thread_id, owner FROM qzero_threads WHERE url_key=?', (url_key,)).fetchone()
    conn.close()
    if not row:
        return redirect('/qzero')
    user = session.get('qzero_user', '')
    if row[1] and row[1] != user:
        return redirect('/qzero')
    return render_template('qzero.html', open_thread_id=row[0], open_mode=mode)


@bp.route('/qzero/api/predict', methods=['POST'])
def api_qzero_predict():
    # 教科あてAPI(外部サイトからも使える。これがCerebroの本体機能)
    if not rate_limit(f'qzero:{client_ip()}', 30):
        return err('少し待ってね')
    data_pre = request.get_json(silent=True) or {}
    _cost = _qzero_tokens_of(data_pre.get('text')) + 150  # 入力+返事の概算
    if not _qzero_usage_spend(_cost):
        return _qzero_usage_err()
    data = request.get_json(silent=True) or {}
    text = str(data.get('text') or '').strip()[:500]
    if not text:
        return err('textを入れてね')
    try:
        result = qzero_brain.predict_subject(text)
    except Exception as e:
        return err('予測に失敗したよ: ' + str(e)[:80])
    return ok(**result)

@bp.route('/qzero/api/chat', methods=['POST'])
def api_qzero_chat():
    # チャットAPI: 意図を読んで返事をする
    if not rate_limit(f'qzerochat:{client_ip()}', 30):
        return err('少し待ってね')
    data_pre = request.get_json(silent=True) or {}
    _cost = _qzero_tokens_of(data_pre.get('text')) + 150  # 入力+返事の概算
    if not _qzero_usage_spend(_cost):
        return _qzero_usage_err()
    data = request.get_json(silent=True) or {}
    text = str(data.get('text') or '').strip()[:500]
    if not text:
        return err('メッセージを入れてね')
    # まず「教わったこと」を思い出す(教科あてより優先)
    conn = _qzero_db()
    mems = [{'question': r[0], 'answer': dec(r[1])} for r in
            conn.execute('SELECT question, answer FROM qzero_memory').fetchall()]
    conn.close()
    hit = qzero_brain.match_memory(text, mems)
    if hit:
        return ok(reply=hit['answer'], intent='learned')

    # 次に「会話パターン(こう来たら・こう返す)」を確認する
    conn = _qzero_db()
    pats = [{'trigger': r[0], 'reply': dec(r[1])} for r in
            conn.execute('SELECT trigger, reply FROM qzero_patterns').fetchall()]
    conn.close()
    phit = qzero_brain.match_pattern(text, pats)
    if phit:
        return ok(reply=phit['reply'], intent='pattern')

    intent = qzero_brain.detect_intent(text)

    if intent == 'greeting':
        return ok(reply='こんにちは! 私はQZERO、Q\'zの自作AIだよ。クイズの教科を当てたり、クイズを探したりできる。何か問題文を見せてくれたら、何の教科か当ててみせるよ!', intent=intent)

    if intent == 'about':
        return ok(reply='私はQZERO。6331問のクイズで勉強した、Q\'z専用のAIだよ。\n・問題文を見せると「何の教科か」を当てる(正確率80%!)\n・「理科のクイズ出して」みたいに言うと、クイズを探す\nまだQ\'zの中のことが得意分野。これからもっと賢くなっていくよ。', intent=intent)

    if intent == 'find_quiz':
        topic = qzero_brain.strip_command(text)
        with get_db() as conn:
            cur = make_cursor(conn)
            cur.execute(q('SELECT question, answer, tags FROM quizzes ORDER BY RANDOM() LIMIT 200'))
            rows = [dict(r) for r in cur.fetchall()]
        hits = []
        for r in rows:
            question = dec(r.get('question') or '')
            tags = dec(r.get('tags') or '')
            if topic and (topic in question or topic in tags):
                hits.append((question, dec(r.get('answer') or '')))
            if len(hits) >= 3:
                break
        if hits:
            lines = '\n'.join(['・' + h[0] + ' (答え: ' + h[1] + ')' for h in hits])
            return ok(reply='「' + topic + '」に関するクイズを見つけたよ!\n' + lines, intent=intent)
        return ok(reply='「' + topic + '」のクイズは見つからなかった…。別の言葉で試してみて。クイズシェア本体にはもっとたくさんあるよ!', intent=intent)

    if intent == 'classify':
        # 「何科?」の前後にある問題文を教科判定にかける
        body = _re_qzero.sub(r'(これ|この問題|は)?(何科|なにか|なんか|教科|ジャンル|\?|？|。)', '', text).strip()
        target = body or text
        result = qzero_brain.predict_subject(target)
        return ok(reply='それは「' + result['subject'] + '」だと思う! (確信度 ' + str(result['confidence']) + '%)', intent=intent, detail=result)

    # わからない → 正直に言って、質問を記録する(Cerebroを育てる教材になる)
    import pytz as _p
    from datetime import datetime as _d
    conn = _qzero_db()
    conn.execute('INSERT INTO qzero_unknown (question, created_at) VALUES (?,?)',
                 (text, _d.now(_p.timezone('Asia/Tokyo')).strftime('%Y-%m-%d %H:%M')))
    conn.commit()
    conn.close()
    return ok(reply='ごめん、それはまだ答えられない…。でも今の質問は記録したよ。こうやって少しずつ賢くなっていくんだ。今は「問題文の教科当て」と「クイズ探し」が得意だよ!', intent='unknown')

