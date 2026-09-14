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


def staff_as_member():
    """社員として管理センターにログインしていれば、そのまま会員として扱う。
       会員登録をしなくても試せるようにするため。
       AI と退職者は除く。"""
    sid = session.get('staff_id')
    if not sid or sid == 'ai_qstart':
        return None
    c = _db()
    r = c.execute("SELECT staff_id, name, status FROM qz_staff "
                  "WHERE staff_id=? AND status='active'", (sid,)).fetchone()
    c.close()
    if not r:
        return None
    # 会員としてのニックネームは mp_member に持つ。
    # 社員名は暗号化されていて会員の場では使わないので、初回に決めてもらう。
    c2 = _db()
    prof = c2.execute('SELECT * FROM mp_member WHERE member_id=?', (sid,)).fetchone()
    c2.close()
    if prof:
        d = dict(prof)
        d['is_staff'] = True
        d['need_nick'] = False
        return d
    return {'member_id': sid, 'nickname': '', 'tier': 'staff',
            'status': 'active', 'grade': '', 'school': '', 'furigana': 0,
            'is_staff': True, 'need_nick': True}


def me():
    """ログイン中の会員。会員登録がなくても、社員なら社員として扱う。"""
    mid = session.get('mp_member')
    if mid:
        c = _db()
        r = c.execute('SELECT * FROM mp_member WHERE member_id=?', (mid,)).fetchone()
        c.close()
        if r and r['status'] == 'active':
            d = dict(r); d['is_staff'] = False
            return d
    return staff_as_member()


def my_tier(m):
    """公開できる数の区分。管理者は無制限、社員と招待者は20、ほかは15。"""
    if not m:
        return 'regular'
    if session.get('staff_id'):
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
                   furigana=bool(m.get('furigana')),
                   show_grade=bool(m.get('show_grade')),
                   is_staff=bool(m.get('is_staff')),
                   need_nick=bool(m.get('need_nick')))


@bp.route('/api/mp/profile', methods=['POST'])
def mp_profile():
    m = me()
    if not m:
        return jsonify(ok=False, error='ログインしてください'), 401
    d = request.get_json(silent=True) or {}
    c = _db()
    c.execute("UPDATE mp_member SET nickname=?, grade=?, school=?, furigana=?, "
              "show_grade=? WHERE member_id=?",
              ((d.get('nickname') or m['nickname'])[:20],
               (d.get('grade') or '')[:20], (d.get('school') or '')[:40],
               1 if d.get('furigana') else 0,
               1 if d.get('show_grade') else 0, m['member_id']))
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
    if not me():
        return jsonify(ok=False, error='会員だけが見られます'), 401
    q = (request.args.get('q') or '').strip()
    tag = (request.args.get('tag') or '').strip()
    school = (request.args.get('school') or '').strip()
    sort = request.args.get('sort') or 'new'
    mine = request.args.get('mine')

    sql = ("SELECT a.id,a.member_id,a.title,a.summary,a.tags,a.difficulty,"
           "a.school,a.views,a.likes,a.created_at,a.forked_from,a.status,"
           "m.nickname,CASE WHEN m.show_grade=1 THEN m.grade ELSE '' END AS grade,"
           "m.started_at, "
           "CAST(julianday(a.created_at) - julianday(m.started_at) AS INT) + 1 AS day_no "
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
    if sort == 'first':
        # その人にとって1〜3作品目のものだけ。上手い人ばかり上に来ないように。
        sql += ("AND (SELECT COUNT(*) FROM mp_app b WHERE b.member_id=a.member_id "
                "AND b.status='published' AND b.id < a.id) < 3 ")
    sql += {'like': 'ORDER BY a.likes DESC, a.id DESC',
            'view': 'ORDER BY a.views DESC, a.id DESC',
            'first': 'ORDER BY a.id DESC'}.get(sort, 'ORDER BY a.id DESC')
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
    if not me():
        return jsonify(ok=False, error='会員だけが見られます'), 401
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
        if not agreed and not u.get('is_staff'):
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
    own = c.execute('SELECT member_id FROM mp_app WHERE id=?', (aid,)).fetchone()
    c.commit(); c.close()
    if liked and own:
        notify(own['member_id'], 'like', aid, u['member_id'], 'いいねがつきました')
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
    owner = r['member_id']
    c.commit(); c.close()
    notify(owner, 'fork', aid, u['member_id'], 'あなたの作品がコピーされました')
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


@bp.route('/member')
def page_member():
    return render_template('member.html')


@bp.route('/api/mp/setup', methods=['POST'])
def mp_setup():
    """社員が会員として使いはじめるとき、ニックネームを決める。"""
    sid = session.get('staff_id')
    if not sid or sid == 'ai_qstart':
        return jsonify(ok=False, error='社員としてログインしてください'), 401
    c = _db()
    r = c.execute("SELECT 1 FROM qz_staff WHERE staff_id=? AND status='active'",
                  (sid,)).fetchone()
    if not r:
        c.close()
        return jsonify(ok=False, error='使えるアカウントではありません'), 403
    d = request.get_json(silent=True) or {}
    nick = (d.get('nickname') or '').strip()
    if not nick or len(nick) > 20:
        c.close()
        return jsonify(ok=False, error='ニックネームは1〜20文字で'), 400
    if c.execute('SELECT 1 FROM mp_member WHERE member_id=?', (sid,)).fetchone():
        c.execute('UPDATE mp_member SET nickname=?, grade=?, school=? WHERE member_id=?',
                  (nick, (d.get('grade') or '')[:20], (d.get('school') or '')[:40], sid))
    else:
        # 社員なので、パスワードは使わない（管理センターのログインを使う）
        c.execute("""INSERT INTO mp_member
            (member_id,nickname,password_hash,tier,grade,school)
            VALUES(?,?,'-','staff',?,?)""",
            (sid, nick, (d.get('grade') or '')[:20], (d.get('school') or '')[:40]))
    c.commit(); c.close()
    return jsonify(ok=True, nickname=nick)


# ====================================================================
# ファイルとデータベース
#   作品のなかに、いくつでもファイルを作れる。
#   フォルダは path の '/' で表す（lib/utils.py）。
# ====================================================================
import re as _re2

KINDS = {'py':'py', 'html':'html', 'htm':'html', 'css':'css', 'js':'js',
         'json':'json', 'txt':'txt', 'md':'md', 'csv':'csv'}
FILE_MAX = 40                    # 1作品のファイル数
PATH_RE = _re2.compile(r'^[A-Za-z0-9_\-./]{1,60}$')
VERSIONS_KEEP = 20


def _kind_of(path):
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else 'txt'
    return KINDS.get(ext, 'txt')


def _own(aid, u):
    """自分の作品か確かめる"""
    c = _db()
    r = c.execute('SELECT member_id FROM mp_app WHERE id=?', (aid,)).fetchone()
    c.close()
    return bool(r and u and r['member_id'] == u['member_id'])


@bp.route('/api/mp/app/<int:aid>/files')
def mp_files(aid):
    """ファイル一覧。公開作品なら誰でも（会員なら）見られる。"""
    u = me()
    if not u:
        return jsonify(ok=False, error='会員だけが見られます'), 401
    c = _db()
    a = c.execute('SELECT member_id,status FROM mp_app WHERE id=?', (aid,)).fetchone()
    if not a:
        c.close(); return jsonify(ok=False, error='見つかりません'), 404
    if a['status'] != 'published' and a['member_id'] != u['member_id']:
        c.close(); return jsonify(ok=False, error='公開されていません'), 403
    files = [dict(r) for r in c.execute(
        'SELECT id,path,content,kind,is_entry,updated_at FROM mp_file '
        'WHERE app_id=? ORDER BY path', (aid,)).fetchall()]
    dbs = [dict(r) for r in c.execute(
        'SELECT id,name,schema,seed FROM mp_db WHERE app_id=?', (aid,)).fetchall()]
    c.close()
    return jsonify(ok=True, files=files, dbs=dbs,
                   is_mine=(a['member_id'] == u['member_id']))


@bp.route('/api/mp/app/<int:aid>/files', methods=['POST'])
def mp_files_save(aid):
    """ファイルをまとめて保存する。送られてこなかったものは消す。"""
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    if not _own(aid, u):
        return jsonify(ok=False, error='自分の作品ではありません'), 403
    d = request.get_json(silent=True) or {}
    files = d.get('files') or []
    if len(files) > FILE_MAX:
        return jsonify(ok=False, error=f'ファイルは{FILE_MAX}個までです'), 400

    keep, bad = [], []
    for f in files:
        p = (f.get('path') or '').strip().strip('/')
        if not p or not PATH_RE.match(p) or '..' in p:
            bad.append(p or '(空)'); continue
        body = f.get('content') or ''
        if len(body) > CODE_MAX:
            bad.append(p + '（大きすぎます）'); continue
        keep.append((p, body, _kind_of(p), 1 if f.get('is_entry') else 0))
    if bad:
        return jsonify(ok=False, error='使えない名前: ' + '、'.join(bad[:5])), 400
    if not keep:
        return jsonify(ok=False, error='ファイルがありません'), 400

    c = _db()

    # 保存の前に、今の状態を履歴に残す（⑯）
    old = [dict(r) for r in c.execute(
        'SELECT path,content,kind,is_entry FROM mp_file WHERE app_id=?', (aid,)).fetchall()]
    if old:
        c.execute('INSERT INTO mp_version(app_id,files,note) VALUES(?,?,?)',
                  (aid, json.dumps(old, ensure_ascii=False), d.get('note') or ''))
        # 古い世代を捨てる
        c.execute("""DELETE FROM mp_version WHERE app_id=? AND id NOT IN
                     (SELECT id FROM mp_version WHERE app_id=?
                      ORDER BY id DESC LIMIT ?)""", (aid, aid, VERSIONS_KEEP))

    c.execute('DELETE FROM mp_file WHERE app_id=?', (aid,))
    for p, body, kind, ent in keep:
        c.execute("""INSERT INTO mp_file(app_id,path,content,kind,is_entry)
                     VALUES(?,?,?,?,?)""", (aid, p, body, kind, ent))
    c.execute("UPDATE mp_app SET updated_at=datetime('now','localtime') WHERE id=?", (aid,))
    c.commit(); c.close()
    return jsonify(ok=True, count=len(keep))


@bp.route('/api/mp/app/<int:aid>/db', methods=['POST'])
def mp_db_save(aid):
    """作品のデータベースを保存する"""
    u = me()
    if not u or not _own(aid, u):
        return jsonify(ok=False, error='自分の作品ではありません'), 403
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or 'app.db').strip()[:40]
    c = _db()
    c.execute("""INSERT INTO mp_db(app_id,name,schema,seed)
                 VALUES(?,?,?,?) ON CONFLICT(app_id,name) DO UPDATE SET
                 schema=excluded.schema, seed=excluded.seed,
                 updated_at=datetime('now','localtime')""",
              (aid, name, (d.get('schema') or '')[:CODE_MAX],
               (d.get('seed') or '')[:CODE_MAX]))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/mp/app/<int:aid>/versions')
def mp_versions(aid):
    """保存の履歴（⑯）"""
    u = me()
    if not u or not _own(aid, u):
        return jsonify(ok=False, error='自分の作品ではありません'), 403
    c = _db()
    rows = [{'id': r['id'], 'saved_at': r['saved_at'], 'note': r['note'],
             'files': len(json.loads(r['files']))}
            for r in c.execute('SELECT * FROM mp_version WHERE app_id=? '
                               'ORDER BY id DESC', (aid,)).fetchall()]
    c.close()
    return jsonify(ok=True, versions=rows)


@bp.route('/api/mp/app/<int:aid>/restore/<int:vid>', methods=['POST'])
def mp_restore(aid, vid):
    """前のバージョンにもどす"""
    u = me()
    if not u or not _own(aid, u):
        return jsonify(ok=False, error='自分の作品ではありません'), 403
    c = _db()
    v = c.execute('SELECT files FROM mp_version WHERE id=? AND app_id=?',
                  (vid, aid)).fetchone()
    if not v:
        c.close(); return jsonify(ok=False, error='見つかりません'), 404
    # もどす前の状態も履歴に残す（もどしすぎても平気なように）
    cur = [dict(r) for r in c.execute(
        'SELECT path,content,kind,is_entry FROM mp_file WHERE app_id=?', (aid,)).fetchall()]
    if cur:
        c.execute('INSERT INTO mp_version(app_id,files,note) VALUES(?,?,?)',
                  (aid, json.dumps(cur, ensure_ascii=False), 'もどす前'))
    c.execute('DELETE FROM mp_file WHERE app_id=?', (aid,))
    for f in json.loads(v['files']):
        c.execute("""INSERT INTO mp_file(app_id,path,content,kind,is_entry)
                     VALUES(?,?,?,?,?)""",
                  (aid, f['path'], f['content'], f['kind'], f.get('is_entry', 0)))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# セットA  見本 / タグ / お気に入り / 作った日数
# ====================================================================

@bp.route('/api/mp/samples')
def mp_samples():
    """見本の一覧。白紙のエディタで止まらないように。"""
    if not me():
        return jsonify(ok=False, error='会員だけが見られます'), 401
    c = _db()
    rows = [dict(r) for r in c.execute(
        'SELECT id,title,summary,difficulty,tags FROM mp_sample '
        'WHERE active=1 ORDER BY order_no, id').fetchall()]
    c.close()
    return jsonify(ok=True, samples=rows)


@bp.route('/api/mp/sample/<int:sid>')
def mp_sample_get(sid):
    """見本の中身。エディタに読み込んで、書きかえてもらう。"""
    if not me():
        return jsonify(ok=False, error='会員だけが見られます'), 401
    c = _db()
    r = c.execute('SELECT * FROM mp_sample WHERE id=? AND active=1', (sid,)).fetchone()
    c.close()
    if not r:
        return jsonify(ok=False, error='見つかりません'), 404
    return jsonify(ok=True, title=r['title'], summary=r['summary'],
                   files=json.loads(r['files']), tags=r['tags'],
                   difficulty=r['difficulty'],
                   db={'name': 'app.db', 'schema': r['db_schema'], 'seed': r['db_seed']})


@bp.route('/api/mp/tags')
def mp_tags():
    """よく使われているタグ。押すと絞り込める。"""
    if not me():
        return jsonify(ok=False, error='会員だけが見られます'), 401
    c = _db()
    rows = c.execute("SELECT tags FROM mp_app WHERE status='published' "
                     "AND tags IS NOT NULL AND tags <> ''").fetchall()
    c.close()
    cnt = {}
    for r in rows:
        for t in (r['tags'] or '').split(','):
            t = t.strip()
            if t:
                cnt[t] = cnt.get(t, 0) + 1
    top = sorted(cnt.items(), key=lambda x: -x[1])[:20]
    return jsonify(ok=True, tags=[{'name': k, 'count': v} for k, v in top])


@bp.route('/api/mp/app/<int:aid>/fav', methods=['POST'])
def mp_fav_toggle(aid):
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    hit = c.execute('SELECT 1 FROM mp_fav WHERE member_id=? AND app_id=?',
                    (u['member_id'], aid)).fetchone()
    if hit:
        c.execute('DELETE FROM mp_fav WHERE member_id=? AND app_id=?',
                  (u['member_id'], aid))
        faved = False
    else:
        c.execute('INSERT INTO mp_fav(member_id,app_id) VALUES(?,?)',
                  (u['member_id'], aid))
        faved = True
    c.commit(); c.close()
    return jsonify(ok=True, faved=faved)


@bp.route('/api/mp/favs')
def mp_favs():
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT a.id,a.title,a.summary,a.tags,a.difficulty,a.likes,a.views,
               a.member_id,a.status,a.forked_from,m.nickname,m.grade
        FROM mp_fav v JOIN mp_app a ON a.id=v.app_id
        LEFT JOIN mp_member m ON m.member_id=a.member_id
        WHERE v.member_id=? AND a.status='published'
        ORDER BY v.created_at DESC""", (u['member_id'],)).fetchall()]
    c.close()
    return jsonify(ok=True, apps=rows)


# ====================================================================
# データベース（表を画面から作る）
#   CREATE TABLE を手で書かなくても、表と行を組み立てられるようにする。
#   実際の SQLite は、動かすときにブラウザの中で作る。
# ====================================================================
COL_TYPES = ('TEXT', 'INTEGER', 'REAL')
NAME_RE = _re2.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,30}$')


@bp.route('/api/mp/app/<int:aid>/tables')
def mp_tables(aid):
    u = me()
    if not u:
        return jsonify(ok=False, error='会員だけが見られます'), 401
    c = _db()
    a = c.execute('SELECT member_id,status FROM mp_app WHERE id=?', (aid,)).fetchone()
    if not a:
        c.close(); return jsonify(ok=False, error='見つかりません'), 404
    if a['status'] != 'published' and a['member_id'] != u['member_id']:
        c.close(); return jsonify(ok=False, error='公開されていません'), 403
    rows = []
    for r in c.execute('SELECT * FROM mp_table WHERE app_id=? ORDER BY order_no, id',
                       (aid,)).fetchall():
        rows.append({'id': r['id'], 'name': r['name'],
                     'cols': json.loads(r['cols']), 'rows': json.loads(r['rows'])})
    c.close()
    return jsonify(ok=True, tables=rows)


@bp.route('/api/mp/app/<int:aid>/tables', methods=['POST'])
def mp_tables_save(aid):
    """表をまるごと保存する"""
    u = me()
    if not u or not _own(aid, u):
        return jsonify(ok=False, error='自分の作品ではありません'), 403
    d = request.get_json(silent=True) or {}
    tabs = d.get('tables') or []
    if len(tabs) > 10:
        return jsonify(ok=False, error='表は10個までです'), 400

    clean = []
    for t in tabs:
        nm = (t.get('name') or '').strip()
        if not NAME_RE.match(nm):
            return jsonify(ok=False,
                error=f'表の名前「{nm}」は使えません。英字ではじめてください'), 400
        cols = []
        for col in (t.get('cols') or []):
            cn = (col.get('name') or '').strip()
            ct = (col.get('type') or 'TEXT').upper()
            if not NAME_RE.match(cn):
                return jsonify(ok=False,
                    error=f'列の名前「{cn}」は使えません。英字ではじめてください'), 400
            if ct not in COL_TYPES:
                ct = 'TEXT'
            cols.append({'name': cn, 'type': ct})
        if not cols:
            return jsonify(ok=False, error=f'表「{nm}」に列がありません'), 400
        rws = t.get('rows') or []
        if len(rws) > 500:
            return jsonify(ok=False, error='行は500までです'), 400
        clean.append((nm, cols, [list(r)[:len(cols)] for r in rws]))

    c = _db()
    c.execute('DELETE FROM mp_table WHERE app_id=?', (aid,))
    for i, (nm, cols, rws) in enumerate(clean):
        c.execute("""INSERT INTO mp_table(app_id,name,cols,rows,order_no)
                     VALUES(?,?,?,?,?)""",
                  (aid, nm, json.dumps(cols, ensure_ascii=False),
                   json.dumps(rws, ensure_ascii=False), i))
    c.commit(); c.close()
    return jsonify(ok=True, count=len(clean))


@bp.route('/member/program/<author>/<int:aid>')
def page_program(author, aid):
    """作品ごとのURL。
       /member/program/{作った人のID}/{作品の番号}"""
    return render_template('member.html', open_app=aid, open_author=author)


@bp.route('/member/u/<author>')
def page_author(author):
    """作った人のページ"""
    return render_template('member.html', open_author=author, open_profile=1)


@bp.route('/member/mine')
@bp.route('/member/rules')
@bp.route('/member/login')
@bp.route('/member/edit')
@bp.route('/member/edit/<int:aid>')
def page_member_sub(aid=None):
    """会員ページの各画面。中身は同じHTMLで、開く場所だけ変える。"""
    return render_template('member.html', open_edit=aid)


# ====================================================================
# セットB  通知 / 作者のページ / はじめての人 / 学年の表示
# ====================================================================

def notify(to, kind, app_id=None, actor=None, body=''):
    """通知を1件つくる。自分の操作では通知しない。"""
    if not to or to == actor:
        return
    try:
        c = _db()
        c.execute("""INSERT INTO mp_notice(member_id,kind,app_id,actor,body)
                     VALUES(?,?,?,?,?)""", (to, kind, app_id, actor, body[:200]))
        c.commit(); c.close()
    except Exception:
        pass


@bp.route('/api/mp/notices')
def mp_notices():
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT n.*, a.title, m.nickname AS actor_name
        FROM mp_notice n
        LEFT JOIN mp_app a ON a.id = n.app_id
        LEFT JOIN mp_member m ON m.member_id = n.actor
        WHERE n.member_id=? ORDER BY n.id DESC LIMIT 50""",
        (u['member_id'],)).fetchall()]
    unread = c.execute('SELECT COUNT(*) AS n FROM mp_notice '
                       'WHERE member_id=? AND is_read=0',
                       (u['member_id'],)).fetchone()
    c.close()
    return jsonify(ok=True, notices=rows, unread=(unread['n'] if unread else 0))


@bp.route('/api/mp/notices/read', methods=['POST'])
def mp_notices_read():
    u = me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    c.execute('UPDATE mp_notice SET is_read=1 WHERE member_id=?', (u['member_id'],))
    c.commit(); c.close()
    return jsonify(ok=True)


@bp.route('/api/mp/u/<author>')
def mp_author(author):
    """作った人のページ。その人の作品と、これまでの歩み。"""
    if not me():
        return jsonify(ok=False, error='会員だけが見られます'), 401
    c = _db()
    m = c.execute('SELECT * FROM mp_member WHERE member_id=?', (author,)).fetchone()
    if not m:
        c.close(); return jsonify(ok=False, error='見つかりません'), 404
    apps = [dict(r) for r in c.execute("""
        SELECT id,title,summary,tags,difficulty,likes,views,created_at,
               forked_from,status,member_id
        FROM mp_app WHERE member_id=? AND status='published'
        ORDER BY id DESC""", (author,)).fetchall()]
    st = c.execute("""SELECT COUNT(*) AS apps, COALESCE(SUM(likes),0) AS likes,
                      COALESCE(SUM(views),0) AS views
                      FROM mp_app WHERE member_id=? AND status='published'""",
                   (author,)).fetchone()
    forked = c.execute("""SELECT COUNT(*) AS n FROM mp_app f
                          JOIN mp_app a ON a.id = f.forked_from
                          WHERE a.member_id=?""", (author,)).fetchone()
    days = c.execute("SELECT CAST(julianday('now','localtime') - "
                     "julianday(COALESCE(started_at, created_at)) AS INT) + 1 AS d "
                     "FROM mp_member WHERE member_id=?", (author,)).fetchone()
    c.close()
    return jsonify(ok=True, author={
        'member_id': m['member_id'], 'nickname': m['nickname'],
        'grade': (m['grade'] if m['show_grade'] else ''),
        'started_at': m['started_at'], 'days': (days['d'] if days else 1),
        'apps': st['apps'], 'likes': st['likes'], 'views': st['views'],
        'forked': (forked['n'] if forked else 0)}, apps=apps)


@bp.route('/member/favs')
@bp.route('/member/notices')
@bp.route('/member/u/<author>')
def page_member_sub2(author=None):
    return render_template('member.html', open_author2=author)


# ====================================================================
# セットC  できたよ / ふりがな
# ====================================================================

@bp.route('/api/mp/app/<int:aid>/ran', methods=['POST'])
def mp_app_ran(aid):
    """エラーなく動いたときに呼ばれる。はじめての1回を記録して祝う。"""
    u = me()
    if not u:
        return jsonify(ok=True, first=False)
    c = _db()
    r = c.execute('SELECT member_id, first_run_at, run_count FROM mp_app WHERE id=?',
                  (aid,)).fetchone()
    if not r or r['member_id'] != u['member_id']:
        c.close(); return jsonify(ok=True, first=False)
    first = not r['first_run_at']
    if first:
        c.execute("UPDATE mp_app SET first_run_at=datetime('now','localtime'), "
                  "run_count=run_count+1 WHERE id=?", (aid,))
    else:
        c.execute('UPDATE mp_app SET run_count=run_count+1 WHERE id=?', (aid,))
    c.commit(); c.close()
    return jsonify(ok=True, first=first)


# ====================================================================
# みんなで書きこめるデータ
#   作品の表を「共有」にすると、遊んだ人が行を足せる。
#   図鑑・掲示板・記録つきゲームなどが作れるようになる。
#
#   守ること
#     ・作品ごとに完全に分ける（他の作品のデータは触れない）
#     ・1作品1000行まで（容量を守る）
#     ・1人1分5件まで（荒らし対策）
#     ・書いた人を必ず残す
#     ・消さずに隠す（まちがえて消しても戻せる）
# ====================================================================
DATA_MAX_ROWS = 1000
DATA_MAX_LEN = 500          # 1つの値の長さ


def _shared_cols(aid, tname):
    """その表が共有かどうかと、列の定義を返す"""
    c = _db()
    r = c.execute('SELECT cols, shared FROM mp_table WHERE app_id=? AND name=?',
                  (aid, tname)).fetchone()
    c.close()
    if not r:
        return None, None
    return (json.loads(r['cols']), bool(r['shared']))


@bp.route('/api/mp/app/<int:aid>/data/<tname>')
def mp_data_get(aid, tname):
    """みんなが書きこんだデータを読む"""
    if not me():
        return jsonify(ok=False, error='会員だけが使えます'), 401
    cols, shared = _shared_cols(aid, tname)
    if cols is None:
        return jsonify(ok=False, error='その表は ありません'), 404
    c = _db()
    rows = [{'id': r['id'], 'vals': json.loads(r['vals']),
             'by': r['by_member'], 'at': r['created_at']}
            for r in c.execute(
                'SELECT * FROM mp_appdata WHERE app_id=? AND tname=? AND hidden=0 '
                'ORDER BY id LIMIT ?', (aid, tname, DATA_MAX_ROWS)).fetchall()]
    c.close()
    return jsonify(ok=True, rows=rows, cols=cols, shared=shared)


@bp.route('/api/mp/app/<int:aid>/data/<tname>', methods=['POST'])
def mp_data_add(aid, tname):
    """データを1件 足す"""
    u = me()
    if not u:
        return jsonify(ok=False, error='会員だけが 書けます'), 401
    if not rate_limit('mpdata:' + u['member_id'], 5):
        return jsonify(ok=False, error='書きこみが 早すぎます。すこし 待ってください'), 429

    cols, shared = _shared_cols(aid, tname)
    if cols is None:
        return jsonify(ok=False, error='その表は ありません'), 404
    if not shared:
        return jsonify(ok=False, error='この表は みんなで 書けません'), 403

    d = request.get_json(silent=True) or {}
    vals = d.get('vals') or {}
    if not isinstance(vals, dict):
        return jsonify(ok=False, error='かたちが ちがいます'), 400

    clean = {}
    for col in cols:
        v = vals.get(col['name'])
        if v is None:
            continue
        sv = str(v)[:DATA_MAX_LEN]
        if col['type'] in ('INTEGER', 'REAL'):
            try:
                sv = float(sv) if col['type'] == 'REAL' else int(float(sv))
            except Exception:
                sv = 0
        clean[col['name']] = sv
    if not clean:
        return jsonify(ok=False, error='なにも 入っていません'), 400

    # 個人情報らしきものを止める
    ng = check_content(' '.join(str(v) for v in clean.values()))
    if ng:
        return jsonify(ok=False,
            error='個人情報かも しれないものが あります: ' + '、'.join(ng)), 400

    c = _db()
    n = c.execute('SELECT COUNT(*) AS n FROM mp_appdata WHERE app_id=?',
                  (aid,)).fetchone()
    if n and n['n'] >= DATA_MAX_ROWS:
        c.close()
        return jsonify(ok=False,
            error=f'この作品の データが いっぱいです（{DATA_MAX_ROWS}件まで）'), 409
    c.execute('INSERT INTO mp_appdata(app_id,tname,vals,by_member) VALUES(?,?,?,?)',
              (aid, tname, json.dumps(clean, ensure_ascii=False), u['member_id']))
    rid = c.lastrowid
    c.commit(); c.close()
    return jsonify(ok=True, id=rid)


@bp.route('/api/mp/app/<int:aid>/data/<tname>/<int:rid>', methods=['DELETE'])
def mp_data_del(aid, tname, rid):
    """消す。自分が書いたもの か、作品の作者だけ。消さずに隠す。"""
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    c = _db()
    r = c.execute('SELECT by_member FROM mp_appdata WHERE id=? AND app_id=?',
                  (rid, aid)).fetchone()
    a = c.execute('SELECT member_id FROM mp_app WHERE id=?', (aid,)).fetchone()
    if not r or not a:
        c.close(); return jsonify(ok=False, error='見つかりません'), 404
    if r['by_member'] != u['member_id'] and a['member_id'] != u['member_id']:
        c.close(); return jsonify(ok=False, error='消せるのは 書いた人と 作者だけです'), 403
    c.execute('UPDATE mp_appdata SET hidden=1 WHERE id=?', (rid,))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# ⑤ エラーを やさしく 訳す
#   よくあるものは辞書で答える（速い・AIを呼ばない）。
#   知らないものだけ zin 1 に頼む。
# ====================================================================
ERR_HINTS = [
    ('SyntaxError', 'invalid syntax',
     '書き方が ちがうみたいです。かっこの 閉じわすれや、'
     'コロン（:）の つけわすれが ないか 見てみてください。'),
    ('SyntaxError', 'unexpected EOF',
     'かっこか クォート（"）が 閉じられて いないようです。'),
    ('SyntaxError', 'EOL while scanning',
     '文字を かこむ " や \' が 片方だけに なっています。'),
    ('IndentationError', '',
     '行の あたまの スペースが そろって いません。'
     '同じ かたまりの 中は、おなじ 数だけ 下げてください。'),
    ('NameError', 'is not defined',
     'その 名前は まだ ありません。つづりの まちがいか、'
     '先に つくり忘れて いないか 見てみてください。'),
    ('TypeError', 'unsupported operand',
     'もじ と かず を たそうと して います。str() や int() で '
     'そろえて みてください。'),
    ('TypeError', 'takes', 'かっこの 中に わたす ものの 数が あって いません。'),
    ('ZeroDivisionError', '', '0 で わることは できません。'),
    ('IndexError', 'out of range',
     'ならびの 番号が おおきすぎます。0 から はじまることに 気を つけて。'),
    ('KeyError', '', 'その 名前の データは まだ 入って いません。'),
    ('ValueError', 'invalid literal',
     'かずに できない もじを、かずに しようと しています。'),
    ('ModuleNotFoundError', '',
     'その ぶひん（モジュール）は ここでは 使えません。'
     'ファイルを つくった 場合は、名前が あって いるか 見てみてください。'),
    ('AttributeError', '', 'その ものに、その はたらきは ありません。'),
    ('RecursionError', '',
     'じぶんを よびつづけて います。とまる ところを つくって ください。'),
]


def _hint_for(err):
    e = err or ''
    for name, key, msg in ERR_HINTS:
        if name in e and (not key or key in e):
            return msg
    return None


@bp.route('/api/mp/explain_error', methods=['POST'])
def mp_explain_error():
    """エラーを 小学生にも わかる ことばに する。"""
    u = me()
    if not u:
        return jsonify(ok=False), 401
    d = request.get_json(silent=True) or {}
    err = (d.get('error') or '').strip()[:600]
    if not err:
        return jsonify(ok=False, error='なにも ありません'), 400

    # まず辞書で答える
    hint = _hint_for(err)
    if hint:
        return jsonify(ok=True, text=hint, by='dict')

    # 知らないものは zin 1 に聞く
    if not rate_limit('mpexp:' + u['member_id'], 5):
        return jsonify(ok=True, text='エラーの 意味を しらべる のを '
                       'すこし 待ってください。', by='limit')
    try:
        from qstart_core import run_model
        prompt = (
            'あなたは 小学生に プログラミングを おしえる 先生です。\n'
            'つぎの エラーを、小学生にも わかる やさしい 日本語で、'
            '2文いないで 説明してください。むずかしい ことばは 使わないでください。\n'
            'どこを 見れば よいかも 教えてください。\n\n'
            'エラー:\n' + err)
        out = run_model('zin', prompt, max_tokens=120)
        txt = (out or '').strip()
        if txt:
            return jsonify(ok=True, text=txt[:400], by='zin')
    except Exception as e:
        print('[エラー説明]', e)

    return jsonify(ok=True, by='none',
                   text='エラーの 名前で しらべると、ヒントが 見つかります。')


# ====================================================================
# ⑩ コメント
#   小学生も見る場所なので、書きこみは いちばん 荒れやすい。
#     ・個人情報の検査を通す
#     ・1分3件まで
#     ・消さずに隠す（作者と管理者が隠せる。あとで戻せる）
#     ・通報できる
# ====================================================================
COMMENT_MAX = 400


@bp.route('/api/mp/app/<int:aid>/comments')
def mp_comments(aid):
    u = me()
    if not u:
        return jsonify(ok=False, error='会員だけが 見られます'), 401
    c = _db()
    a = c.execute('SELECT member_id FROM mp_app WHERE id=?', (aid,)).fetchone()
    rows = [dict(r) for r in c.execute("""
        SELECT co.id, co.member_id, co.body, co.created_at, m.nickname
        FROM mp_comment co LEFT JOIN mp_member m ON m.member_id = co.member_id
        WHERE co.app_id=? AND co.hidden=0 ORDER BY co.id""", (aid,)).fetchall()]
    c.close()
    return jsonify(ok=True, comments=rows,
                   is_owner=bool(a and a['member_id'] == u['member_id']))


@bp.route('/api/mp/app/<int:aid>/comments', methods=['POST'])
def mp_comment_add(aid):
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    if not rate_limit('mpcom:' + u['member_id'], 3):
        return jsonify(ok=False, error='はやすぎます。すこし 待ってください'), 429
    d = request.get_json(silent=True) or {}
    body = (d.get('body') or '').strip()
    if not body:
        return jsonify(ok=False, error='なにか 書いてください'), 400
    if len(body) > COMMENT_MAX:
        return jsonify(ok=False, error=f'{COMMENT_MAX}文字までです'), 400

    ng = check_content(body)
    if ng:
        return jsonify(ok=False,
            error='個人情報かも しれないものが あります: ' + '、'.join(ng)), 400

    c = _db()
    a = c.execute('SELECT member_id, title FROM mp_app WHERE id=?', (aid,)).fetchone()
    if not a:
        c.close(); return jsonify(ok=False, error='見つかりません'), 404
    c.execute('INSERT INTO mp_comment(app_id,member_id,body) VALUES(?,?,?)',
              (aid, u['member_id'], body))
    c.commit(); c.close()
    notify(a['member_id'], 'comment', aid, u['member_id'], 'コメントが つきました')
    return jsonify(ok=True)


@bp.route('/api/mp/comment/<int:cid>/hide', methods=['POST'])
def mp_comment_hide(cid):
    """消さずに隠す。書いた人・作者・管理者だけ。"""
    u = me()
    if not u:
        return jsonify(ok=False, error='ログインしてください'), 401
    d = request.get_json(silent=True) or {}
    c = _db()
    r = c.execute("""SELECT co.member_id AS writer, a.member_id AS owner
                     FROM mp_comment co JOIN mp_app a ON a.id = co.app_id
                     WHERE co.id=?""", (cid,)).fetchone()
    if not r:
        c.close(); return jsonify(ok=False, error='見つかりません'), 404
    if u['member_id'] not in (r['writer'], r['owner']) and my_tier(u) != 'admin':
        c.close(); return jsonify(ok=False, error='けせるのは 書いた人と 作者だけです'), 403
    c.execute('UPDATE mp_comment SET hidden=1, hidden_by=?, hidden_why=? WHERE id=?',
              (u['member_id'], (d.get('why') or '')[:200], cid))
    c.commit(); c.close()
    return jsonify(ok=True)
