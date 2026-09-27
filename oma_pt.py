# -*- coding: utf-8 -*-
"""
Omame Point (OmamePT)
======================
PJ で 活動すると たまる。ゲームや コンテストで つかう。

  add_pt(member_id, reason, ref_id)   きまった かずを たす
  spend_pt(member_id, n, reason)      つかう
  get_pt(member_id)                   いま いくつ か

ずるを ふせぐ しくみ
  ・おなじ ことで 2回 もらえない（reason + ref_id で 1回きり）
  ・じぶんの 作品に じぶんで いいね → かぞえない
  ・1日に ためられる のは 50 まで（コンテストは べつ）
"""

import os, sqlite3
from datetime import datetime, timedelta, timezone

# PythonAnywhere は UTC。日本時間で かんがえる。
JST = timezone(timedelta(hours=9))


def now_jst():
    return datetime.now(JST)


def jst_str():
    return now_jst().strftime('%Y-%m-%d %H:%M:%S')

# なにを したら いくつ たまるか
EARN = {
    'publish':   (10, '作品を 公開した'),
    'like_got':  (2,  'いいねを もらった'),
    'fork_got':  (5,  'コピーされた'),
    'answer':    (3,  'しつもんに こたえた'),
    'best':      (15, 'ベストアンサーに えらばれた'),
    'wiki_new':  (5,  '図鑑を 書いた'),
    'wiki_edit': (2,  '図鑑を 直した'),
    'draw_pub':  (5,  'えを 公開した'),
    'login':     (1,  '今日も 来た'),
    'login7':    (10, '7日 つづけて 来た'),
}

# コンテストの しょうきん（1日の かぎりは かからない）
PRIZE = {
    'small':   {1: 1000, 2: 800,  3: 500},
    'mid':     {1: 2000, 2: 1600, 3: 1000},
    'big':     {1: 10000, 2: 8000, 3: 5000},
    'special': {1: 10000, 2: 8000, 3: 5000},
}
PRIZE_JOIN = 100          # 4位いかの さんかしょう

DAY_MAX = 50              # 1日に ためられる かず
NO_LIMIT = ('prize', 'join', 'admin')   # かぎりの かからない もの


def _db():
    c = sqlite3.connect(os.environ.get('SQLITE_PATH',
                                       '/home/yuto113/quizshare.db'))
    c.row_factory = sqlite3.Row
    return c


def _today():
    return now_jst().strftime('%Y-%m-%d')


def get_pt(member_id):
    """いま もっている PT と、これまでの ごうけい"""
    c = _db()
    r = c.execute('SELECT pt, total FROM oma_pt WHERE member_id=?',
                  (member_id,)).fetchone()
    c.close()
    if not r:
        return {'pt': 0, 'total': 0}
    return {'pt': r['pt'], 'total': r['total']}


def add_pt(member_id, reason, ref_id=None, n=None, detail=None, by=None):
    """PT を たす。
       reason は EARN の かぎ、または prize / join / admin。
       ref_id が あると、おなじ ものでは 2回 もらえない。
       by は「した人」。じぶんの ことなら かぞえない。"""
    if not member_id:
        return {'ok': False, 'why': 'だれか わからない'}

    # じぶんで じぶんに いいね などは かぞえない
    if by and by == member_id:
        return {'ok': False, 'why': 'じぶんの ぶんは かぞえません'}

    if n is None:
        if reason not in EARN:
            return {'ok': False, 'why': 'しらない りゆう'}
        n, label = EARN[reason]
    else:
        label = detail or reason

    c = _db()

    # おなじ ことで 2回 もらえない
    if ref_id is not None:
        hit = c.execute('SELECT 1 FROM oma_pt_log WHERE member_id=? '
                        'AND reason=? AND ref_id=?',
                        (member_id, reason, str(ref_id))).fetchone()
        if hit:
            c.close()
            return {'ok': False, 'why': 'もう もらって います'}

    # 1日の かぎり
    day = _today()
    if reason not in NO_LIMIT:
        r = c.execute('SELECT earned FROM oma_pt_day WHERE member_id=? AND day=?',
                      (member_id, day)).fetchone()
        got = r['earned'] if r else 0
        if got >= DAY_MAX:
            c.close()
            return {'ok': False, 'why': '今日は もう いっぱいです', 'capped': True}
        if got + n > DAY_MAX:
            n = DAY_MAX - got        # はみ出す ぶんは けずる

    # たす
    c.execute("""INSERT INTO oma_pt(member_id, pt, total, updated_at)
                 VALUES(?,?,?,?)
                 ON CONFLICT(member_id) DO UPDATE SET
                 pt = pt + ?, total = total + ?, updated_at = ?""",
              (member_id, n, n, jst_str(), n, n, jst_str()))
    c.execute("""INSERT INTO oma_pt_log(member_id, amount, reason, detail,
                 ref_id, created_at) VALUES(?,?,?,?,?,?)""",
              (member_id, n, reason, label,
               str(ref_id) if ref_id else None, jst_str()))
    if reason not in NO_LIMIT:
        c.execute("""INSERT INTO oma_pt_day(member_id, day, earned)
                     VALUES(?,?,?)
                     ON CONFLICT(member_id, day) DO UPDATE SET
                     earned = earned + ?""", (member_id, day, n, n))
    c.commit()
    r = c.execute('SELECT pt FROM oma_pt WHERE member_id=?',
                  (member_id,)).fetchone()
    c.close()
    return {'ok': True, 'got': n, 'label': label, 'pt': r['pt'] if r else n}


def spend_pt(member_id, n, reason, ref_id=None, detail=None):
    """PT を つかう。たりなければ しっぱい。"""
    n = int(n)
    if n <= 0:
        return {'ok': False, 'why': 'かずが おかしい'}
    c = _db()
    r = c.execute('SELECT pt FROM oma_pt WHERE member_id=?',
                  (member_id,)).fetchone()
    have = r['pt'] if r else 0
    if have < n:
        c.close()
        return {'ok': False, 'why': 'PT が たりません', 'need': n - have,
                'have': have}
    c.execute("UPDATE oma_pt SET pt = pt - ?, updated_at = ? "
              "WHERE member_id=?", (n, jst_str(), member_id))
    c.execute("""INSERT INTO oma_pt_log(member_id, amount, reason, detail,
                 ref_id, created_at) VALUES(?,?,?,?,?,?)""",
              (member_id, -n, reason, detail or reason,
               str(ref_id) if ref_id else None, jst_str()))
    c.commit()
    c.close()
    return {'ok': True, 'spent': n, 'pt': have - n}


def daily_login(member_id):
    """1日1回、来たら もらえる。7日 つづけば おまけ。"""
    day = _today()
    c = _db()
    hit = c.execute("SELECT 1 FROM oma_pt_log WHERE member_id=? "
                    "AND reason='login' AND ref_id=?",
                    (member_id, day)).fetchone()
    if hit:
        c.close()
        return None
    # 7日 つづけて 来たか
    rows = c.execute("""SELECT DISTINCT ref_id FROM oma_pt_log
                        WHERE member_id=? AND reason='login'
                        ORDER BY ref_id DESC LIMIT 7""",
                     (member_id,)).fetchall()
    c.close()
    add_pt(member_id, 'login', ref_id=day)
    days = [r['ref_id'] for r in rows]
    if len(days) >= 6:
        t = now_jst().date()
        ok = all((t - timedelta(days=i + 1)).strftime('%Y-%m-%d') in days
                 for i in range(6))
        if ok:
            add_pt(member_id, 'login7', ref_id=day)
            return 'week'
    return 'day'


def give_prize(contest_id, entry_id, member_id, scale, rank):
    """コンテストの しょうきん。1日の かぎりは かからない。"""
    if rank and rank <= 3:
        n = PRIZE.get(scale, PRIZE['small']).get(rank, PRIZE_JOIN)
        label = 'コンテスト %d位' % rank
        reason = 'prize'
    else:
        n = PRIZE_JOIN
        label = 'コンテスト さんかしょう'
        reason = 'join'
    return add_pt(member_id, reason, ref_id='%s-%s' % (contest_id, entry_id),
                  n=n, detail=label)
