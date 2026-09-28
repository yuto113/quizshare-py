# -*- coding: utf-8 -*-
"""
Omame Group Games
==================
公式ゲーム。毎月 無料1・有料2 を 出す。

  ファイルは static/games/<slug>/ に おく。
  DB（oma_game）には だいめい・ねだんなど だけ。

  もっていない 人には ファイルを わたさない。
  /oma_games/<slug>/play が 中身を 返す（かった 人だけ）。
"""

import os, re, json, shutil, sqlite3
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, abort, Response, send_from_directory)

bp_games = Blueprint('games', __name__)

GAMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'static', 'games')
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{1,40}$')
OK_EXT = ('.html', '.css', '.js', '.json', '.txt', '.md')
FILE_MAX = 500000        # 1ファイル 500KB
GAME_MAX = 3000000       # 1ゲーム ぜんぶで 3MB


def _db():
    c = sqlite3.connect(os.environ.get('SQLITE_PATH',
                                       '/home/yuto113/quizshare.db'))
    c.row_factory = sqlite3.Row
    return c


def _me():
    from bp_member import me
    return me()


def _is_admin():
    from bp_admin import _role
    try:
        return _role() == 'admin'
    except Exception:
        return False


def game_dir(slug):
    return os.path.join(GAMES_DIR, slug)


def has_game(member_id, game_id):
    """もっているか"""
    c = _db()
    r = c.execute('SELECT 1 FROM oma_game_own WHERE member_id=? AND game_id=?',
                  (member_id, game_id)).fetchone()
    c.close()
    return bool(r)


def _safe(slug, path):
    """あぶない パスを はじく"""
    if not SLUG_RE.match(slug or ''):
        return None
    p = (path or '').strip().lstrip('/')
    if not p or '..' in p or p.startswith('.'):
        return None
    if not re.match(r'^[A-Za-z0-9_\-./]{1,80}$', p):
        return None
    if not p.lower().endswith(OK_EXT):
        return None
    full = os.path.normpath(os.path.join(game_dir(slug), p))
    if not full.startswith(os.path.normpath(game_dir(slug))):
        return None
    return full


# ====================================================================
# ページ
# ====================================================================
@bp_games.route('/oma_games')
def games_top():
    if not _me():
        return redirect('/oma_pj')
    return render_template('oma/games.html', page='games', view='list')


@bp_games.route('/oma_games/<slug>')
def games_one(slug):
    if not _me():
        return redirect('/oma_pj')
    return render_template('oma/games.html', page='games', view='one',
                           slug=slug)


@bp_games.route('/oma_games/<slug>/play')
def games_play(slug):
    """じっさいに あそぶ 画面。もっている 人だけ。"""
    u = _me()
    if not u:
        return redirect('/oma_pj')
    c = _db()
    g = c.execute('SELECT * FROM oma_game WHERE slug=? AND active=1',
                  (slug,)).fetchone()
    c.close()
    if not g:
        abort(404)
    if g['price'] > 0 and not has_game(u['member_id'], g['id']):
        return redirect('/oma_games/' + slug)
    # 無料でも もっていない ことに して おく → はじめて あそんだら 記録
    if not has_game(u['member_id'], g['id']):
        c = _db()
        try:
            c.execute('INSERT INTO oma_game_own(member_id,game_id,paid) '
                      'VALUES(?,?,0)', (u['member_id'], g['id']))
            c.execute('UPDATE oma_game SET plays=plays+1 WHERE id=?', (g['id'],))
            c.commit()
        except Exception:
            pass
        c.close()
    return render_template('oma/game_play.html', game=dict(g), slug=slug)


@bp_games.route('/oma_games/<slug>/file/<path:fp>')
def games_file(slug, fp):
    """ゲームの ファイル。もっている 人だけに わたす。"""
    u = _me()
    if not u:
        abort(403)
    c = _db()
    g = c.execute('SELECT * FROM oma_game WHERE slug=? AND active=1',
                  (slug,)).fetchone()
    c.close()
    if not g:
        abort(404)
    if g['price'] > 0 and not has_game(u['member_id'], g['id']):
        abort(403)
    full = _safe(slug, fp)
    if not full or not os.path.isfile(full):
        abort(404)
    d = os.path.dirname(full)
    return send_from_directory(d, os.path.basename(full))


# ====================================================================
# API（あそぶ 人）
# ====================================================================
@bp_games.route('/api/games')
def api_games():
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT g.*, (SELECT COUNT(*) FROM oma_game_own o
                     WHERE o.game_id=g.id AND o.member_id=?) AS mine
        FROM oma_game g WHERE g.active=1
        ORDER BY g.month DESC, g.id DESC""", (u['member_id'],)).fetchall()]
    c.close()
    try:
        import oma_pt
        pt = oma_pt.get_pt(u['member_id'])['pt']
    except Exception:
        pt = 0
    return jsonify(ok=True, games=rows, pt=pt)


@bp_games.route('/api/games/<slug>')
def api_game_one(slug):
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    g = c.execute('SELECT * FROM oma_game WHERE slug=? AND active=1',
                  (slug,)).fetchone()
    if not g:
        c.close()
        return jsonify(ok=False, error='ありません'), 404
    items = [dict(r) for r in c.execute("""
        SELECT i.*, (SELECT n FROM oma_item_own o
                     WHERE o.item_id=i.id AND o.member_id=?) AS mine
        FROM oma_game_item i WHERE i.game_id=? AND i.active=1
        ORDER BY i.price""", (u['member_id'], g['id'])).fetchall()]
    c.close()
    try:
        import oma_pt
        pt = oma_pt.get_pt(u['member_id'])['pt']
    except Exception:
        pt = 0
    return jsonify(ok=True, game=dict(g), items=items, pt=pt,
                   mine=has_game(u['member_id'], g['id']))


@bp_games.route('/api/games/<slug>/buy', methods=['POST'])
def api_game_buy(slug):
    """ゲームを かう"""
    u = _me()
    if not u:
        return jsonify(ok=False, error='ログインして ください'), 401
    c = _db()
    g = c.execute('SELECT * FROM oma_game WHERE slug=? AND active=1',
                  (slug,)).fetchone()
    c.close()
    if not g:
        return jsonify(ok=False, error='ありません'), 404
    if has_game(u['member_id'], g['id']):
        return jsonify(ok=True, already=True)
    if g['price'] <= 0:
        c = _db()
        c.execute('INSERT OR IGNORE INTO oma_game_own(member_id,game_id,paid) '
                  'VALUES(?,?,0)', (u['member_id'], g['id']))
        c.commit(); c.close()
        return jsonify(ok=True, free=True)

    import oma_pt
    r = oma_pt.spend_pt(u['member_id'], g['price'], 'buy',
                        ref_id='g%s' % g['id'],
                        detail='ゲーム「%s」を かった' % g['title'])
    if not r['ok']:
        return jsonify(ok=False, error=r['why'],
                       need=r.get('need'), have=r.get('have')), 402
    c = _db()
    c.execute('INSERT OR IGNORE INTO oma_game_own(member_id,game_id,paid) '
              'VALUES(?,?,?)', (u['member_id'], g['id'], g['price']))
    c.commit(); c.close()
    return jsonify(ok=True, pt=r['pt'])


@bp_games.route('/api/games/<slug>/item/<code>/buy', methods=['POST'])
def api_item_buy(slug, code):
    """ゲームの 中の もの を かう"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    g = c.execute('SELECT id,title FROM oma_game WHERE slug=?', (slug,)).fetchone()
    if not g:
        c.close(); return jsonify(ok=False, error='ありません'), 404
    it = c.execute('SELECT * FROM oma_game_item WHERE game_id=? AND code=? '
                   'AND active=1', (g['id'], code)).fetchone()
    c.close()
    if not it:
        return jsonify(ok=False, error='その ものは ありません'), 404

    import oma_pt
    r = oma_pt.spend_pt(u['member_id'], it['price'], 'item',
                        ref_id='i%s-%s' % (it['id'], os.urandom(3).hex()),
                        detail='%s（%s）' % (it['name'], g['title']))
    if not r['ok']:
        return jsonify(ok=False, error=r['why'],
                       need=r.get('need'), have=r.get('have')), 402
    c = _db()
    c.execute("""INSERT INTO oma_item_own(member_id,item_id,n) VALUES(?,?,1)
                 ON CONFLICT(member_id,item_id) DO UPDATE SET n = n + 1""",
              (u['member_id'], it['id']))
    c.commit(); c.close()
    return jsonify(ok=True, pt=r['pt'], item=dict(it))


@bp_games.route('/api/games/<slug>/save', methods=['GET', 'POST'])
def api_game_save(slug):
    """セーブデータ。ゲームの 中から よぶ。"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    c = _db()
    g = c.execute('SELECT id FROM oma_game WHERE slug=?', (slug,)).fetchone()
    if not g:
        c.close(); return jsonify(ok=False, error='ありません'), 404
    if request.method == 'GET':
        r = c.execute('SELECT data FROM oma_game_save WHERE member_id=? '
                      'AND game_id=?', (u['member_id'], g['id'])).fetchone()
        items = [dict(x) for x in c.execute("""
            SELECT i.code, o.n FROM oma_item_own o
            JOIN oma_game_item i ON i.id = o.item_id
            WHERE o.member_id=? AND i.game_id=?""",
            (u['member_id'], g['id'])).fetchall()]
        c.close()
        try:
            data = json.loads(r['data']) if r and r['data'] else {}
        except Exception:
            data = {}
        return jsonify(ok=True, data=data, items=items)

    d = request.get_json(silent=True) or {}
    body = json.dumps(d.get('data') or {}, ensure_ascii=False)[:100000]
    c.execute("""INSERT INTO oma_game_save(member_id,game_id,data)
                 VALUES(?,?,?)
                 ON CONFLICT(member_id,game_id) DO UPDATE SET
                 data=excluded.data,
                 saved_at=datetime('now','localtime')""",
              (u['member_id'], g['id'], body))
    c.commit(); c.close()
    return jsonify(ok=True)


# ====================================================================
# API（管理者）
# ====================================================================
@bp_games.route('/api/admin/games')
def api_admin_games():
    if not _is_admin():
        return jsonify(ok=False, error='管理者のみ'), 403
    c = _db()
    rows = []
    for r in c.execute('SELECT * FROM oma_game ORDER BY id DESC').fetchall():
        d = dict(r)
        d['owners'] = c.execute('SELECT COUNT(*) AS n FROM oma_game_own '
                                'WHERE game_id=?', (r['id'],)).fetchone()['n']
        d['items'] = c.execute('SELECT COUNT(*) AS n FROM oma_game_item '
                               'WHERE game_id=?', (r['id'],)).fetchone()['n']
        gd = game_dir(r['slug'])
        d['files'] = len(os.listdir(gd)) if os.path.isdir(gd) else 0
        rows.append(d)
    c.close()
    return jsonify(ok=True, games=rows)


@bp_games.route('/api/admin/game', methods=['POST'])
def api_admin_game_new():
    if not _is_admin():
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    slug = (d.get('slug') or '').strip().lower()
    if not SLUG_RE.match(slug):
        return jsonify(ok=False,
            error='URLの名前は 小文字の英数字と - _ で 2〜41文字'), 400
    title = (d.get('title') or '').strip()
    if not title:
        return jsonify(ok=False, error='だいめいを 入れて ください'), 400

    c = _db()
    old = c.execute('SELECT id FROM oma_game WHERE slug=?', (slug,)).fetchone()
    if old:
        c.execute("""UPDATE oma_game SET title=?,summary=?,price=?,month=?,
                     icon=?,active=? WHERE slug=?""",
                  (title[:80], (d.get('summary') or '')[:400],
                   int(d.get('price') or 0), (d.get('month') or '')[:7],
                   (d.get('icon') or '')[:8],
                   1 if d.get('active', 1) else 0, slug))
        gid = old['id']
    else:
        cur = c.execute("""INSERT INTO oma_game(slug,title,summary,price,month,
                           icon) VALUES(?,?,?,?,?,?)""",
                        (slug, title[:80], (d.get('summary') or '')[:400],
                         int(d.get('price') or 0), (d.get('month') or '')[:7],
                         (d.get('icon') or '🎮')[:8]))
        gid = cur.lastrowid
        # ひな形を つくる
        gd = game_dir(slug)
        os.makedirs(gd, exist_ok=True)
        if not os.path.isfile(os.path.join(gd, 'index.html')):
            with open(os.path.join(gd, 'index.html'), 'w',
                      encoding='utf-8') as f:
                f.write('<!DOCTYPE html>\n<html lang="ja">\n<head>\n'
                        '<meta charset="UTF-8">\n'
                        '<meta name="viewport" content="width=device-width,'
                        'initial-scale=1">\n<title>' + title + '</title>\n'
                        '<link rel="stylesheet" href="style.css">\n'
                        '</head>\n<body>\n\n<h1>' + title + '</h1>\n\n'
                        '<script src="script.js"></script>\n'
                        '</body>\n</html>\n')
        if not os.path.isfile(os.path.join(gd, 'style.css')):
            with open(os.path.join(gd, 'style.css'), 'w',
                      encoding='utf-8') as f:
                f.write('body{font-family:sans-serif;margin:0;padding:16px}\n')
        if not os.path.isfile(os.path.join(gd, 'script.js')):
            with open(os.path.join(gd, 'script.js'), 'w',
                      encoding='utf-8') as f:
                f.write('// Omame Group Games\n'
                        '// セーブは omame.save({...}) / omame.load()\n'
                        '// もっている もの は omame.items\n\n')
    c.commit(); c.close()
    return jsonify(ok=True, id=gid, slug=slug)


@bp_games.route('/api/admin/game/<slug>/files')
def api_admin_files(slug):
    if not _is_admin():
        return jsonify(ok=False, error='管理者のみ'), 403
    gd = game_dir(slug)
    if not SLUG_RE.match(slug) or not os.path.isdir(gd):
        return jsonify(ok=True, files=[])
    out, total = [], 0
    for root, _dirs, names in os.walk(gd):
        for nm in names:
            full = os.path.join(root, nm)
            rel = os.path.relpath(full, gd).replace(os.sep, '/')
            sz = os.path.getsize(full)
            total += sz
            out.append({'path': rel, 'size': sz})
    out.sort(key=lambda x: x['path'])
    return jsonify(ok=True, files=out, total=total, max=GAME_MAX)


@bp_games.route('/api/admin/game/<slug>/file')
def api_admin_file_get(slug):
    if not _is_admin():
        return jsonify(ok=False, error='管理者のみ'), 403
    full = _safe(slug, request.args.get('path'))
    if not full or not os.path.isfile(full):
        return jsonify(ok=False, error='ありません'), 404
    try:
        with open(full, encoding='utf-8') as f:
            return jsonify(ok=True, content=f.read())
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:100]), 500


@bp_games.route('/api/admin/game/<slug>/file', methods=['POST'])
def api_admin_file_put(slug):
    if not _is_admin():
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    full = _safe(slug, d.get('path'))
    if not full:
        return jsonify(ok=False, error='つかえない 名前です'), 400
    body = d.get('content') or ''
    if len(body.encode('utf-8')) > FILE_MAX:
        return jsonify(ok=False, error='大きすぎます（500KBまで）'), 400
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as f:
        f.write(body)
    return jsonify(ok=True)


@bp_games.route('/api/admin/game/<slug>/file', methods=['DELETE'])
def api_admin_file_del(slug):
    if not _is_admin():
        return jsonify(ok=False, error='管理者のみ'), 403
    full = _safe(slug, request.args.get('path'))
    if not full or not os.path.isfile(full):
        return jsonify(ok=False, error='ありません'), 404
    os.remove(full)
    return jsonify(ok=True)


@bp_games.route('/api/admin/game/<slug>/item', methods=['POST'])
def api_admin_item(slug):
    """ゲームの 中で 売る もの"""
    if not _is_admin():
        return jsonify(ok=False, error='管理者のみ'), 403
    d = request.get_json(silent=True) or {}
    c = _db()
    g = c.execute('SELECT id FROM oma_game WHERE slug=?', (slug,)).fetchone()
    if not g:
        c.close(); return jsonify(ok=False, error='ありません'), 404
    code = (d.get('code') or '').strip()
    if not re.match(r'^[a-z0-9_]{1,30}$', code):
        return jsonify(ok=False, error='コードは 小文字の英数字と _'), 400
    c.execute("""INSERT INTO oma_game_item(game_id,code,name,summary,price)
                 VALUES(?,?,?,?,?)
                 ON CONFLICT(game_id,code) DO UPDATE SET
                 name=excluded.name, summary=excluded.summary,
                 price=excluded.price""",
              (g['id'], code, (d.get('name') or code)[:60],
               (d.get('summary') or '')[:200], int(d.get('price') or 0)))
    c.commit(); c.close()
    return jsonify(ok=True)
