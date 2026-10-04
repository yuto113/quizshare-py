# -*- coding: utf-8 -*-
"""
Omame Mail ─ チャット
======================
・1対1 と グループ
・コード / QR で つながる（承認式。QRは即つながる）
・DB は 暗号化して 保存（盗まれても 安全）
・運営は「読まない約束」で 運用する
"""

import os, json, sqlite3, secrets, string, base64, hashlib
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, abort, Response)

bp_mail = Blueprint('mail', __name__)

# DB を まもる かぎ（サーバーだけが 持つ）
_MAIL_KEY = os.environ.get('MAIL_KEY', '')


from datetime import datetime, timedelta, timezone

def jst_str():
    return datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')


def _db():
    c = sqlite3.connect(os.environ.get('SQLITE_PATH',
                                       '/home/yuto113/quizshare.db'))
    c.row_factory = sqlite3.Row
    return c


def _me():
    from bp_member import me
    return me()


# ---- かんたんな 暗号化（DB用）----
def _key():
    """32バイトの かぎを つくる"""
    src = (_MAIL_KEY or 'omame-default-key-change-me')
    return hashlib.sha256(src.encode()).digest()


def enc(text):
    """文を 暗号化して 文字列に する"""
    if text is None:
        text = ''
    try:
        from cryptography.fernet import Fernet
        k = base64.urlsafe_b64encode(_key())
        return Fernet(k).encrypt(text.encode('utf-8')).decode('ascii')
    except Exception:
        # cryptography が なければ かんたんXOR（ないよりまし）
        k = _key()
        b = text.encode('utf-8')
        x = bytes(b[i] ^ k[i % len(k)] for i in range(len(b)))
        return 'x:' + base64.b64encode(x).decode('ascii')


def dec(token):
    if not token:
        return ''
    try:
        if token.startswith('x:'):
            k = _key()
            x = base64.b64decode(token[2:])
            b = bytes(x[i] ^ k[i % len(k)] for i in range(len(x)))
            return b.decode('utf-8', 'replace')
        from cryptography.fernet import Fernet
        k = base64.urlsafe_b64encode(_key())
        return Fernet(k).decrypt(token.encode('ascii')).decode('utf-8')
    except Exception:
        return '(よめませんでした)'


def new_code(n=10):
    alnum = string.ascii_uppercase + string.digits
    c = _db()
    while True:
        code = ''.join(secrets.choice(alnum) for _ in range(n))
        if not c.execute('SELECT 1 FROM mp_member WHERE mail_code=?',
                         (code,)).fetchone():
            c.close()
            return code


# ====================================================================
# つながる（コード / QR / 承認）
# ====================================================================
@bp_mail.route('/api/mail/me')
def api_mail_me():
    """じぶんの コードなど"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    r = c.execute('SELECT mail_code, nickname FROM mp_member WHERE member_id=?',
                  (u['member_id'],)).fetchone()
    c.close()
    return jsonify(ok=True, member_id=u['member_id'],
                   nickname=r['nickname'] if r else '',
                   code=r['mail_code'] if r else '')


@bp_mail.route('/api/mail/newcode', methods=['POST'])
def api_mail_newcode():
    """コードを 作り直す（つながって いる 人は そのまま）"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    code = new_code()
    c = _db()
    c.execute('UPDATE mp_member SET mail_code=? WHERE member_id=?',
              (code, u['member_id']))
    c.commit(); c.close()
    return jsonify(ok=True, code=code)


def _find_by_code(code):
    c = _db()
    r = c.execute('SELECT member_id, nickname FROM mp_member '
                  'WHERE mail_code=? AND status=?',
                  ((code or '').strip().upper(), 'active')).fetchone()
    c.close()
    return dict(r) if r else None


@bp_mail.route('/api/mail/find', methods=['POST'])
def api_mail_find():
    """コードで 相手を さがす（つなぐ 前の かくにん）"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    r = _find_by_code(d.get('code'))
    if not r:
        return jsonify(ok=False, error='その コードの 人は いません'), 404
    if r['member_id'] == u['member_id']:
        return jsonify(ok=False, error='じぶんです'), 400
    return jsonify(ok=True, member_id=r['member_id'], nickname=r['nickname'])


def _are_friends(a, b):
    c = _db()
    r = c.execute('SELECT 1 FROM oma_friend WHERE a=? AND b=?', (a, b)).fetchone()
    c.close()
    return bool(r)


def _make_friends(a, b):
    c = _db()
    for x, y in ((a, b), (b, a)):
        try:
            c.execute('INSERT INTO oma_friend(a,b) VALUES(?,?)', (x, y))
        except Exception:
            pass
    c.commit(); c.close()


@bp_mail.route('/api/mail/request', methods=['POST'])
def api_mail_request():
    """つながり ねがいを 出す（コードから。承認式）"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    r = _find_by_code(d.get('code'))
    if not r:
        return jsonify(ok=False, error='その コードの 人は いません'), 404
    to = r['member_id']
    if to == u['member_id']:
        return jsonify(ok=False, error='じぶんです'), 400
    if _is_blocked(u['member_id'], to):
        return jsonify(ok=False, error='この人とは つながれません'), 403
    if _are_friends(u['member_id'], to):
        return jsonify(ok=True, already=True)

    c = _db()
    # QR から なら すぐ つなぐ
    if d.get('via') == 'qr':
        c.close()
        _make_friends(u['member_id'], to)
        _ensure_dm(u['member_id'], to)
        return jsonify(ok=True, connected=True)
    # ふつうは 承認まち
    try:
        c.execute("INSERT INTO oma_friend_req(from_id,to_id) VALUES(?,?)",
                  (u['member_id'], to))
    except Exception:
        pass
    # 相手に しらせる
    from bp_member import notify
    c.execute("""INSERT INTO mp_notice(member_id,kind,actor,body,created_at)
                 VALUES(?,?,?,?,?)""",
              (to, 'mail_req', u['member_id'],
               (u.get('nickname') or u['member_id'])
               + ' さんが つながりたいそうです', jst_str()))
    c.commit(); c.close()
    return jsonify(ok=True, waiting=True)


@bp_mail.route('/api/mail/requests')
def api_mail_requests():
    """じぶんに 来た つながり ねがい"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT r.id, r.from_id, m.nickname
        FROM oma_friend_req r LEFT JOIN mp_member m ON m.member_id=r.from_id
        WHERE r.to_id=? AND r.status='wait' ORDER BY r.id DESC""",
        (u['member_id'],)).fetchall()]
    c.close()
    return jsonify(ok=True, requests=rows)


@bp_mail.route('/api/mail/answer', methods=['POST'])
def api_mail_answer():
    """つながり ねがいに こたえる"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    c = _db()
    r = c.execute("SELECT * FROM oma_friend_req WHERE id=? AND to_id=?",
                  (d.get('id'), u['member_id'])).fetchone()
    if not r:
        c.close(); return jsonify(ok=False, error='ありません'), 404
    ok = bool(d.get('ok'))
    c.execute("UPDATE oma_friend_req SET status=? WHERE id=?",
              ('ok' if ok else 'no', r['id']))
    c.commit(); c.close()
    if ok:
        _make_friends(u['member_id'], r['from_id'])
        _ensure_dm(u['member_id'], r['from_id'])
    return jsonify(ok=True)


# ====================================================================
# チャット（1対1・グループ）
# ====================================================================
def _ensure_dm(a, b):
    """2人の 1対1ルームが なければ 作る。あれば その id。"""
    c = _db()
    r = c.execute("""SELECT r.id FROM oma_room r
        JOIN oma_room_member m1 ON m1.room_id=r.id AND m1.member_id=?
        JOIN oma_room_member m2 ON m2.room_id=r.id AND m2.member_id=?
        WHERE r.kind='dm'""", (a, b)).fetchone()
    if r:
        c.close()
        return r['id']
    cur = c.execute("INSERT INTO oma_room(kind) VALUES('dm')")
    rid = cur.lastrowid
    for m in (a, b):
        c.execute('INSERT INTO oma_room_member(room_id,member_id) VALUES(?,?)',
                  (rid, m))
    c.commit(); c.close()
    return rid


def _in_room(rid, member_id):
    c = _db()
    r = c.execute('SELECT 1 FROM oma_room_member WHERE room_id=? AND member_id=?',
                  (rid, member_id)).fetchone()
    c.close()
    return bool(r)


@bp_mail.route('/api/mail/rooms')
def api_mail_rooms():
    """じぶんの トークルーム 一覧"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    me_id = u['member_id']
    c = _db()
    rooms = []
    for r in c.execute("""
        SELECT r.*, rm.last_read FROM oma_room r
        JOIN oma_room_member rm ON rm.room_id=r.id AND rm.member_id=?
        ORDER BY r.id DESC""", (me_id,)).fetchall():
        d = dict(r)
        # 1対1なら 相手の 名前を だす
        if r['kind'] == 'dm':
            o = c.execute("""SELECT m.member_id, m.nickname, m.avatar,
                             m.avatar_kind, m.color
                             FROM oma_room_member rm
                             JOIN mp_member m ON m.member_id=rm.member_id
                             WHERE rm.room_id=? AND rm.member_id<>?""",
                          (r['id'], me_id)).fetchone()
            d['title'] = (o['nickname'] if o else 'だれか')
            d['other'] = dict(o) if o else None
        else:
            d['title'] = r['name'] or 'グループ'
            n = c.execute('SELECT COUNT(*) AS n FROM oma_room_member '
                          'WHERE room_id=?', (r['id'],)).fetchone()
            d['members'] = n['n']
        # さいごの メッセージ
        last = c.execute("""SELECT id, member_id, body, created_at FROM oma_msg
                            WHERE room_id=? AND hidden=0
                            ORDER BY id DESC LIMIT 1""", (r['id'],)).fetchone()
        if last:
            d['last'] = dec(last['body'])[:30]
            d['last_at'] = last['created_at']
            d['last_id'] = last['id']
            # まだ 読んで いない かず
            un = c.execute("""SELECT COUNT(*) AS n FROM oma_msg
                              WHERE room_id=? AND id>? AND member_id<>? AND hidden=0""",
                           (r['id'], r['last_read'] or 0, me_id)).fetchone()
            d['unread'] = un['n']
        else:
            d['last'] = ''; d['last_at'] = None; d['unread'] = 0
        rooms.append(d)
    c.close()
    # あたらしい メッセージ順に
    rooms.sort(key=lambda x: (x.get('last_id') or 0), reverse=True)
    return jsonify(ok=True, rooms=rooms, me=me_id)


@bp_mail.route('/api/mail/room/<int:rid>')
def api_mail_room(rid):
    """1つの ルームの メッセージ"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    if not _in_room(rid, u['member_id']):
        return jsonify(ok=False, error='入って いません'), 403
    after = request.args.get('after', '0')
    c = _db()
    room = dict(c.execute('SELECT * FROM oma_room WHERE id=?', (rid,)).fetchone())
    msgs = []
    for m in c.execute("""SELECT o.*, mm.nickname, mm.avatar, mm.avatar_kind,
                          mm.color FROM oma_msg o
                          LEFT JOIN mp_member mm ON mm.member_id=o.member_id
                          WHERE o.room_id=? AND o.id>? AND o.hidden=0
                          ORDER BY o.id LIMIT 200""",
                       (rid, after)).fetchall():
        d = dict(m)
        d['body'] = dec(m['body'])
        if m['kind'] == 'image' and m['extra']:
            d['extra'] = dec(m['extra'])    # 画像を もどす
        # 返信元の 中身
        if m['reply_to']:
            rp = c.execute('SELECT member_id, body, kind FROM oma_msg WHERE id=?',
                           (m['reply_to'],)).fetchone()
            if rp:
                d['reply'] = {'member_id': rp['member_id'],
                              'body': (dec(rp['body'])[:40] if rp['kind']=='text'
                                       else '［' + (rp['kind']) + '］')}
        # リアクション
        rs = c.execute('SELECT emoji, COUNT(*) AS n, '
                       'SUM(CASE WHEN member_id=? THEN 1 ELSE 0 END) AS mine '
                       'FROM oma_react WHERE msg_id=? GROUP BY emoji',
                       (u['member_id'], m['id'])).fetchall()
        d['reacts'] = [dict(x) for x in rs]
        msgs.append(d)
    # だれが どこまで 読んだか（既読）
    reads = [dict(r) for r in c.execute(
        'SELECT member_id, last_read FROM oma_room_member WHERE room_id=?',
        (rid,)).fetchall()]
    # メンバー
    mems = [dict(r) for r in c.execute("""
        SELECT m.member_id, m.nickname FROM oma_room_member rm
        JOIN mp_member m ON m.member_id=rm.member_id WHERE rm.room_id=?""",
        (rid,)).fetchall()]
    c.close()
    return jsonify(ok=True, room=room, msgs=msgs, reads=reads,
                   members=mems, me=u['member_id'])


@bp_mail.route('/api/mail/send', methods=['POST'])
def api_mail_send():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    rid = d.get('room_id')
    body = (d.get('body') or '').strip()
    if not _in_room(rid, u['member_id']):
        return jsonify(ok=False, error='入って いません'), 403
    _kind = (d.get('kind') or 'text')
    if not body and _kind == 'text':
        return jsonify(ok=False, error='なにか 書いて'), 400
    if len(body) > 2000:
        return jsonify(ok=False, error='ながすぎます'), 400
    # 1対1で ブロックの 関係なら 送れない
    cc = _db()
    rk = cc.execute('SELECT kind FROM oma_room WHERE id=?', (rid,)).fetchone()
    if rk and rk['kind'] == 'dm':
        other = cc.execute('SELECT member_id FROM oma_room_member '
                           'WHERE room_id=? AND member_id<>?',
                           (rid, u['member_id'])).fetchone()
        cc.close()
        if other and _is_blocked(u['member_id'], other['member_id']):
            return jsonify(ok=False, error='この人とは やりとり できません'), 403
    else:
        cc.close()
    c = _db()
    kind = d.get('kind') or 'text'
    extra = d.get('extra') or None
    reply = d.get('reply_to') or None
    cur = c.execute("""INSERT INTO oma_msg(room_id,member_id,body,created_at,
                       kind,extra,reply_to) VALUES(?,?,?,?,?,?,?)""",
                    (rid, u['member_id'], enc(body), jst_str(),
                     kind, extra, reply))
    mid = cur.lastrowid
    # じぶんは そこまで 読んだ ことに
    c.execute('UPDATE oma_room_member SET last_read=? WHERE room_id=? '
              'AND member_id=?', (mid, rid, u['member_id']))
    c.commit(); c.close()
    return jsonify(ok=True, id=mid)


@bp_mail.route('/api/mail/read', methods=['POST'])
def api_mail_read():
    """ここまで 読んだ、を きろく（既読）"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    rid = d.get('room_id')
    mid = d.get('msg_id') or 0
    if not _in_room(rid, u['member_id']):
        return jsonify(ok=False), 403
    c = _db()
    c.execute('UPDATE oma_room_member SET last_read=? WHERE room_id=? '
              'AND member_id=? AND last_read<?', (mid, rid, u['member_id'], mid))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp_mail.route('/api/mail/group', methods=['POST'])
def api_mail_group():
    """グループを 作る"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify(ok=False, error='名前を 入れて'), 400
    members = d.get('members') or []      # つながって いる 人の ID
    c = _db()
    cur = c.execute("INSERT INTO oma_room(kind,name,owner) VALUES('group',?,?)",
                    (name[:40], u['member_id']))
    rid = cur.lastrowid
    c.execute('INSERT INTO oma_room_member(room_id,member_id) VALUES(?,?)',
              (rid, u['member_id']))
    for m in members:
        if m != u['member_id']:
            try:
                c.execute('INSERT INTO oma_room_member(room_id,member_id) '
                          'VALUES(?,?)', (rid, m))
            except Exception:
                pass
    c.commit(); c.close()
    return jsonify(ok=True, id=rid)


@bp_mail.route('/api/mail/friends')
def api_mail_friends():
    """つながって いる 人の 一覧"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT m.member_id, m.nickname, m.avatar, m.avatar_kind, m.color
        FROM oma_friend f JOIN mp_member m ON m.member_id=f.b
        WHERE f.a=? ORDER BY m.nickname""", (u['member_id'],)).fetchall()]
    c.close()
    return jsonify(ok=True, friends=rows)


@bp_mail.route('/api/mail/start', methods=['POST'])
def api_mail_start():
    """相手と 1対1を はじめる（ルームを 作って id を 返す）"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    to = d.get('member_id')
    if not to or not _are_friends(u['member_id'], to):
        return jsonify(ok=False, error='つながって いません'), 403
    rid = _ensure_dm(u['member_id'], to)
    return jsonify(ok=True, room_id=rid)



@bp_mail.route('/api/mail/qr')
def api_mail_qr():
    """じぶんの つながりコードを QR画像に して 返す"""
    u = _me()
    if not u:
        abort(403)
    c = _db()
    r = c.execute('SELECT mail_code FROM mp_member WHERE member_id=?',
                  (u['member_id'],)).fetchone()
    c.close()
    if not r or not r['mail_code']:
        abort(404)
    import qrcode, io
    # QR に 入れる 中身：つなぐ ための URL
    host = request.host_url.rstrip('/')
    data = host + '/oma_mail?add=' + r['mail_code']
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return Response(buf.read(), mimetype='image/png')


@bp_mail.route('/oma_mail')
def oma_mail_page():
    from bp_member import me
    if not me():
        return redirect('/oma_pj')
    from flask import render_template
    return render_template('oma/mail.html', page='mail')


# ====================================================================
# 新着の かず（ヘッダー用）
# ====================================================================
@bp_mail.route('/api/mail/unread')
def api_mail_unread():
    """まだ 読んで いない メッセージの ごうけい"""
    u = _me()
    if not u:
        return jsonify(ok=False, n=0), 401
    me_id = u['member_id']
    c = _db()
    r = c.execute("""
        SELECT COALESCE(SUM(x.un), 0) AS n FROM (
          SELECT (SELECT COUNT(*) FROM oma_msg o
                  WHERE o.room_id=rm.room_id AND o.id>rm.last_read
                  AND o.member_id<>? AND o.hidden=0) AS un
          FROM oma_room_member rm WHERE rm.member_id=?
        ) x""", (me_id, me_id)).fetchone()
    # つながり ねがいの かずも
    req = c.execute("SELECT COUNT(*) AS n FROM oma_friend_req "
                    "WHERE to_id=? AND status='wait'", (me_id,)).fetchone()
    c.close()
    return jsonify(ok=True, n=(r['n'] or 0), req=(req['n'] or 0))


# ====================================================================
# グループを 抜ける
# ====================================================================
@bp_mail.route('/api/mail/leave', methods=['POST'])
def api_mail_leave():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    rid = d.get('room_id')
    c = _db()
    room = c.execute('SELECT kind, owner FROM oma_room WHERE id=?', (rid,)).fetchone()
    if not room:
        c.close(); return jsonify(ok=False, error='ありません'), 404
    if room['kind'] != 'group':
        c.close(); return jsonify(ok=False, error='1対1は 抜けられません'), 400
    # 抜ける
    c.execute('DELETE FROM oma_room_member WHERE room_id=? AND member_id=?',
              (rid, u['member_id']))
    # みんな 抜けたら 部屋も けす
    n = c.execute('SELECT COUNT(*) AS n FROM oma_room_member WHERE room_id=?',
                  (rid,)).fetchone()
    if n and n['n'] == 0:
        c.execute('DELETE FROM oma_msg WHERE room_id=?', (rid,))
        c.execute('DELETE FROM oma_room WHERE id=?', (rid,))
    else:
        # 「◯◯が ぬけました」と のこす
        c.execute("""INSERT INTO oma_msg(room_id,member_id,body,created_at)
                     VALUES(?,?,?,?)""",
                  (rid, u['member_id'],
                   enc('（' + (u.get('nickname') or u['member_id'])
                       + ' が ぬけました）'), jst_str()))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# グループに 人を さそう（あとから）
# ====================================================================
@bp_mail.route('/api/mail/invite', methods=['POST'])
def api_mail_invite():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    rid = d.get('room_id')
    who = d.get('member_id')
    c = _db()
    room = c.execute('SELECT kind FROM oma_room WHERE id=?', (rid,)).fetchone()
    if not room or room['kind'] != 'group':
        c.close(); return jsonify(ok=False, error='グループでは ありません'), 400
    if not _in_room(rid, u['member_id']):
        c.close(); return jsonify(ok=False, error='入って いません'), 403
    if not _are_friends(u['member_id'], who):
        c.close(); return jsonify(ok=False, error='つながって いる 人だけ'), 403
    try:
        c.execute('INSERT INTO oma_room_member(room_id,member_id) VALUES(?,?)',
                  (rid, who))
        nick = c.execute('SELECT nickname FROM mp_member WHERE member_id=?',
                         (who,)).fetchone()
        c.execute("""INSERT INTO oma_msg(room_id,member_id,body,created_at)
                     VALUES(?,?,?,?)""",
                  (rid, u['member_id'],
                   enc('（' + (nick['nickname'] if nick else who)
                       + ' が 入りました）'), jst_str()))
        c.commit()
    except Exception:
        pass
    c.close()
    return jsonify(ok=True)


# ====================================================================
# ブロック
# ====================================================================
def _is_blocked(a, b):
    """a が b を ブロックして いるか（どちらかが していれば True）"""
    c = _db()
    r = c.execute('SELECT 1 FROM oma_block WHERE '
                  '(member_id=? AND blocked=?) OR (member_id=? AND blocked=?)',
                  (a, b, b, a)).fetchone()
    c.close()
    return bool(r)


@bp_mail.route('/api/mail/block', methods=['POST'])
def api_mail_block():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    who = d.get('member_id')
    on = bool(d.get('block'))
    c = _db()
    if on:
        try:
            c.execute('INSERT INTO oma_block(member_id,blocked) VALUES(?,?)',
                      (u['member_id'], who))
        except Exception:
            pass
    else:
        c.execute('DELETE FROM oma_block WHERE member_id=? AND blocked=?',
                  (u['member_id'], who))
    c.commit(); c.close()
    return jsonify(ok=True, blocked=on)


@bp_mail.route('/api/mail/blocks')
def api_mail_blocks():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT b.blocked AS member_id, m.nickname
        FROM oma_block b LEFT JOIN mp_member m ON m.member_id=b.blocked
        WHERE b.member_id=?""", (u['member_id'],)).fetchall()]
    c.close()
    return jsonify(ok=True, blocks=rows)


# ====================================================================
# 通報
# ====================================================================
@bp_mail.route('/api/mail/report', methods=['POST'])
def api_mail_report():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    rid = d.get('room_id')
    mid = d.get('msg_id')
    c = _db()
    snap = None; target = None
    if mid:
        m = c.execute('SELECT member_id, body FROM oma_msg WHERE id=?',
                      (mid,)).fetchone()
        if m:
            snap = m['body']        # 暗号の まま のこす
            target = m['member_id']
    c.execute("""INSERT INTO oma_mail_report(by_member,room_id,msg_id,target,
                 reason,snapshot,created_at) VALUES(?,?,?,?,?,?,?)""",
              (u['member_id'], rid, mid, target or d.get('target'),
               (d.get('reason') or '')[:300], snap, jst_str()))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# リアクション
# ====================================================================
@bp_mail.route('/api/mail/react', methods=['POST'])
def api_mail_react():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    mid = d.get('msg_id')
    emoji = (d.get('emoji') or '')[:8]
    if not mid or not emoji:
        return jsonify(ok=False), 400
    c = _db()
    hit = c.execute('SELECT 1 FROM oma_react WHERE msg_id=? AND member_id=? '
                    'AND emoji=?', (mid, u['member_id'], emoji)).fetchone()
    if hit:
        c.execute('DELETE FROM oma_react WHERE msg_id=? AND member_id=? AND emoji=?',
                  (mid, u['member_id'], emoji))
        on = False
    else:
        c.execute('INSERT INTO oma_react(msg_id,member_id,emoji) VALUES(?,?,?)',
                  (mid, u['member_id'], emoji))
        on = True
    c.commit(); c.close()
    return jsonify(ok=True, on=on)


# ====================================================================
# スタンプ
# ====================================================================
@bp_mail.route('/api/mail/stamps')
def api_mail_stamps():
    """つかえる スタンプ 一覧（買った ものと 無料）"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT s.*, (SELECT 1 FROM oma_stamp_own o
                     WHERE o.stamp_id=s.id AND o.member_id=?) AS mine
        FROM oma_stamp s WHERE s.active=1 ORDER BY s.price, s.id""",
        (u['member_id'],)).fetchall()]
    c.close()
    try:
        import oma_pt
        pt = oma_pt.get_pt(u['member_id'])['pt']
    except Exception:
        pt = 0
    # つかえるか（無料 or 買った）
    for r in rows:
        r['usable'] = (r['price'] == 0 or r['mine'])
    return jsonify(ok=True, stamps=rows, pt=pt)


@bp_mail.route('/api/mail/stamp/<code>/buy', methods=['POST'])
def api_mail_stamp_buy(code):
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    st = c.execute('SELECT * FROM oma_stamp WHERE code=? AND active=1',
                   (code,)).fetchone()
    if not st:
        c.close(); return jsonify(ok=False, error='ありません'), 404
    if st['price'] <= 0:
        c.close(); return jsonify(ok=True, free=True)
    own = c.execute('SELECT 1 FROM oma_stamp_own WHERE member_id=? AND stamp_id=?',
                    (u['member_id'], st['id'])).fetchone()
    c.close()
    if own:
        return jsonify(ok=True, already=True)
    import oma_pt
    r = oma_pt.spend_pt(u['member_id'], st['price'], 'stamp',
                        ref_id='st%s' % st['id'], detail='スタンプ「%s」' % st['name'])
    if not r['ok']:
        return jsonify(ok=False, error=r['why'],
                       need=r.get('need'), have=r.get('have')), 402
    c = _db()
    c.execute('INSERT OR IGNORE INTO oma_stamp_own(member_id,stamp_id) VALUES(?,?)',
              (u['member_id'], st['id']))
    c.commit(); c.close()
    return jsonify(ok=True, pt=r['pt'])


# ====================================================================
# 画像（おえかき から 送る）
# ====================================================================
@bp_mail.route('/api/mail/send_draw', methods=['POST'])
def api_mail_send_draw():
    """じぶんの おえかきを チャットに 送る"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    rid = d.get('room_id')
    draw_id = d.get('draw_id')
    if not _in_room(rid, u['member_id']):
        return jsonify(ok=False, error='入って いません'), 403
    c = _db()
    dr = c.execute('SELECT thumb FROM mp_draw WHERE id=? AND member_id=?',
                   (draw_id, u['member_id'])).fetchone()
    if not dr:
        c.close(); return jsonify(ok=False, error='その えは ありません'), 404
    cur = c.execute("""INSERT INTO oma_msg(room_id,member_id,body,created_at,
                       kind,extra) VALUES(?,?,?,?,'image',?)""",
                    (rid, u['member_id'], enc(''), jst_str(),
                     enc(dr['thumb'] or '')))
    mid = cur.lastrowid
    c.execute('UPDATE oma_room_member SET last_read=? WHERE room_id=? '
              'AND member_id=?', (mid, rid, u['member_id']))
    c.commit(); c.close()
    return jsonify(ok=True, id=mid)


@bp_mail.route('/api/mail/mydraws')
def api_mail_mydraws():
    """じぶんの おえかき（送る 用）"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    rows = [dict(r) for r in c.execute(
        "SELECT id, title, thumb FROM mp_draw WHERE member_id=? AND hidden=0 "
        "ORDER BY id DESC LIMIT 40", (u['member_id'],)).fetchall()]
    c.close()
    return jsonify(ok=True, draws=rows)
