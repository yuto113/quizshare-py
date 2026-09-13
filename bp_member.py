# -*- coding: utf-8 -*-
"""
MIRAI POWER 会員システム
========================
会員が Python / HTML / CSS / JS / SQLite でWebアプリを作り、
会員のなかで公開できる場。

大事な決めごと
  ・投稿されたコードはサーバーで実行しない。すべてブラウザの中で動かす。
    （Pyodide / sql.js）他人のコードをサーバーで動かすと、
    ファイルもDBも危険にさらされるため。
  ・下書きはいくつでも作れる。公開できる数だけ制限する。
"""

import os, json, sqlite3, secrets, time
from flask import Blueprint, render_template, request, jsonify, session, redirect

from qz_common import hash_password, verify_password, rate_limit, client_ip

bp = Blueprint('member', __name__)

# 公開できるアプリの数
LIMITS = {'admin': 10 ** 9, 'staff': 20, 'invited': 20, 'regular': 15}

CODE_MAX = 50000        # 1つの言語につき50KB


def _db():
    c = sqlite3.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    c.row_factory = sqlite3.Row
    return c


def me():
    """ログイン中の会員。いなければ None"""
    mid = session.get('mp_member')
    if not mid:
        return None
    c = _db()
    r = c.execute('SELECT * FROM mp_member WHERE member_id=?', (mid,)).fetchone()
    c.close()
    if not r or r['status'] != 'active':
        return None
    return dict(r)


def my_tier(m):
    """公開できる数を決める区分。社員と管理者は管理センター側で判定。"""
    if not m:
        return 'regular'
    sid = session.get('staff_id')
    if sid:
        try:
            from app import admin_center_role
            role = admin_center_role()
            if role == 'admin':
                return 'admin'
            if role == 'staff':
                return 'staff'
        except Exception:
            pass
    return m.get('tier') or 'regular'


def published_count(mid):
    c = _db()
    r = c.execute("SELECT COUNT(*) AS n FROM mp_app "
                  "WHERE member_id=? AND status='published'", (mid,)).fetchone()
    c.close()
    return r['n'] if r else 0


# ====================================================================
# 会員登録・ログイン
# ====================================================================
@bp.route('/api/mp/register', methods=['POST'])
def mp_register():
    if not rate_limit('mpreg:' + client_ip(), 5):
        return jsonify(ok=False, error='登録の試行が多すぎます'), 429
    import re
    d = request.get_json(silent=True) or {}
    mid = (d.get('member_id') or '').strip()
    nick = (d.get('nickname') or '').strip()
    pw = d.get('password') or ''
    if not re.fullmatch(r'[A-Za-z0-9_]{3,20}', mid):
        return jsonify(ok=False, error='IDは半角英数字とアンダースコアで3〜20文字'), 400
    if not nick or len(nick) > 20:
        return jsonify(ok=False, error='ニックネームは1〜20文字'), 400
    if len(pw) < 6:
        return jsonify(ok=False, error='パスワードは6文字以上'), 400

    code = (d.get('invite') or '').strip().upper()
    tier, inviter = 'regular', None

    c = _db()
    if c.execute('SELECT 1 FROM mp_member WHERE member_id=?', (mid,)).fetchone():
        c.close()
        return jsonify(ok=False, error='そのIDは使われています'), 409

    if code:
        inv = c.execute("SELECT * FROM mp_invite WHERE code=? AND active=1",
                        (code,)).fetchone()
        if not inv:
            c.close()
            return jsonify(ok=False, error='招待コードが見つかりません'), 400
        if inv['max_uses'] >= 0 and inv['used_count'] >= inv['max_uses']:
            c.close()
            return jsonify(ok=False, error='この招待コードは使い切られています'), 400
        tier, inviter = 'invited', inv['owner']
        c.execute('UPDATE mp_invite SET used_count=used_count+1 WHERE code=?', (code,))

    c.execute("""INSERT INTO mp_member
        (member_id,nickname,password_hash,email,tier,invited_by,invite_code,
         grade,school) VALUES(?,?,?,?,?,?,?,?,?)""",
        (mid, nick, hash_password(pw), (d.get('email') or '')[:200],
         tier, inviter, code or None,
         (d.get('grade') or '')[:20], (d.get('school') or '')[:40]))
    c.commit(); c.close()

    session['mp_member'] = mid
    return jsonify(ok=True, tier=tier, limit=LIMITS[tier])


@bp.route('/api/mp/login', methods=['POST'])
def mp_login():
    ip = client_ip()
    d = request.get_json(silent=True) or {}
    mid = (d.get('member_id') or '').strip()
    if not rate_limit('mplogin:' + ip, 5) or not rate_limit('mplogin_id:' + mid, 5):
        return jsonify(ok=False, error='ログインの試行が多すぎます'), 429
    c = _db()
    r = c.execute('SELECT * FROM mp_member WHERE member_id=?', (mid,)).fetchone()
    if not r or not verify_password(d.get('password') or '', r['password_hash']):
        c.close()
        return jsonify(ok=False, error='IDまたはパスワードが違います'), 401
    if r['status'] != 'active':
        c.close()
        return jsonify(ok=False, error='このアカウントは利用できません'), 403
    c.execute("UPDATE mp_member SET last_login=datetime('now','localtime') "
              "WHERE member_id=?", (mid,))
    c.commit(); c.close()
    session['mp_member'] = mid
    return jsonify(ok=True, nickname=r['nickname'])


@bp.route('/api/mp/logout', methods=['POST'])
def mp_logout():
    session.pop('mp_member', None)
    return jsonify(ok=True)


@bp.route('/api/mp/me')
def mp_me():
    m = me()
    if not m:
        return jsonify(ok=True, logged_in=False)
    t = my_tier(m)
    return jsonify(ok=True, logged_in=True, member_id=m['member_id'],
                   nickname=m['nickname'], tier=t, limit=LIMITS[t],
                   published=published_count(m['member_id']),
                   grade=m.get('grade'), school=m.get('school'),
                   furigana=bool(m.get('furigana')))


@bp.route('/api/mp/profile', methods=['POST'])
def mp_profile():
    m = me()
    if not m:
        return jsonify(ok=False, error='ログインしてください'), 401
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute("UPDATE mp_member SET nickname=?, grade=?, school=?, furigana=? "
              "WHERE member_id=?",
              ((d.get('nickname') or m['nickname'])[:20],
               (d.get('grade') or '')[:20], (d.get('school') or '')[:40],
               1 if d.get('furigana') else 0, m['member_id']))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# アプリ
# ====================================================================
def _clip(v):
    return (v or '')[:CODE_MAX]


@bp.route('/api/mp/apps')
def mp_apps():
    """公開されているアプリの一覧。会員でなくても見られる（宣伝になる）。"""
    q = (request.args.get('q') or '').strip()
    tag = (request.args.get('tag') or '').strip()
    school = (request.args.get('school') or '').strip()
    sort = request.args.get('sort') or 'new'
    mine = request.args.get('mine')

    sql = ("SELECT a.id,a.member_id,a.title,a.summary,a.tags,a.difficulty,"
           "a.school,a.views,a.likes,a.created_at,a.forked_from,a.status,"
           "m.nickname,m.grade "
           "FROM mp_app a LEFT JOIN mp_member m ON m.member_id=a.member_id WHERE ")
    args = []
    if mine:
        u = me()
        if not u:
            return jsonify(ok=False, error='ログインしてください'), 401
        sql += "a.member_id=? AND a.status<>'removed' "
        args.append(u['member_id'])
    else:
        sql += "a.status='published' "
    if q:
        sql += "AND (a.title LIKE ? OR a.summary LIKE ?) "
        args += ['%' + q + '%', '%' + q + '%']
    if tag:
        sql += "AND a.tags LIKE ? "; args.append('%' + tag + '%')
    if school:
        sql += "AND a.school LIKE ? "; args.append('%' + school + '%')
    sql += {'like': 'ORDER BY a.likes DESC, a.id DESC',
            'view': 'ORDER BY a.views DESC, a.id DESC'}.get(sort,
            'ORDER BY a.id DESC')
    sql += ' LIMIT 60'

    c = _db()
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    # 集計（運動が広がっているのが見えるように）
    tot = c.execute("SELECT COUNT(*) AS apps, COUNT(DISTINCT member_id) AS people "
                    "FROM mp_app WHERE status='published'").fetchone()
    c.close()
    return jsonify(ok=True, apps=rows,
                   total_apps=(tot['apps'] if tot else 0),
                   total_people=(tot['people'] if tot else 0))


@bp.route('/api/mp/app/<int:aid>')
def mp_app_get(aid):
    c = _db()
    r = c.execute("SELECT a.*, m.nickname, m.grade FROM mp_app a "
                  "LEFT JOIN mp_member m ON m.member_id=a.member_id "
                  "WHERE a.id=?", (aid,)).fetchone()
    if not r:
        c.close()
        return jsonify(ok=False, error='見つかりません'), 404
    d = dict(r)
    u = me()
    if d['status'] != 'published' and (not u or u['member_id'] != d['member_id']):
        c.close()
        return jsonify(ok=False, error='公開されていません'), 403
    c.execute('UPDATE mp_app SET views=views+1 WHERE id=?', (aid,))
    liked = False
    if u:
        liked = bool(c.execute('SELECT 1 FROM mp_app_like WHERE app_id=? AND member_id=?',
                               (aid, u['member_id'])).fetchone())
    c.commit(); c.close()
    d['liked'] = liked
    d['is_mine'] = bool(u and u['member_id'] == d['member_id'])
    return jsonify(ok=True, app=d)


@bp.route('/api/mp/app', methods=['POST'])
def mp_app_save():
    """新規作成と更新。公開の数だけ制限する。下書きは自由。"""
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    d = request.get_json(silent=True) or {}
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify(ok=False, error='題名を入れてください'), 400
    want = 'published' if d.get('publish') else 'draft'
    aid = d.get('id')

    # 公開する前に、決まりごとへの同意を確かめる
    if want == 'published':
        c0 = _db()
        agreed = c0.execute('SELECT 1 FROM mp_agree WHERE member_id=? AND version=?',
                            (u['member_id'], RULES_VERSION)).fetchone()
        c0.close()
        if not agreed:
            return jsonify(ok=False, need_agree=True,
                           error='公開する前に、決まりごとを読んでください'), 403
        # 個人情報らしきものがないか見る
        blob = ' '.join([title, d.get('summary') or '', d.get('code') or '',
                         d.get('html') or '', d.get('js') or ''])
        ng = check_content(blob)
        if ng and not d.get('confirmed'):
            return jsonify(ok=False, warn=ng, need_confirm=True,
                error='個人情報かもしれないものが見つかりました: ' + '、'.join(ng)), 400

    tier = my_tier(u)
    limit = LIMITS[tier]

    c = _db()
    was = None
    if aid:
        row = c.execute('SELECT member_id,status FROM mp_app WHERE id=?', (aid,)).fetchone()
        if not row or row['member_id'] != u['member_id']:
            c.close()
            return jsonify(ok=False, error='自分のアプリではありません'), 403
        was = row['status']

    # 新しく公開するときだけ数える
    if want == 'published' and was != 'published':
        if published_count(u['member_id']) >= limit:
            c.close()
            return jsonify(ok=False, over_limit=True, limit=limit,
                error=f'公開できるのは{limit}個までです。'
                      f'どれかを下書きに戻すと、また公開できます'), 409

    vals = (title[:80], (d.get('summary') or '')[:300],
            _clip(d.get('code')), _clip(d.get('html')), _clip(d.get('css')),
            _clip(d.get('js')), _clip(d.get('sql')),
            (d.get('tags') or '')[:120], int(d.get('difficulty') or 1),
            (d.get('school') or u.get('school') or '')[:40], want)

    if aid:
        c.execute("""UPDATE mp_app SET title=?,summary=?,code=?,html=?,css=?,js=?,
                     sql=?,tags=?,difficulty=?,school=?,status=?,
                     updated_at=datetime('now','localtime') WHERE id=?""",
                  vals + (aid,))
    else:
        cur = c.execute("""INSERT INTO mp_app
            (title,summary,code,html,css,js,sql,tags,difficulty,school,status,
             member_id,forked_from) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            vals + (u['member_id'], d.get('forked_from')))
        aid = cur.lastrowid
    c.commit(); c.close()
    return jsonify(ok=True, id=aid, status=want)


@bp.route('/api/mp/app/<int:aid>/status', methods=['POST'])
def mp_app_status(aid):
    """公開⇄下書きの切り替え"""
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    d = request.get_json(silent=True) or {}
    want = 'published' if d.get('publish') else 'draft'
    c = _db()
    row = c.execute('SELECT member_id,status FROM mp_app WHERE id=?', (aid,)).fetchone()
    if not row or row['member_id'] != u['member_id']:
        c.close()
        return jsonify(ok=False, error='自分のアプリではありません'), 403
    if want == 'published' and row['status'] != 'published':
        limit = LIMITS[my_tier(u)]
        if published_count(u['member_id']) >= limit:
            c.close()
            return jsonify(ok=False, over_limit=True, limit=limit,
                error=f'公開できるのは{limit}個までです'), 409
    c.execute("UPDATE mp_app SET status=?, updated_at=datetime('now','localtime') "
              "WHERE id=?", (want, aid))
    c.commit(); c.close()
    return jsonify(ok=True, status=want)


@bp.route('/api/mp/app/<int:aid>/delete', methods=['POST'])
def mp_app_delete(aid):
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    row = c.execute('SELECT member_id FROM mp_app WHERE id=?', (aid,)).fetchone()
    if not row or row['member_id'] != u['member_id']:
        c.close()
        return jsonify(ok=False, error='自分のアプリではありません'), 403
    c.execute('DELETE FROM mp_app WHERE id=?', (aid,))
    c.execute('DELETE FROM mp_app_like WHERE app_id=?', (aid,))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/mp/app/<int:aid>/like', methods=['POST'])
def mp_app_like(aid):
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    hit = c.execute('SELECT 1 FROM mp_app_like WHERE app_id=? AND member_id=?',
                    (aid, u['member_id'])).fetchone()
    if hit:
        c.execute('DELETE FROM mp_app_like WHERE app_id=? AND member_id=?',
                  (aid, u['member_id']))
        c.execute('UPDATE mp_app SET likes=MAX(0,likes-1) WHERE id=?', (aid,))
        liked = False
    else:
        c.execute('INSERT INTO mp_app_like(app_id,member_id) VALUES(?,?)',
                  (aid, u['member_id']))
        c.execute('UPDATE mp_app SET likes=likes+1 WHERE id=?', (aid,))
        liked = True
    c.commit(); c.close()
    return jsonify(ok=True, liked=liked)


@bp.route('/api/mp/app/<int:aid>/fork', methods=['POST'])
def mp_app_fork(aid):
    """他の人のアプリを自分の下書きにコピーする。
       「見る」から「いじる」への段差をなくすため。"""
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    r = c.execute("SELECT * FROM mp_app WHERE id=? AND status='published'",
                  (aid,)).fetchone()
    if not r:
        c.close()
        return jsonify(ok=False, error='見つかりません'), 404
    cur = c.execute("""INSERT INTO mp_app
        (title,summary,code,html,css,js,sql,tags,difficulty,school,status,
         member_id,forked_from)
        VALUES(?,?,?,?,?,?,?,?,?,?,'draft',?,?)""",
        (r['title'] + ' のコピー', r['summary'], r['code'], r['html'], r['css'],
         r['js'], r['sql'], r['tags'], r['difficulty'],
         u.get('school') or '', u['member_id'], aid))
    nid = cur.lastrowid
    c.commit(); c.close()
    return jsonify(ok=True, id=nid)


# ====================================================================
# 決まりごと
# ====================================================================
RULES_VERSION = '1.0'

RULES = [
    ('お金もうけに使わないこと',
     '売り物にしたり、宣伝やお店の案内をのせるのはやめてください。'),
    ('自分や人の個人情報を書かないこと',
     '本名・住所・電話番号・くわしい学校名やクラス・写真は書かないでください。'
     '危ない目にあうことがあります。'),
    ('人を傷つけないこと',
     'わるぐちや、こわがらせる内容、いじめにつながるものはだめです。'),
    ('人の作品を自分のものにしないこと',
     'まねして作るのはOKですが、「コピーして作る」ボタンを使ってください。'
     '元の人の名前がのこります。'),
    ('わいせつ・暴力的なものはだめ',
     'みんなが見る場所です。小さい子も見ています。'),
    ('よそのサイトに勝手につながないこと',
     'プログラムから外のサイトへ通信するのは止めてあります。'),
    ('ブラウザが止まるプログラムを出さないこと',
     '終わらないくり返しは、見た人のパソコンが固まります。'),
    ('人の絵やキャラクターを勝手に使わないこと',
     'アニメやゲームのキャラクターは、権利をもっている人のものです。'),
]

# 個人情報らしきものを見つける
import re as _re
_NG = [
    (_re.compile(r'0\d{1,4}-\d{1,4}-\d{3,4}'), '電話番号らしきもの'),
    (_re.compile(r'\d{3}-?\d{4}\s*[都道府県市区町村]'), '住所らしきもの'),
    (_re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'), 'メールアドレス'),
    (_re.compile(r'\d年\d組'), 'クラス'),
]


def check_content(text):
    """個人情報らしきものがあれば知らせる。止めはしないが、警告する。"""
    found = []
    for pat, name in _NG:
        if pat.search(text or ''):
            found.append(name)
    return found


@bp.route('/api/mp/rules')
def mp_rules():
    m = me()
    agreed = False
    if m:
        c = _db()
        agreed = bool(c.execute('SELECT 1 FROM mp_agree WHERE member_id=? AND version=?',
                                (m['member_id'], RULES_VERSION)).fetchone())
        c.close()
    return jsonify(ok=True, version=RULES_VERSION, agreed=agreed,
                   rules=[{'title': t, 'body': b} for t, b in RULES])


@bp.route('/api/mp/agree', methods=['POST'])
def mp_agree():
    m = me()
    if not m:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    c.execute('INSERT OR IGNORE INTO mp_agree(member_id,version) VALUES(?,?)',
              (m['member_id'], RULES_VERSION))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/mp/report', methods=['POST'])
def mp_report():
    m = me()
    if not m:
        return jsonify(ok=False, error='ログインしてください'), 401
    if not rate_limit('mpreport:' + m['member_id'], 3):
        return jsonify(ok=False, error='通報が多すぎます'), 429
    d = request.get_json(silent=True) or {}
    if not d.get('app_id'):
        return jsonify(ok=False, error='対象がありません'), 400
    c = _db()
    c.execute('INSERT INTO mp_report(app_id,reporter,reason,body) VALUES(?,?,?,?)',
              (d.get('app_id'), m['member_id'],
               (d.get('reason') or 'other')[:40], (d.get('body') or '')[:1000]))
    c.commit(); c.close()
    return jsonify(ok=True)
