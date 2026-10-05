# -*- coding: utf-8 -*-
"""公式アカウント 追加機能
   ① はじめの メッセージ（友だち追加した その場で 1回 とどく）
   ② 自動返信（利用者が 書くたびに きまった 返事が 返る）

   つかいかた:   cd ~/quizshare-py && python3 apply_official2.py

   ・書きかえる 前に /tmp に バックアップを とる
   ・ぜんぶ 検査して OK の ときだけ 書きこむ（1つでも 合わなければ 何も かえない）
   ・書いた あとの import が しっぱいしたら 自動で 元に もどす
"""
import os, re, sys, shutil, sqlite3, subprocess

PY_FILE = 'oma_mail.py'
HTML_FILE = os.path.join('templates', 'oma', 'mail.html')
DB_FILE = os.environ.get('SQLITE_PATH', '/home/yuto113/quizshare.db')

# ------------------------------------------------------------------
# oma_mail.py の さいごに 足す もの
# ------------------------------------------------------------------
NEW_PY = r'''
# ====================================================================
# 公式アカウント: はじめの メッセージ・自動返信
#   ・はじめの メッセージ … 友だち追加した その場で 1回だけ とどく
#   ・自動返信 … 利用者が 書くたびに きまった 返事が 返る
#   ・送り主は 'off:<公式ID>'。extra の しるし（by:welcome / by:auto）で 見わける
# ====================================================================
def _off_settings(oid):
    c = _db()
    r = c.execute('SELECT welcome, welcome_on, auto_reply, auto_on '
                  'FROM oma_official WHERE id=?', (oid,)).fetchone()
    c.close()
    if not r:
        return None
    return {'welcome': r['welcome'] or '',
            'welcome_on': int(r['welcome_on'] or 0),
            'auto_reply': r['auto_reply'] or '',
            'auto_on': int(r['auto_on'] or 0)}


def _off_say(room_id, oid, text, tag):
    """公式の 名前で 1通 入れる（tag は 'by:welcome' か 'by:auto'）"""
    c = _db()
    c.execute("INSERT INTO oma_msg(room_id,member_id,body,created_at,kind,extra) "
              "VALUES(?,?,?,?,'text',?)",
              (room_id, 'off:%s' % oid, enc(text), jst_str(), tag))
    c.commit(); c.close()


def _off_room_exists(oid, member_id):
    """その人と その公式の 部屋が もう あるか"""
    c = _db()
    r = c.execute("SELECT 1 FROM oma_room r JOIN oma_room_member m "
                  "ON m.room_id=r.id AND m.member_id=? "
                  "WHERE r.kind='official' AND r.owner=?",
                  (member_id, 'off:%s' % oid)).fetchone()
    c.close()
    return bool(r)


def _send_welcome(oid, room_id):
    """友だち追加した ときの「はじめの メッセージ」。
       しっぱいしても 友だち追加 そのものは 成功させる"""
    try:
        st = _off_settings(oid)
        if st and st['welcome_on'] and st['welcome'].strip():
            _off_say(room_id, oid, st['welcome'].strip(), 'by:welcome')
    except Exception as e:
        print('[official welcome]', e)


def _auto_reply(rid, member_id, kind):
    """利用者が 書いたら きまった 返事を 返す。
       しっぱいしても 利用者の 送信は こわさない"""
    try:
        if kind != 'text':
            return
        c = _db()
        room = c.execute("SELECT kind, owner FROM oma_room WHERE id=?",
                         (rid,)).fetchone()
        c.close()
        if not room or room['kind'] != 'official':
            return
        oid = str(room['owner'] or '')[4:]
        if not oid.isdigit():
            return
        st = _off_settings(int(oid))
        if not st or not st['auto_on'] or not st['auto_reply'].strip():
            return
        # つづけて 何通も 書かれても、10びょうに 1回だけ 返す
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        thr = (_dt.now(_tz(_td(hours=9))) - _td(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')
        c = _db()
        n = c.execute("SELECT COUNT(*) AS n FROM oma_msg WHERE room_id=? "
                      "AND member_id=? AND extra='by:auto' AND created_at>?",
                      (rid, 'off:%s' % oid, thr)).fetchone()['n']
        c.close()
        if n:
            return
        _off_say(rid, int(oid), st['auto_reply'].strip(), 'by:auto')
    except Exception as e:
        print('[official auto]', e)


def _waiting(c, room_id):
    """運営者の 返事を まって いるか。自動の 返事・はじめの メッセージは 数えない"""
    r = c.execute("SELECT member_id FROM oma_msg WHERE room_id=? AND hidden=0 "
                  "AND COALESCE(extra,'') NOT IN ('by:auto','by:welcome') "
                  "ORDER BY id DESC LIMIT 1", (room_id,)).fetchone()
    return bool(r and not str(r['member_id']).startswith('off:'))


@bp_mail.route('/api/mail/official/<int:oid>/settings', methods=['GET', 'POST'])
def api_off_settings(oid):
    """はじめの メッセージ・自動返信の 設定。見るのは 運営者、かえるのは オーナー"""
    u = _me()
    if not u:
        return jsonify(ok=False), 401
    role = _off_role(oid, u['member_id'])
    if not role:
        return jsonify(ok=False, error='運営者では ありません'), 403
    if request.method == 'GET':
        st = _off_settings(oid) or {}
        return jsonify(ok=True, **st)
    if role != 'owner':
        return jsonify(ok=False, error='オーナーだけが かえられます'), 403
    d = request.get_json(silent=True) or {}
    welcome = (d.get('welcome') or '').strip()[:1000]
    auto = (d.get('auto_reply') or '').strip()[:1000]
    w_on = 1 if d.get('welcome_on') else 0
    a_on = 1 if d.get('auto_on') else 0
    if w_on and not welcome:
        return jsonify(ok=False, error='はじめの メッセージを 書いて ください'), 400
    if a_on and not auto:
        return jsonify(ok=False, error='自動返信の ないようを 書いて ください'), 400
    c = _db()
    c.execute('UPDATE oma_official SET welcome=?, welcome_on=?, auto_reply=?, '
              'auto_on=? WHERE id=?', (welcome, w_on, auto, a_on, oid))
    c.commit(); c.close()
    return jsonify(ok=True)
'''

# ------------------------------------------------------------------
# mail.html: 「おしらせ」の 画面を 作りなおす
# ------------------------------------------------------------------
NEW_BCAST_JS = r'''function ofBack(){ ofManage(OF_ID, '', ''); }

async function ofBroadcast(){
  var st = await J('/api/mail/official/' + OF_ID + '/settings');
  if (st.ok !== true){ toast(st.error, 'error'); return; }
  var owner = (OF_ROLE === 'owner');
  var dis = owner ? '' : ' disabled';
  var sec = 'font-weight:700;font-size:13px;margin:0 0 3px';
  var note = 'font-size:12px;color:var(--muted);margin:0 0 6px;line-height:1.7';
  var lab = 'display:flex;align-items:center;gap:6px;font-size:12.5px;margin:4px 0 6px';
  var hr = '<hr style="border:none;border-top:1px solid var(--line2);margin:16px 0">';
  openModal(
      '<h1 style="font-size:17px;margin-bottom:12px">📢 おしらせ・自動メッセージ</h1>'

    + '<div style="' + sec + '">一斉に おしらせ</div>'
    + '<p style="' + note + '">追加して くれた 全員の トークに とどきます。</p>'
    + '<textarea class="inp" id="bcText" rows="3" placeholder="おしらせの ないよう"></textarea>'
    + '<div class="row" style="margin-top:8px">'
    + '<button class="btn mn" onclick="ofBroadcastSend()">おくる</button></div>'

    + hr
    + '<div style="' + sec + '">👋 はじめの メッセージ</div>'
    + '<p style="' + note + '">公式を 追加した 人に、入った ときに すぐ とどきます'
    + '（1人に 1回だけ）。</p>'
    + '<label style="' + lab + '"><input type="checkbox" id="wlOn"'
    + (st.welcome_on ? ' checked' : '') + dis + '> つかう</label>'
    + '<textarea class="inp" id="wlText" rows="3" placeholder="はじめに 見せる メッセージ"'
    + dis + '>' + K(st.welcome || '') + '</textarea>'

    + hr
    + '<div style="' + sec + '">🤖 自動返信</div>'
    + '<p style="' + note + '">利用者が メッセージを 書くたびに、ここに 書いた 返事が '
    + '自動で 返ります（つづけて 書かれた ときは 10びょうに 1回）。'
    + '人が 返事を する ことも できます。</p>'
    + '<label style="' + lab + '"><input type="checkbox" id="arOn"'
    + (st.auto_on ? ' checked' : '') + dis + '> つかう</label>'
    + '<textarea class="inp" id="arText" rows="3" placeholder="自動で 返す 返事"'
    + dis + '>' + K(st.auto_reply || '') + '</textarea>'

    + (owner
        ? '<div class="row" style="margin-top:12px">'
          + '<button class="btn mn" onclick="ofSaveSettings()">設定を ほぞん</button></div>'
        : '<p style="font-size:11.5px;color:var(--muted);margin-top:8px">'
          + 'この 設定は オーナーだけが かえられます。</p>')
    + '<div class="row" style="margin-top:12px">'
    + '<button class="btn gh" onclick="ofBack()">もどる</button></div>');
}

async function ofSaveSettings(){
  var d = await P('/api/mail/official/' + OF_ID + '/settings', {
    welcome: $('wlText').value, welcome_on: $('wlOn').checked ? 1 : 0,
    auto_reply: $('arText').value, auto_on: $('arOn').checked ? 1 : 0});
  if (d.ok !== true){ toast(d.error, 'error'); return; }
  toast('ほぞんしました');
}

'''


def die(msg):
    print('★' + msg)
    print('★何も 書きかえて いません')
    sys.exit(1)


def js_ok(js):
    """かっこと 引用符の 検査（正規表現の リテラルは 先に のぞく）"""
    js = re.sub(r"\.replace\(/(?:\\.|[^/\\\n])+/[gimsuy]*,", ".replace(0,", js)
    p = b = 0
    q = None
    esc = False
    for ch in js:
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if q:
            if ch == q:
                q = None
            continue
        if ch in '"\'`':
            q = ch
            continue
        if ch == '(':
            p += 1
        elif ch == ')':
            p -= 1
        elif ch == '{':
            b += 1
        elif ch == '}':
            b -= 1
    return (p == 0 and b == 0 and q is None), p, b


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die('%s: 置きかえる ばしょが %d こ みつかりました（1こ の はず）' % (label, n))
    return text.replace(old, new, 1)


def main():
    if not os.path.isfile(PY_FILE) or not os.path.isfile(HTML_FILE):
        die('oma_mail.py か mail.html が みつかりません。~/quizshare-py で 実行して ください')

    py = open(PY_FILE, encoding='utf-8').read()
    html = open(HTML_FILE, encoding='utf-8').read()

    has_py = '_auto_reply' in py
    has_js = 'ofSaveSettings' in html
    if has_py and has_js:
        print('すでに 入って います')
        return
    if has_py or has_js:
        die('片方だけ 入って います。手で たしかめて ください')

    for need in ('def _db(', 'def enc(', 'def jst_str(', 'def _off_role(',
                 'def _off_room(', 'bp_mail = Blueprint'):
        if need not in py:
            die('oma_mail.py に「%s」が ありません' % need)

    # ===== oma_mail.py =====
    # ① 送信の さいごに 自動返信（api_mail_send の 中だけ）
    i = py.find('def api_mail_send():')
    if i < 0:
        die('api_mail_send が みつかりません')
    j = py.find('@bp_mail.route', i)
    if j < 0:
        die('api_mail_send の つぎの ルートが みつかりません')
    body = py[i:j]
    old = "    c.commit(); c.close()\n    return jsonify(ok=True, id=mid)"
    new = ("    c.commit(); c.close()\n"
           "    _auto_reply(rid, u['member_id'], _kind)\n"
           "    return jsonify(ok=True, id=mid)")
    body = replace_once(body, old, new, 'api_mail_send')
    py2 = py[:i] + body + py[j:]

    # ② 公式を 追加した とき、部屋が はじめてなら はじめの メッセージ
    py2 = replace_once(
        py2,
        "    rid = _off_room(oid, u['member_id'])\n    return jsonify(ok=True, room_id=rid)",
        "    existed = _off_room_exists(oid, u['member_id'])\n"
        "    rid = _off_room(oid, u['member_id'])\n"
        "    if not existed:\n"
        "        _send_welcome(oid, rid)\n"
        "    return jsonify(ok=True, room_id=rid)",
        'api_off_add')

    # ③ 受信箱の「返事まち」は 自動の 返事を 数えない
    py2 = replace_once(
        py2,
        "'from_user': bool(last and not str(last['member_id']).startswith('off:')),",
        "'from_user': _waiting(c, r['id']),",
        'api_off_inbox')

    # ④ 新しい 関数を さいごに
    py2 = py2.rstrip('\n') + '\n\n\n' + NEW_PY.strip('\n') + '\n'
    try:
        compile(py2, PY_FILE, 'exec')
    except SyntaxError as e:
        die('oma_mail.py の 新版に 構文エラー: %s' % e)

    # ===== mail.html =====
    h2 = replace_once(
        html,
        "var OF_ID = null, OF_ROOM = null;\n"
        "async function ofManage(id, name, role){\n"
        "  OF_ID = id;\n",
        "var OF_ID = null, OF_ROOM = null, OF_NAME = '', OF_ROLE = 'staff';\n"
        "async function ofManage(id, name, role){\n"
        "  OF_ID = id;\n"
        "  // 名前が 空なら「もどる」から。さいごに ひらいた 公式の 名前と 立場を つかう\n"
        "  if (name){ OF_NAME = name; OF_ROLE = role; }\n"
        "  name = OF_NAME; role = OF_ROLE;\n",
        'ofManage')

    h2 = replace_once(h2, '📢 一斉に おしらせ</button>',
                      '📢 おしらせ・自動メッセージ</button>', 'ボタンの 名前')

    a = h2.find('function ofBroadcast(){')
    b = h2.find('async function ofBroadcastSend(){')
    if a < 0 or b < 0 or a >= b:
        die('ofBroadcast の ばしょが わかりません')
    if h2[a:b].count('function ') != 1:
        die('ofBroadcast の あいだに ほかの 関数が あります')
    h2 = h2[:a] + NEW_BCAST_JS + h2[b:]

    k = h2.rindex('<script>')
    ok, p, bb = js_ok(h2[k + 8:h2.index('</script>', k)])
    if not ok:
        die('mail.html の かっこが 合いません（丸 %d / 波 %d）' % (p, bb))

    # ===== ここまで ぜんぶ OK。バックアップ → DB → 書きこみ =====
    shutil.copy(PY_FILE, '/tmp/oma_mail.py.bak3')
    shutil.copy(HTML_FILE, '/tmp/mail.html.bak3')
    print('バックアップ OK（/tmp/oma_mail.py.bak3, /tmp/mail.html.bak3）')

    con = sqlite3.connect(DB_FILE)
    for col, typ in (('welcome', 'TEXT'), ('welcome_on', 'INTEGER DEFAULT 0'),
                     ('auto_reply', 'TEXT'), ('auto_on', 'INTEGER DEFAULT 0')):
        try:
            con.execute('ALTER TABLE oma_official ADD COLUMN %s %s' % (col, typ))
            print('DB: oma_official.%s を 足しました' % col)
        except sqlite3.OperationalError as e:
            if 'duplicate column' in str(e).lower():
                print('DB: oma_official.%s は もう あります' % col)
            else:
                con.close()
                die('DB の 列を 足せませんでした: %s' % e)
    con.commit()
    con.close()

    try:
        open(PY_FILE, 'w', encoding='utf-8').write(py2)
        open(HTML_FILE, 'w', encoding='utf-8').write(h2)
        r = subprocess.run([sys.executable, '-c', 'import oma_mail'],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or '')[-400:])
    except Exception as e:
        shutil.copy('/tmp/oma_mail.py.bak3', PY_FILE)
        shutil.copy('/tmp/mail.html.bak3', HTML_FILE)
        print('★しっぱいしたので 2つとも 元に もどしました:', e)
        sys.exit(1)

    print('★ oma_mail.py に 追加しました')
    print('★ mail.html を 更新しました')
    print('★ 完了')


main()
