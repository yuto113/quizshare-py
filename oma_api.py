# -*- coding: utf-8 -*-
"""
Omame PJ ─ コレクションの API
==============================
bp_oma.py の うしろに くっつける。

  GET  /api/mp/col/<proj>/<owner_id>/<col_id>   1つ 読む
  POST /api/mp/col/<proj>/save                  ほぞん
  POST /api/mp/col/<proj>/<col_id>/like         いいね
  POST /api/mp/col/<proj>/<col_id>/fork         コピーして つくる
"""

import os, json, sqlite3, secrets, re
from flask import request, jsonify, session


def _install(bp_oma, PROJS, _db, _me, owner_to_member, member_to_owner,
             new_col_id):

    # ----------------------------------------------------------------
    @bp_oma.route('/api/mp/col/<proj>/<owner_id>/<col_id>')
    def api_col_get(proj, owner_id, col_id):
        if proj not in PROJS:
            return jsonify(ok=False, error='ない プロジェクト'), 404
        u = _me()
        if not u:
            return jsonify(ok=False, error='会員だけ'), 401
        mid = owner_to_member(owner_id)
        if not mid:
            return jsonify(ok=False, error='その人は いません'), 404
        mine = (mid == u['member_id'])

        c = _db()
        if proj in ('prog_code', 'prog_block'):
            r = c.execute("""SELECT a.*, m.nickname, m.owner_id
                             FROM mp_app a LEFT JOIN mp_member m
                             ON m.member_id=a.member_id
                             WHERE a.col_id=? AND a.member_id=?""",
                          (col_id, mid)).fetchone()
            if not r:
                c.close(); return jsonify(ok=False, error='見つかりません'), 404
            if r['status'] != 'published' and not mine:
                c.close(); return jsonify(ok=False, error='公開されて いません'), 403
            if not mine:
                c.execute('UPDATE mp_app SET views=views+1 WHERE id=?', (r['id'],))
            files = [dict(x) for x in c.execute(
                'SELECT path,content,kind,is_entry FROM mp_file '
                'WHERE app_id=? ORDER BY path', (r['id'],)).fetchall()]
            dbs = c.execute('SELECT name,schema,seed FROM mp_db WHERE app_id=?',
                            (r['id'],)).fetchone()
            c.commit(); c.close()
            return jsonify(ok=True, item=dict(r), files=files,
                           db=(dict(dbs) if dbs else None), is_mine=mine)

        if proj == 'draw':
            r = c.execute("""SELECT d.*, m.nickname, m.owner_id
                             FROM mp_draw d LEFT JOIN mp_member m
                             ON m.member_id=d.member_id
                             WHERE d.col_id=? AND d.member_id=?""",
                          (col_id, mid)).fetchone()
            c.close()
            if not r:
                return jsonify(ok=False, error='見つかりません'), 404
            if not r['public'] and not mine:
                return jsonify(ok=False, error='公開されて いません'), 403
            return jsonify(ok=True, item=dict(r), is_mine=mine)

        c.close()
        return jsonify(ok=False, error='まだ つくって いません'), 501

    # ----------------------------------------------------------------
    @bp_oma.route('/api/mp/col/<proj>/save', methods=['POST'])
    def api_col_save(proj):
        if proj not in PROJS:
            return jsonify(ok=False, error='ない プロジェクト'), 404
        u = _me()
        if not u:
            return jsonify(ok=False, error='ログインして ください'), 401

        from bp_member import (check_content, my_tier, PUBLISH_LIMITS,
                               FILE_MAX, CODE_MAX)
        d = request.get_json(silent=True) or {}
        col_id = d.get('col_id')
        title = (d.get('title') or '').strip()
        if not title:
            return jsonify(ok=False, error='だいめいを 入れて ください'), 400

        pub = bool(d.get('publish'))
        c = _db()

        # 公開する ときは きまりへの 同意と かずの かくにん
        if pub:
            ag = c.execute('SELECT agreed_at FROM mp_member WHERE member_id=?',
                           (u['member_id'],)).fetchone()
            if not ag or not ag['agreed_at']:
                c.close()
                return jsonify(ok=False, need_agree=True,
                    error='さきに「きまり」を 読んで ください'), 403
            tier = my_tier(u)
            lim = PUBLISH_LIMITS.get(tier, 15)
            n = c.execute("SELECT COUNT(*) AS n FROM mp_app WHERE member_id=? "
                          "AND status='published' AND col_id<>?",
                          (u['member_id'], col_id or '')).fetchone()
            if n and n['n'] >= lim:
                c.close()
                return jsonify(ok=False, over_limit=True,
                    error='公開できるのは %d こ までです。'
                          'どれかを 下書きに もどすと また 公開 できます' % lim), 409

        ng = check_content(title + ' ' + (d.get('summary') or ''))
        if ng:
            c.close()
            return jsonify(ok=False,
                error='個人情報かも しれない ものが あります: ' + '、'.join(ng)), 400

        files = d.get('files') or []
        if len(files) > FILE_MAX:
            c.close()
            return jsonify(ok=False, error='ファイルは %d こ までです' % FILE_MAX), 400
        for f in files:
            if len(f.get('content') or '') > CODE_MAX:
                c.close()
                return jsonify(ok=False,
                    error='%s が 大きすぎます' % f.get('path')), 400

        # あたらしく つくる か、なおす か
        if col_id:
            r = c.execute('SELECT id FROM mp_app WHERE col_id=? AND member_id=?',
                          (col_id, u['member_id'])).fetchone()
            if not r:
                c.close(); return jsonify(ok=False, error='じぶんの ものでは ありません'), 403
            aid = r['id']
            c.execute("""UPDATE mp_app SET title=?,summary=?,tags=?,difficulty=?,
                         status=?, updated_at=datetime('now','localtime')
                         WHERE id=?""",
                      (title[:80], (d.get('summary') or '')[:300],
                       (d.get('tags') or '')[:120], int(d.get('difficulty') or 1),
                       'published' if pub else 'draft', aid))
        else:
            while True:
                col_id = new_col_id()
                if not c.execute('SELECT 1 FROM mp_app WHERE col_id=?',
                                 (col_id,)).fetchone():
                    break
            cur = c.execute("""INSERT INTO mp_app(member_id,title,summary,tags,
                               difficulty,status,col_id)
                               VALUES(?,?,?,?,?,?,?)""",
                            (u['member_id'], title[:80],
                             (d.get('summary') or '')[:300],
                             (d.get('tags') or '')[:120],
                             int(d.get('difficulty') or 1),
                             'published' if pub else 'draft', col_id))
            aid = cur.lastrowid

        # ファイル
        c.execute('DELETE FROM mp_file WHERE app_id=?', (aid,))
        for f in files:
            p = (f.get('path') or '').strip()
            if not p or '..' in p or not re.match(r'^[A-Za-z0-9_\-./]{1,60}$', p):
                continue
            c.execute("""INSERT INTO mp_file(app_id,path,content,kind,is_entry)
                         VALUES(?,?,?,?,?)""",
                      (aid, p, f.get('content') or '', f.get('kind') or 'txt',
                       1 if f.get('is_entry') else 0))

        # データベース
        db = d.get('db') or {}
        if db.get('schema') or db.get('seed'):
            c.execute('DELETE FROM mp_db WHERE app_id=?', (aid,))
            c.execute("""INSERT INTO mp_db(app_id,name,schema,seed)
                         VALUES(?,?,?,?)""",
                      (aid, db.get('name') or 'app.db',
                       (db.get('schema') or '')[:20000],
                       (db.get('seed') or '')[:20000]))

        c.commit(); c.close()
        return jsonify(ok=True, col_id=col_id,
                       owner_id=member_to_owner(u['member_id']))

    # ----------------------------------------------------------------
    @bp_oma.route('/api/mp/col/<proj>/<col_id>/like', methods=['POST'])
    def api_col_like(proj, col_id):
        u = _me()
        if not u:
            return jsonify(ok=False), 401
        c = _db()
        if proj in ('prog_code', 'prog_block'):
            r = c.execute('SELECT id, member_id FROM mp_app WHERE col_id=?',
                          (col_id,)).fetchone()
            if not r:
                c.close(); return jsonify(ok=False, error='見つかりません'), 404
            hit = c.execute('SELECT 1 FROM mp_app_like WHERE app_id=? '
                            'AND member_id=?', (r['id'], u['member_id'])).fetchone()
            if hit:
                c.execute('DELETE FROM mp_app_like WHERE app_id=? AND member_id=?',
                          (r['id'], u['member_id']))
                c.execute('UPDATE mp_app SET likes=MAX(0,likes-1) WHERE id=?',
                          (r['id'],))
                liked = False
            else:
                c.execute('INSERT INTO mp_app_like(app_id,member_id) VALUES(?,?)',
                          (r['id'], u['member_id']))
                c.execute('UPDATE mp_app SET likes=likes+1 WHERE id=?', (r['id'],))
                liked = True
            owner = r['member_id']
            c.commit(); c.close()
            if liked:
                from bp_member import notify
                notify(owner, 'like', r['id'], u['member_id'], 'いいねが つきました')
            return jsonify(ok=True, liked=liked)
        c.close()
        return jsonify(ok=False, error='まだ'), 501

    # ----------------------------------------------------------------
    @bp_oma.route('/api/mp/col/<proj>/<col_id>/fork', methods=['POST'])
    def api_col_fork(proj, col_id):
        u = _me()
        if not u:
            return jsonify(ok=False, error='ログインして ください'), 401
        if proj not in ('prog_code', 'prog_block'):
            return jsonify(ok=False, error='この プロジェクトでは できません'), 400

        c = _db()
        r = c.execute("SELECT * FROM mp_app WHERE col_id=? AND status='published'",
                      (col_id,)).fetchone()
        if not r:
            c.close(); return jsonify(ok=False, error='見つかりません'), 404

        while True:
            nid = new_col_id()
            if not c.execute('SELECT 1 FROM mp_app WHERE col_id=?',
                             (nid,)).fetchone():
                break
        cur = c.execute("""INSERT INTO mp_app(member_id,title,summary,tags,
                           difficulty,status,forked_from,col_id)
                           VALUES(?,?,?,?,?,'draft',?,?)""",
                        (u['member_id'], (r['title'] + ' のコピー')[:80],
                         r['summary'], r['tags'], r['difficulty'],
                         r['id'], nid))
        aid = cur.lastrowid
        for f in c.execute('SELECT path,content,kind,is_entry FROM mp_file '
                           'WHERE app_id=?', (r['id'],)).fetchall():
            c.execute("""INSERT INTO mp_file(app_id,path,content,kind,is_entry)
                         VALUES(?,?,?,?,?)""",
                      (aid, f['path'], f['content'], f['kind'], f['is_entry']))
        db = c.execute('SELECT name,schema,seed FROM mp_db WHERE app_id=?',
                       (r['id'],)).fetchone()
        if db:
            c.execute('INSERT INTO mp_db(app_id,name,schema,seed) VALUES(?,?,?,?)',
                      (aid, db['name'], db['schema'], db['seed']))
        owner = r['member_id']
        c.commit(); c.close()
        from bp_member import notify
        notify(owner, 'fork', r['id'], u['member_id'],
               'あなたの 作品が コピーされました')
        return jsonify(ok=True, col_id=nid)
