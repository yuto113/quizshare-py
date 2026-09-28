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
    # かりた ぶんは「ためた ごうけい」に 入れない。
    # じぶんで かせいだ ぶんだけが きろくに のこる。
    add_total = 0 if reason == 'loan' else n
    c.execute("""INSERT INTO oma_pt(member_id, pt, total, updated_at)
                 VALUES(?,?,?,?)
                 ON CONFLICT(member_id) DO UPDATE SET
                 pt = pt + ?, total = total + ?, updated_at = ?""",
              (member_id, n, add_total, jst_str(), n, add_total, jst_str()))
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


# ====================================================================
# PT を かりる（社員と管理者だけ）
#   ・額は 無制限
#   ・返す 期日は じぶんで きめる（さいちょう 30日）
#   ・期日から 7日の ゆうよ
#   ・ゆうよも すぎたら 強制回収（のこりを ぜんぶ 引く。マイナスOK）
#   ・かりたら 社員 ぜんいんに 知らされる
# ====================================================================
GRACE_DAYS = 7
MAX_DUE_DAYS = 30


def _is_staff(member_id):
    """社員か 管理者か"""
    c = _db()
    r = c.execute('SELECT tier, status FROM mp_member WHERE member_id=?',
                  (member_id,)).fetchone()
    c.close()
    if not r or r['status'] != 'active':
        return False
    return r['tier'] in ('staff', 'admin')


def my_loans(member_id):
    """かりて いる ものを ぜんぶ"""
    c = _db()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM oma_pt_loan WHERE member_id=? AND status<>'done' "
        "ORDER BY id DESC", (member_id,)).fetchall()]
    c.close()
    today = _today()
    for r in rows:
        r['nokori'] = r['amount'] - r['repaid']
        r['late'] = (today > r['grace_date'])
        r['due_soon'] = (today > r['due_date'] and not r['late'])
    return rows


def debt_total(member_id):
    """のこって いる 借金の ごうけい"""
    c = _db()
    r = c.execute("SELECT COALESCE(SUM(amount - repaid), 0) AS n "
                  "FROM oma_pt_loan WHERE member_id=? AND status<>'done'",
                  (member_id,)).fetchone()
    c.close()
    return r['n'] if r else 0


def borrow(member_id, amount, days, reason=''):
    """かりる"""
    if not _is_staff(member_id):
        return {'ok': False, 'why': '社員だけが かりられます'}
    try:
        amount = int(amount)
        days = int(days)
    except Exception:
        return {'ok': False, 'why': 'かずが おかしい'}
    if amount <= 0:
        return {'ok': False, 'why': 'かずを 入れて ください'}
    if days < 1 or days > MAX_DUE_DAYS:
        return {'ok': False, 'why': '期日は 1〜%d日 です' % MAX_DUE_DAYS}

    now = now_jst()
    due = (now + timedelta(days=days)).strftime('%Y-%m-%d')
    grace = (now + timedelta(days=days + GRACE_DAYS)).strftime('%Y-%m-%d')

    c = _db()
    cur = c.execute("""INSERT INTO oma_pt_loan(member_id, amount, due_date,
                 grace_date, reason, created_at)
                 VALUES(?,?,?,?,?,?)""",
              (member_id, amount, due, grace, (reason or '')[:200], jst_str()))
    lid = cur.lastrowid
    c.commit()
    c.close()

    # PT を わたす
    add_pt(member_id, 'loan', ref_id='L%s' % lid, n=amount,
           detail='かりた（%s までに 返す）' % due)

    # 社員 ぜんいんに 知らせる
    _tell_staff(member_id, amount, due)

    return {'ok': True, 'id': lid, 'due': due, 'grace': grace,
            'pt': get_pt(member_id)['pt']}


def repay(member_id, amount, loan_id=None):
    """手で かえす"""
    try:
        amount = int(amount)
    except Exception:
        return {'ok': False, 'why': 'かずが おかしい'}
    if amount <= 0:
        return {'ok': False, 'why': 'かずを 入れて ください'}

    now = get_pt(member_id)['pt']
    if now < amount:
        return {'ok': False, 'why': 'PT が たりません', 'have': now}

    c = _db()
    if loan_id:
        loans = c.execute("SELECT * FROM oma_pt_loan WHERE id=? AND member_id=? "
                          "AND status<>'done'", (loan_id, member_id)).fetchall()
    else:
        loans = c.execute("SELECT * FROM oma_pt_loan WHERE member_id=? "
                          "AND status<>'done' ORDER BY due_date",
                          (member_id,)).fetchall()
    if not loans:
        c.close()
        return {'ok': False, 'why': 'かりて いる ものが ありません'}

    left = amount
    done = []
    for r in loans:
        if left <= 0:
            break
        nokori = r['amount'] - r['repaid']
        pay = min(left, nokori)
        newrep = r['repaid'] + pay
        if newrep >= r['amount']:
            c.execute("UPDATE oma_pt_loan SET repaid=?, status='done', "
                      "closed_at=? WHERE id=?", (newrep, jst_str(), r['id']))
            done.append(r['id'])
        else:
            c.execute('UPDATE oma_pt_loan SET repaid=? WHERE id=?',
                      (newrep, r['id']))
        left -= pay
    c.commit()
    c.close()

    used = amount - left
    if used > 0:
        spend_pt(member_id, used, 'repay', detail='かえした')
    return {'ok': True, 'paid': used, 'done': done,
            'debt': debt_total(member_id), 'pt': get_pt(member_id)['pt']}


def force_collect(member_id):
    """ゆうよも すぎた ものを 強制回収。
       のこって いる ぶんを ぜんぶ 引く。PT が マイナスに なっても よい。"""
    today = _today()
    c = _db()
    rows = c.execute("SELECT * FROM oma_pt_loan WHERE member_id=? "
                     "AND status<>'done' AND grace_date < ?",
                     (member_id, today)).fetchall()
    if not rows:
        c.close()
        return None

    total = 0
    for r in rows:
        nokori = r['amount'] - r['repaid']
        total += nokori
        c.execute("UPDATE oma_pt_loan SET repaid=?, status='done', "
                  "closed_at=? WHERE id=?",
                  (r['amount'], jst_str(), r['id']))
    c.commit()

    # PT を 引く（マイナスに なっても よい）
    cur = c.execute('SELECT pt FROM oma_pt WHERE member_id=?',
                    (member_id,)).fetchone()
    have = cur['pt'] if cur else 0
    c.execute("""INSERT INTO oma_pt(member_id, pt, total, updated_at)
                 VALUES(?,?,0,?)
                 ON CONFLICT(member_id) DO UPDATE SET pt = pt - ?,
                 updated_at = ?""",
              (member_id, -total, jst_str(), total, jst_str()))
    c.execute("""INSERT INTO oma_pt_log(member_id, amount, reason, detail,
                 ref_id, created_at) VALUES(?,?,?,?,?,?)""",
              (member_id, -total, 'force',
               '期日を すぎたので 強制的に かえしました', None, jst_str()))
    c.commit()
    c.close()
    return {'collected': total, 'pt': have - total}


def check_loans(member_id):
    """ログインの ときに よぶ。期日を すぎて いたら 強制回収。"""
    if not _is_staff(member_id):
        return None
    today = _today()
    c = _db()
    # まず late に する
    c.execute("UPDATE oma_pt_loan SET status='late' WHERE member_id=? "
              "AND status='open' AND due_date < ?", (member_id, today))
    c.commit()
    c.close()
    return force_collect(member_id)


def _tell_staff(who, amount, due):
    """社員 ぜんいんに 知らせる"""
    try:
        c = _db()
        nick = c.execute('SELECT nickname FROM mp_member WHERE member_id=?',
                         (who,)).fetchone()
        name = nick['nickname'] if nick else who
        staff = c.execute("SELECT member_id FROM mp_member "
                          "WHERE tier IN ('staff','admin') AND status='active'"
                          ).fetchall()
        for s in staff:
            if s['member_id'] == who:
                continue
            c.execute("""INSERT INTO mp_notice(member_id, kind, actor, body,
                         created_at) VALUES(?,?,?,?,?)""",
                      (s['member_id'], 'loan', who,
                       '%s さんが %d PT かりました（%s までに 返す）'
                       % (name, amount, due), jst_str()))
        c.commit()
        c.close()
    except Exception as e:
        print('[loan tell]', e)


def all_loans():
    """管理ページ用。ぜんいんの かりて いる もの。"""
    c = _db()
    rows = [dict(r) for r in c.execute("""
        SELECT l.*, m.nickname,
               (l.amount - l.repaid) AS nokori
        FROM oma_pt_loan l LEFT JOIN mp_member m ON m.member_id = l.member_id
        ORDER BY (l.status='done'), l.due_date""").fetchall()]
    c.close()
    today = _today()
    for r in rows:
        r['late'] = (r['status'] != 'done' and today > r['grace_date'])
    return rows
