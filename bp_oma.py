# -*- coding: utf-8 -*-
"""
Omame PJ  ─ URL の わりふりと、あたらしい API
=============================================
bp_member.py に つづけて 読みこむ。

URL の かたち
  /oma_pj                                入口（ログインして いない 人）
  /oma_pj/login                          3つから えらぶ
  /oma_pj/home                           プロジェクトを えらぶ
  /oma_pj/rules /me /notices
  /oma_pj/page/<slug>                    公開ページ
  /oma_pj/<proj>                         その プロジェクト
  /oma_pj/<proj>/search                  さがす
  /oma_pj/<proj>/<ownerID>               → /every_watch へ とばす
  /oma_pj/<proj>/<ownerID>/every_watch   その人の 公開ぶん
  /oma_pj/<proj>/<ownerID>/make          つくる
  /oma_pj/<proj>/<ownerID>/watch/<colID> 1つ 見る
"""

import os, json, sqlite3, secrets, re
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, abort)

bp_oma = Blueprint('oma', __name__)

PROJS = ('prog_code', 'prog_block', 'draw', 'talk',
         'wiki', 'shelf', 'qa', 'poll')

from datetime import datetime, timedelta, timezone

# 公式ゲームを 一般に 公開する 日時（日本時間）
GAMES_OPEN = datetime(2026, 10, 1, 0, 0, tzinfo=timezone(timedelta(hours=9)))


def games_open():
    """いま ゲームを 見せて よいか。
       社員・管理者は いつでも。一般は 10/1 00:00 から。"""
    u = _me()
    if u:
        c = _db()
        r = c.execute('SELECT tier FROM mp_member WHERE member_id=?',
                      (u['member_id'],)).fetchone()
        c.close()
        if r and r['tier'] in ('staff', 'admin'):
            return True
    now = datetime.now(timezone(timedelta(hours=9)))
    return now >= GAMES_OPEN


PROJ_NAME = {
    'prog_code':  '⌨️ コードで つくる',
    'prog_block': '🧩 ブロックで つくる',
    'draw':       '🎨 おえかき',
    'talk':       '💬 つぶやき',
    'wiki':       '📖 みんなの図鑑',
    'shelf':      '🗂 たな',
    'qa':         '❓ しつもん',
    'poll':       '🗳 とうひょう',
}

# コレクションが どの テーブルに 入って いるか
PROJ_TABLE = {
    'prog_code':  'mp_app',
    'prog_block': 'mp_app',
    'draw':       'mp_draw',
    'talk':       'mp_post',
    'wiki':       'mp_wiki',
    'shelf':      'mp_shelf',
    'qa':         'mp_qa',
    'poll':       'mp_poll',
}


def _db():
    c = sqlite3.connect(os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db'))
    c.row_factory = sqlite3.Row
    return c


def _me():
    """bp_member の me() を つかう"""
    from bp_member import me
    return me()


def _need_login():
    """ログインして いなければ 入口へ とばす"""
    return None if _me() else redirect('/oma_pj')


def new_col_id():
    """コレクションの ランダムID（6文字）"""
    return secrets.token_hex(3)


def owner_to_member(oid):
    # "me" は じぶんの こと
    if oid == 'me':
        u = _me()
        return u['member_id'] if u else None
    c = _db()
    r = c.execute('SELECT member_id FROM mp_member WHERE owner_id=?', (oid,)).fetchone()
    c.close()
    return r['member_id'] if r else None


def my_owner_id():
    """じぶんの 所有者ID。なければ その場で つくる。"""
    u = _me()
    if not u:
        return None
    oid = member_to_owner(u['member_id'])
    if oid:
        return oid
    c = _db()
    while True:
        oid = secrets.token_hex(4)
        if not c.execute('SELECT 1 FROM mp_member WHERE owner_id=?',
                         (oid,)).fetchone():
            break
    c.execute('UPDATE mp_member SET owner_id=? WHERE member_id=?',
              (oid, u['member_id']))
    c.commit(); c.close()
    return oid


def member_to_owner(mid):
    c = _db()
    r = c.execute('SELECT owner_id FROM mp_member WHERE member_id=?', (mid,)).fetchone()
    c.close()
    return r['owner_id'] if r else None


# ====================================================================
# ページ
# ====================================================================
@bp_oma.route('/oma_pj')
def oma_index():
    """入口。もう 入って いれば ホームへ。"""
    if _me():
        return redirect('/oma_pj/home')
    return render_template('oma/index.html')


@bp_oma.route('/oma_pj/login')
def oma_login():
    return render_template('oma/login.html')


@bp_oma.route('/oma_pj/home')
def oma_home():
    r = _need_login()
    if r:
        return r
    return render_template('oma/home.html', page='home')


@bp_oma.route('/oma_pj/rules')
def oma_rules():
    """きまりは だれでも 見られる。"""
    return render_template('oma/rules.html', page='rules',
                           first=request.args.get('first'),
                           guest=(not _me()))


@bp_oma.route('/oma_pj/me')
def oma_me():
    r = _need_login()
    if r:
        return r
    return render_template('oma/me.html', page='me')


@bp_oma.route('/oma_pj/notices')
def oma_notices():
    r = _need_login()
    if r:
        return r
    return render_template('oma/notices.html', page='notices')


@bp_oma.route('/oma_pj/page/<slug>')
def oma_page(slug):
    """公開ページ。会員じゃ なくても 見られる。"""
    from bp_member import page_public
    return page_public(slug)


# ---- プロジェクトごと ----
@bp_oma.route('/oma_pj/<proj>/ex')
def oma_ex(proj):
    """体験版。ログインなしで さわれる。ほぞんは できない。"""
    if proj not in ('prog_code', 'prog_block'):
        abort(404)
    # もう 入って いる 人には /ex は いらない
    if _me():
        return redirect('/oma_pj/%s/me/make' % proj)
    return render_template('oma/' + proj + '.html', page=proj,
                           proj=proj, view='make', ex=True)


@bp_oma.route('/oma_pj/<proj>')
def oma_proj(proj):
    if proj not in PROJS:
        abort(404)
    r = _need_login()
    if r:
        return r
    return render_template('oma/' + proj + '.html', page=proj,
                           proj=proj, view='list')


@bp_oma.route('/oma_pj/<proj>/search')
def oma_search(proj):
    if proj not in PROJS:
        abort(404)
    r = _need_login()
    if r:
        return r
    return render_template('oma/' + proj + '.html', page=proj,
                           proj=proj, view='search',
                           q=request.args.get('q', ''))


@bp_oma.route('/oma_pj/<proj>/<owner_id>')
def oma_owner(proj, owner_id):
    """所有者IDまで → 公開ぶんの 一覧へ とばす"""
    if proj not in PROJS:
        abort(404)
    return redirect('/oma_pj/%s/%s/every_watch' % (proj, owner_id))


@bp_oma.route('/oma_pj/<proj>/<owner_id>/every_watch')
def oma_every(proj, owner_id):
    if proj not in PROJS:
        abort(404)
    r = _need_login()
    if r:
        return r
    mid = owner_to_member(owner_id)
    if not mid:
        abort(404)
    u = _me()
    return render_template('oma/' + proj + '.html', page=proj, proj=proj,
                           view='every_watch', owner_id=owner_id,
                           is_mine=(u and u['member_id'] == mid))


@bp_oma.route('/oma_pj/<proj>/<owner_id>/make')
def oma_make(proj, owner_id):
    if proj not in PROJS:
        abort(404)
    r = _need_login()
    if r:
        return r
    my = my_owner_id()
    # "me" は そのまま つかえる。よその 人の make には 行けない。
    if owner_id != 'me' and owner_id != my:
        return redirect('/oma_pj/%s/me/make' % proj)
    return render_template('oma/' + proj + '.html', page=proj, proj=proj,
                           view='make', owner_id=owner_id, is_mine=True)


@bp_oma.route('/oma_pj/<proj>/<owner_id>/make/<col_id>')
def oma_edit(proj, owner_id, col_id):
    if proj not in PROJS:
        abort(404)
    r = _need_login()
    if r:
        return r
    my = my_owner_id()
    if owner_id != 'me' and owner_id != my:
        abort(403)
    return render_template('oma/' + proj + '.html', page=proj, proj=proj,
                           view='make', owner_id=owner_id, col_id=col_id,
                           is_mine=True)


@bp_oma.route('/oma_pj/<proj>/<owner_id>/watch/<col_id>')
def oma_watch(proj, owner_id, col_id):
    if proj not in PROJS:
        abort(404)
    r = _need_login()
    if r:
        return r
    mid = owner_to_member(owner_id)
    if not mid:
        abort(404)
    u = _me()
    return render_template('oma/' + proj + '.html', page=proj, proj=proj,
                           view='watch', owner_id=owner_id, col_id=col_id,
                           is_mine=(u and u['member_id'] == mid))


# ---- ふるい URL は とばす（2027/01/01 まで） ----
@bp_oma.route('/member')
@bp_oma.route('/member/')
def old_member():
    return redirect('/oma_pj/home')


@bp_oma.route('/member/<path:rest>')
def old_member_sub(rest):
    # /member/program/yuto/5 → できる かぎり 近い ところへ
    parts = rest.split('/')
    if parts[0] == 'mine':
        return redirect('/oma_pj/home')
    if parts[0] == 'rules':
        return redirect('/oma_pj/rules')
    if parts[0] == 'favs':
        return redirect('/oma_pj/home')
    if parts[0] == 'notices':
        return redirect('/oma_pj/notices')
    if parts[0] == 'block':
        return redirect('/oma_pj/prog_block')
    if parts[0] == 'apps':
        return redirect('/oma_pj/prog_code')
    if parts[0] == 'program' and len(parts) >= 3:
        oid = member_to_owner(parts[1])
        if oid:
            c = _db()
            r = c.execute('SELECT col_id FROM mp_app WHERE id=?',
                          (parts[2],)).fetchone()
            c.close()
            if r and r['col_id']:
                return redirect('/oma_pj/prog_code/%s/watch/%s' % (oid, r['col_id']))
            return redirect('/oma_pj/prog_code/%s/every_watch' % oid)
    return redirect('/oma_pj/home')


# ====================================================================
# API
# ====================================================================
@bp_oma.route('/api/mp/stats_public')
def api_stats_public():
    """入口に 出す かず。ログイン いらない。"""
    c = _db()
    try:
        r = c.execute("SELECT COUNT(*) AS a, COUNT(DISTINCT member_id) AS p "
                      "FROM mp_app WHERE status='published'").fetchone()
        d = c.execute("SELECT COUNT(*) AS n FROM mp_draw WHERE public=1 "
                      "AND hidden=0").fetchone()
        c.close()
        return jsonify(ok=True, apps=(r['a'] or 0) + (d['n'] or 0),
                       people=r['p'] or 0)
    except Exception:
        c.close()
        return jsonify(ok=True, apps=0, people=0)


@bp_oma.route('/api/mp/home')
def api_home():
    """ホームに 出す いろいろ"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    mid = u['member_id']
    c = _db()

    def one(sql, args=()):
        try:
            r = c.execute(sql, args).fetchone()
            return list(r)[0] if r else 0
        except Exception:
            return 0

    counts = {
        'prog_code':  one("SELECT COUNT(*) FROM mp_app WHERE status='published'"),
        'prog_block': one("SELECT COUNT(*) FROM mp_app WHERE status='published' "
                          "AND tags LIKE '%ブロック%'"),
        'draw':       one("SELECT COUNT(*) FROM mp_draw WHERE public=1 AND hidden=0"),
        'talk':       one("SELECT COUNT(*) FROM mp_post WHERE hidden=0"),
        'wiki':       one("SELECT COUNT(*) FROM mp_wiki"),
        'shelf':      one("SELECT COUNT(*) FROM mp_shelf"),
        'qa':         one("SELECT COUNT(*) FROM mp_qa WHERE hidden=0"),
        'poll':       one("SELECT COUNT(*) FROM mp_poll"),
    }
    apps = counts['prog_code'] + counts['draw']
    people = one("SELECT COUNT(DISTINCT member_id) FROM mp_app "
                 "WHERE status='published'")
    mine = one("SELECT COUNT(*) FROM mp_app WHERE member_id=? "
               "AND status='published'", (mid,))

    recent = []
    try:
        for r in c.execute("""
            SELECT a.id, a.col_id, a.title, a.created_at, a.member_id,
                   m.nickname, m.owner_id
            FROM mp_app a LEFT JOIN mp_member m ON m.member_id=a.member_id
            WHERE a.status='published' ORDER BY a.id DESC LIMIT 8""").fetchall():
            recent.append({'proj':'prog_code', 'col_id': r['col_id'] or r['id'],
                           'owner_id': r['owner_id'], 'title': r['title'],
                           'nickname': r['nickname'], 'created_at': r['created_at']})
    except Exception:
        pass
    c.close()
    return jsonify(ok=True, counts=counts, apps=apps, people=people,
                   mine=mine, recent=recent)


@bp_oma.route('/api/mp/migrate', methods=['POST'])
def api_migrate():
    """MIRAI POWER 会員 → Omame PJ 会員 に うつす。
       中身は 何も かわらない。しるしを つけて 入れるだけ。"""
    from qz_common import verify_password, rate_limit, client_ip
    ip = client_ip()
    d = request.get_json(silent=True) or {}
    mid = (d.get('member_id') or '').strip()
    if not rate_limit('omamig:' + ip, 5) or not rate_limit('omamig_id:' + mid, 5):
        return jsonify(ok=False, error='ためした かいすうが 多すぎます'), 429

    c = _db()
    r = c.execute('SELECT * FROM mp_member WHERE member_id=?', (mid,)).fetchone()
    if not r or not verify_password(d.get('password') or '', r['password_hash']):
        c.close()
        return jsonify(ok=False, error='ID か パスワードが ちがいます'), 401
    if r['status'] != 'active':
        c.close()
        return jsonify(ok=False, error='この アカウントは つかえません'), 403

    # 所有者IDが なければ 作る
    oid = r['owner_id']
    if not oid:
        while True:
            oid = secrets.token_hex(4)
            if not c.execute('SELECT 1 FROM mp_member WHERE owner_id=?',
                             (oid,)).fetchone():
                break
        c.execute('UPDATE mp_member SET owner_id=? WHERE member_id=?', (oid, mid))
    c.execute("UPDATE mp_member SET last_login=datetime('now','localtime') "
              "WHERE member_id=?", (mid,))
    c.commit()
    c.close()
    session['mp_member'] = mid
    return jsonify(ok=True, owner_id=oid, nickname=r['nickname'])


@bp_oma.route('/api/mp/proj/<proj>/list')
def api_proj_list(proj):
    """プロジェクトごとの 一覧。
       who を つけると その人の ぶんだけ。"""
    if proj not in PROJS:
        return jsonify(ok=False, error='ない プロジェクトです'), 404
    u = _me()
    if not u:
        return jsonify(ok=False, error='会員だけが 見られます'), 401

    who = request.args.get('who')          # owner_id
    q = (request.args.get('q') or '').strip()
    mine_only = request.args.get('mine')
    target = owner_to_member(who) if who else None

    c = _db()
    rows = []
    try:
        if proj in ('prog_code', 'prog_block'):
            sql = ("SELECT a.id, a.col_id, a.title, a.summary, a.tags, "
                   "a.difficulty, a.likes, a.views, a.status, a.created_at, "
                   "a.member_id, m.nickname, m.owner_id "
                   "FROM mp_app a LEFT JOIN mp_member m "
                   "ON m.member_id=a.member_id WHERE 1=1 ")
            args = []
            if target:
                sql += "AND a.member_id=? "; args.append(target)
                if target != u['member_id']:
                    sql += "AND a.status='published' "
            elif mine_only:
                sql += "AND a.member_id=? AND a.status<>'removed' "
                args.append(u['member_id'])
            else:
                sql += "AND a.status='published' "
            if q:
                sql += "AND (a.title LIKE ? OR a.summary LIKE ?) "
                args += ['%' + q + '%', '%' + q + '%']
            sql += "ORDER BY a.id DESC LIMIT 60"
            rows = [dict(r) for r in c.execute(sql, args).fetchall()]

        elif proj == 'draw':
            sql = ("SELECT d.id, d.col_id, d.title, d.mode, d.shape, d.w, d.h, "
                   "d.thumb, d.public, d.likes, d.created_at, d.member_id, "
                   "m.nickname, m.owner_id "
                   "FROM mp_draw d LEFT JOIN mp_member m "
                   "ON m.member_id=d.member_id WHERE d.hidden=0 ")
            args = []
            if target:
                sql += "AND d.member_id=? "; args.append(target)
                if target != u['member_id']:
                    sql += "AND d.public=1 "
            elif mine_only:
                sql += "AND d.member_id=? "; args.append(u['member_id'])
            else:
                sql += "AND d.public=1 "
            if q:
                sql += "AND d.title LIKE ? "; args.append('%' + q + '%')
            sql += "ORDER BY d.id DESC LIMIT 60"
            rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    except Exception as e:
        c.close()
        return jsonify(ok=False, error=str(e)[:120]), 500

    owner_info = None
    if who:
        r = c.execute("""SELECT m.owner_id, m.nickname, m.grade, m.show_grade,
                         m.avatar, m.avatar_kind, m.color, m.started_at,
                         CAST(julianday('now','localtime')
                              - julianday(COALESCE(m.started_at,m.created_at))
                              AS INT) + 1 AS days
                         FROM mp_member m WHERE m.owner_id=?""", (who,)).fetchone()
        if r:
            owner_info = dict(r)
            if not owner_info['show_grade']:
                owner_info['grade'] = ''
    c.close()
    return jsonify(ok=True, items=rows, owner=owner_info,
                   proj=proj, name=PROJ_NAME.get(proj, proj))


# ---- コレクションの API を つなぐ ----
from oma_api import _install as _oma_install
_oma_install(bp_oma, PROJS, _db, _me, owner_to_member, member_to_owner, new_col_id)


@bp_oma.route('/oma_pt')
def oma_pt_page():
    r = _need_login()
    if r:
        return r
    return render_template('oma/pt.html', page='pt')
