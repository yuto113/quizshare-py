# -*- coding: utf-8 -*-
"""管理センターの 新しい 入口  /MFSG2   （v2：日づけで 旧 /admin を 止める）

  /MFSG2/...  は  /admin/...  と おなじ ページを 出す（移行の あいだだけ）。

  旧 /admin の あつかいは  mfsg2_schedule.json（このファイルの となり）で きまる:
    {"mode": "auto", "redirect_at": "2026-11-01", "hide_at": "2026-12-01"}
    mode  auto     日づけで じどう（redirect_at から 転送、hide_at から 404）
          keep     いつまでも そのまま       redirect  いつでも 転送       hide  いつでも 404
    日づけは 日本時間。"2026-11-01" か "2026-11-01 09:00"
    ファイルが ない／こわれて いる ときは keep（あんぜんがわ）
  環境変数 MFSG2_OLD=keep|redirect|hide が あれば それが いちばん つよい。

  旧URLに 来た 回数は  mfsg2_hits.db に 日ごと・場所ごとに 数だけ きろく
  （IP・Cookie・?のあと は きろくしない）。 MFSG2_NOLOG=1 で きろくしない。

  つかいかた:  app.py:  from mfsg2 import install ; install(app)
  コマンド:    python3 mfsg2.py status | report [日数] | set redirect 2026-11-01
               | set hide 2026-12-01 | mode auto|keep|redirect|hide
"""
import os
import re
import sys
import random
import json
import sqlite3
from datetime import datetime, timedelta, timezone

NEW = '/MFSG2'
OLD = '/admin'
JST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = os.path.join(HERE, 'mfsg2_schedule.json')
HITS_DB = os.path.join(HERE, 'mfsg2_hits.db')
MODES = ('keep', 'redirect', 'hide')
KEEP_DAYS = 90

_LINK = re.compile(r'(["\'`])/admin(?=[/"\'`?#\s>]|$)')
_ABS = re.compile(r'^(https?://[^/]+)/admin(?=[/?#]|$)')


def _now():
    return datetime.now(JST)


def _parse(s):
    """'2026-11-01' / '2026-11-01 09:00' -> aware datetime(JST) / だめなら None"""
    if not s:
        return None
    s = str(s).strip()
    for f in ('%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, f).replace(tzinfo=JST)
        except ValueError:
            pass
    return None


_cache = {'mt': None, 'path': None, 'data': None}


def load_schedule(path=None):
    path = path or SCHEDULE
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache['path'] == path and _cache['mt'] == mt:
        return _cache['data']
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    _cache.update(mt=mt, path=path, data=d)
    return d


def effective_mode(now=None, path=None, forced=None):
    """いまの 旧/admin の あつかい: keep / redirect / hide"""
    f = (forced or os.environ.get('MFSG2_OLD') or '').strip().lower()
    if f in MODES:
        return f
    d = load_schedule(path)
    m = str(d.get('mode', 'auto')).strip().lower()
    if m in MODES:
        return m
    if m != 'auto':
        return 'keep'
    now = now or _now()
    h = _parse(d.get('hide_at'))
    r = _parse(d.get('redirect_at'))
    if h and now >= h:
        return 'hide'
    if r and now >= r:
        return 'redirect'
    return 'keep'


# ---------- きろく ----------
def _collapse(p):
    p = re.sub(r'/\d+(?=/|$)', '/#', p or '')
    return p[:200]


def _ref_kind(environ):
    ref = environ.get('HTTP_REFERER') or ''
    if not ref:
        return 'direct'
    m = re.match(r'^https?://([^/?#]+)([^?#]*)', ref)
    if not m:
        return 'direct'
    if m.group(1) == environ.get('HTTP_HOST'):
        return 'self:' + _collapse(m.group(2) or '/')
    return 'other:' + m.group(1)[:80]


def _open(db):
    c = sqlite3.connect(db, timeout=2)
    c.execute('CREATE TABLE IF NOT EXISTS mfsg2_old_hits('
              'day TEXT, path TEXT, ref TEXT, n INTEGER, first_at TEXT, last_at TEXT,'
              'PRIMARY KEY(day,path,ref))')
    return c


def log_hit(environ, now=None, db=None):
    """旧URLの ヒットを 1回 かぞえる。なにが あっても 例外を 出さない"""
    try:
        if os.environ.get('MFSG2_NOLOG') == '1':
            return
        now = now or _now()
        stamp = now.strftime('%Y-%m-%d %H:%M:%S')
        c = _open(db or HITS_DB)
        try:
            c.execute('INSERT INTO mfsg2_old_hits VALUES(?,?,?,1,?,?) '
                      'ON CONFLICT(day,path,ref) DO UPDATE SET n=n+1,last_at=excluded.last_at',
                      (now.strftime('%Y-%m-%d'), _collapse(environ.get('PATH_INFO', '')),
                       _ref_kind(environ), stamp, stamp))
            if random.random() < 0.005:
                old = (now - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
                c.execute('DELETE FROM mfsg2_old_hits WHERE day<?', (old,))
            c.commit()
        finally:
            c.close()
    except Exception as e:
        print('[mfsg2 log]', e)


def install(app, old_mode=None, now=None, config_path=None, db_path=None):
    """app.py の さいごの ほうで  install(app)  と よぶ。
       now: テスト用に 時計を さしかえる関数（ふだんは なし）"""
    inner = app.wsgi_app
    clock = now or _now

    def middleware(environ, start_response):
        p = environ.get('PATH_INFO', '') or ''
        if p == NEW or p.startswith(NEW + '/'):
            rest = p[len(NEW):]
            environ['PATH_INFO'] = OLD + ('' if rest == '/' else rest)
            environ['oma.mfsg2'] = True
        elif p == OLD or p.startswith(OLD + '/'):
            log_hit(environ, clock(), db_path)
            mode = effective_mode(clock(), config_path, old_mode)
            if mode == 'redirect':
                loc = NEW + p[len(OLD):]
                qs = environ.get('QUERY_STRING') or ''
                if qs:
                    loc += '?' + qs
                code = ('302 FOUND' if environ.get('REQUEST_METHOD', 'GET') in ('GET', 'HEAD')
                        else '307 TEMPORARY REDIRECT')
                start_response(code, [('Location', loc), ('Content-Length', '0')])
                return [b'']
            if mode == 'hide':
                body = ('<!doctype html><meta charset="utf-8"><title>404</title>'
                        '<h1>404 Not Found</h1>').encode('utf-8')
                start_response('404 NOT FOUND',
                               [('Content-Type', 'text/html; charset=utf-8'),
                                ('Content-Length', str(len(body)))])
                return [body]
        return inner(environ, start_response)

    app.wsgi_app = middleware

    @app.after_request
    def _mfsg2_rewrite(resp):
        try:
            from flask import request
            if not request.environ.get('oma.mfsg2'):
                return resp
            loc = resp.headers.get('Location')
            if loc:
                if loc == OLD or loc[:len(OLD) + 1] in (OLD + '/', OLD + '?', OLD + '#'):
                    resp.headers['Location'] = NEW + loc[len(OLD):]
                else:
                    m = _ABS.match(loc)
                    if m and m.group(1).split('//', 1)[1] == request.host:
                        resp.headers['Location'] = m.group(1) + NEW + loc[m.end():]
            if (resp.mimetype == 'text/html' and not resp.direct_passthrough
                    and not resp.is_streamed
                    and 'Content-Encoding' not in resp.headers):
                html = resp.get_data(as_text=True)
                new = _LINK.sub(lambda m: m.group(1) + NEW, html)
                if new != html:
                    resp.set_data(new)
        except Exception as e:
            print('[mfsg2]', e)
        return resp

    return effective_mode(clock(), config_path, old_mode)


# ---------- コマンド ----------
def _write_schedule(d, path=None):
    path = path or SCHEDULE
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write('\n')


def cmd_status(path=None):
    d = load_schedule(path)
    print('設定ファイル: %s %s' % (path or SCHEDULE, '' if d else '（ない／空 → keep）'))
    print('  mode        : %s' % d.get('mode', 'auto'))
    for k, label in (('redirect_at', '転送を はじめる日'), ('hide_at', '404 に する日')):
        v = d.get(k)
        print('  %-11s : %s %s' % (k, v or '（なし）',
                                  '' if (not v or _parse(v)) else '← 書きかたが おかしい（無視されます）'))
    env = os.environ.get('MFSG2_OLD')
    if env:
        print('  環境変数 MFSG2_OLD=%s が つよい' % env)
    print('いまの 旧 /admin: %s   （いま %s）' % (effective_mode(path=path), _now().strftime('%Y-%m-%d %H:%M')))


def cmd_report(days=14, db=None):
    db = db or HITS_DB
    if not os.path.exists(db):
        print('まだ きろくが ありません（旧 /admin に だれも 来て いない）')
        return
    c = _open(db)
    since = (_now() - timedelta(days=days)).strftime('%Y-%m-%d')
    rows = c.execute('SELECT day,path,ref,n FROM mfsg2_old_hits WHERE day>=? ORDER BY day DESC,n DESC',
                     (since,)).fetchall()
    c.close()
    print('旧 /admin への アクセス（ここ %d日）' % days)
    if not rows:
        print('  0 件 → いまなら 消しても だいじょうぶ')
        return
    by_day = {}
    for day, p, r, n in rows:
        by_day[day] = by_day.get(day, 0) + n
    for day in sorted(by_day, reverse=True):
        print('  %s  %d 回' % (day, by_day[day]))
    agg = {}
    for day, p, r, n in rows:
        agg[(p, r)] = agg.get((p, r), 0) + n
    print('\n  多い ばしょ（上位10）')
    for (p, r), n in sorted(agg.items(), key=lambda x: -x[1])[:10]:
        print('   %4d  %-34s 来かた: %s' % (n, p, r))
    left = sorted(((p, r, n) for (p, r), n in agg.items() if r.startswith('self:' + NEW)),
                  key=lambda x: -x[2])
    if left:
        print('\n  ★ 新しい /MFSG2 の ページから 旧 /admin に 飛んで いる（リンクの 直しもれ）')
        for p, r, n in left[:10]:
            print('   %4d  %s  ← %s' % (n, p, r[5:]))
    today = _now().strftime('%Y-%m-%d')
    recent = sum(n for d, n in by_day.items() if d >= (_now() - timedelta(days=3)).strftime('%Y-%m-%d'))
    print('\n  ここ3日: %d 回' % recent)
    print('  → ' + ('まだ 使われて います。しばらく 待つのが あんぜん' if recent else
                    '3日 だれも 使って いません。消して よさそう'))


def main(argv):
    a = argv[1:]
    if not a or a[0] == 'status':
        return cmd_status()
    if a[0] == 'report':
        return cmd_report(int(a[1]) if len(a) > 1 and a[1].isdigit() else 14)
    if a[0] == 'set' and len(a) == 3 and a[1] in ('redirect', 'hide'):
        if not _parse(a[2]):
            print('日づけは 2026-11-01 の ように 書いて ください'); return 1
        d = dict(load_schedule()); d.setdefault('mode', 'auto'); d[a[1] + '_at'] = a[2]
        _write_schedule(d); return cmd_status()
    if a[0] == 'mode' and len(a) == 2 and a[1] in ('auto',) + MODES:
        d = dict(load_schedule()); d['mode'] = a[1]
        _write_schedule(d); return cmd_status()
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv) or 0)
