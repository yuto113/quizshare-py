/* ============ 💬 つぶやき（X風） ============ */
var TK_MODE = 'all', TK_GID = null, TK_TAG = null, TK_LAST = null;

function avHtml(m, small){
  var kind = m.avatar_kind || 'letter';
  var col = m.color || pickColor(m.member_id || m.nickname || 'x');
  var cls = 'av' + (small ? ' sm' : '');
  var on = 'onclick="viewAuthor(\'' + K(m.member_id||'') + '\');event.stopPropagation()"';
  if (kind === 'img' && m.avatar)
    return '<div class="' + cls + '" ' + on + '><img src="' + K(m.avatar) + '"></div>';
  if (kind === 'emoji' && m.avatar)
    return '<div class="' + cls + '" ' + on + ' style="background:' + col
      + '">' + K(m.avatar) + '</div>';
  var ch = (m.nickname || m.member_id || '?').charAt(0);
  return '<div class="' + cls + '" ' + on + ' style="background:' + col + '">'
    + K(ch) + '</div>';
}

function pickColor(seed){
  var C = ['#0891b2','#7c3aed','#db2777','#16a34a','#ea580c','#0284c7',
           '#9333ea','#c2410c','#059669','#dc2626'];
  var n = 0;
  for (var i = 0; i < String(seed).length; i++) n += String(seed).charCodeAt(i);
  return C[n % C.length];
}

function ago(s){
  if (!s) return '';
  var t = new Date(String(s).replace(' ', 'T'));
  var d = (Date.now() - t.getTime()) / 1000;
  if (d < 60) return 'いま';
  if (d < 3600) return Math.floor(d/60) + '分';
  if (d < 86400) return Math.floor(d/3600) + '時間';
  if (d < 604800) return Math.floor(d/86400) + '日';
  return (t.getMonth()+1) + '月' + t.getDate() + '日';
}

function linkify(t){
  var out = K(t);
  out = out.replace(/[#＃]([0-9A-Za-z_ぁ-んァ-ヶ一-龠ー]{1,20})/g,
    function(m0, g1){
      return '<span class="tk-tag" onclick="pickTag(\'' + g1 + '\')">#' + g1 + '</span>';
    });
  out = out.replace(/(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener">$1</a>');
  return out;
}

async function loadTalk(mode, gid, more){
  if (!more){ TK_MODE = mode || 'all'; TK_GID = gid || null; TK_LAST = null;
              if (mode !== 'tag') TK_TAG = null; }
  var q = '?';
  if (TK_MODE === 'group' && TK_GID) q += 'group=' + TK_GID;
  else if (TK_MODE === 'follow') q += 'following=1';
  else if (TK_MODE === 'tag' && TK_TAG) q += 'tag=' + encodeURIComponent(TK_TAG);
  if (more && TK_LAST) q += '&before=' + TK_LAST;

  var d = await J('/api/mp/posts' + q);
  var el = $('talkBody');
  if (!d.ok){ el.innerHTML = '<div class="empty">' + K(d.error) + '</div>'; return; }
  if (d.posts.length) TK_LAST = d.posts[d.posts.length - 1].id;

  if (more){
    var box = $('tkList');
    if (box) box.insertAdjacentHTML('beforeend', d.posts.map(tkCard).join(''));
    var b = $('tkMore');
    if (b) b.style.display = d.more ? '' : 'none';
    return;
  }

  var tabs = '<div class="tk-tabs">'
    + '<button class="' + (TK_MODE==='all'?'on':'') + '" onclick="loadTalk(\'all\')">'
    + 'ぜんぶ</button>'
    + '<button class="' + (TK_MODE==='follow'?'on':'') + '" '
    + 'onclick="loadTalk(\'follow\')">フォロー中</button>'
    + (d.groups||[]).map(function(g){
        return '<button class="' + (TK_MODE==='group'&&TK_GID==g.id?'on':'') + '" '
          + 'onclick="loadTalk(\'group\',' + g.id + ')">' + K(g.name)
          + ' <span style="opacity:.55">' + g.members + '</span></button>';
      }).join('')
    + '<button onclick="newGroup()">＋ グループ</button></div>'
    + (TK_TAG ? '<div class="row" style="margin-bottom:10px">'
        + '<b style="font-size:14px">#' + K(TK_TAG) + '</b>'
        + '<button class="btn gh sm" onclick="loadTalk(\'all\')">やめる</button></div>'
        : '')
    + ((d.tags||[]).length && !TK_TAG
        ? '<div class="row" style="margin-bottom:11px;gap:5px">'
          + '<span style="font-size:11.5px;color:var(--muted)">よく つかわれて '
          + 'いる ことば:</span>'
          + d.tags.map(function(t){
              return '<span class="tk-tag" style="font-size:12.5px" '
                + 'onclick="pickTag(\'' + K(t.tag) + '\')">#' + K(t.tag) + '</span>';
            }).join('') + '</div>'
        : '');

  var form = ME ? '<div class="tk-form">' + avHtml(ME)
    + '<div style="flex:1;min-width:0">'
    + '<textarea id="tkBody" maxlength="320" '
    + 'placeholder="いまの きもちを ひとこと…" oninput="tkCount(this)"></textarea>'
    + '<div class="bt">'
    + '<button class="btn cy sm" onclick="sendTalk()">つぶやく</button>'
    + '<span style="font-size:11.5px;color:var(--muted)">'
    + (TK_MODE==='group' ? 'この グループに' : 'みんなに 見えます') + '</span>'
    + '<span class="tk-cnt" id="tkCnt">300</span></div>'
    + '<div id="tkMsg" style="font-size:12px"></div></div></div>' : '';

  el.innerHTML = '<div class="tl">' + tabs + form + '<div id="tkList">'
    + (d.posts.length ? d.posts.map(tkCard).join('')
       : '<div class="empty">まだ つぶやきが ありません。さいしょに どうぞ。</div>')
    + '</div>'
    + '<button class="tk-more" id="tkMore" onclick="loadTalk(null,null,1)" '
    + 'style="' + (d.more ? '' : 'display:none') + '">もっと 見る</button></div>';
}

function tkCard(p){
  var rp = p.repost_of && p.r_id;
  var src = rp ? {member_id:p.r_member, nickname:p.r_nick, avatar:p.r_avatar,
                  avatar_kind:p.r_avatar_kind, color:p.r_color} : p;
  var body = (rp && !p.body) ? p.r_body : p.body;
  var at = (rp && !p.body) ? p.r_at : p.created_at;
  var showId = (rp && !p.body) ? p.r_id : p.id;

  return '<div class="tk" id="tk' + p.id + '">' + avHtml(src)
    + '<div class="tk-main">'
    + (rp ? '<div class="tk-rp">🔁 ' + K(p.nickname||p.member_id)
        + ' さんが ひろめました</div>' : '')
    + '<div class="tk-hd"><b onclick="viewAuthor(\'' + K(src.member_id) + '\')">'
    + K(src.nickname || src.member_id) + '</b>'
    + '<span class="id">@' + K(src.member_id) + '</span>'
    + (p.group_name ? '<span class="tag">' + K(p.group_name) + '</span>' : '')
    + '<span class="when">' + ago(at) + '</span></div>'
    + '<div class="tk-body">' + linkify(body || '') + '</div>'
    + (p.quote_of && p.q_body
        ? '<div class="tk-quote" onclick="openThread(' + p.quote_of + ')">'
          + '<div class="qh">' + avHtml({member_id:p.q_member, nickname:p.q_nick,
              avatar:p.q_avatar, avatar_kind:p.q_avatar_kind, color:p.q_color}, true)
          + '<b>' + K(p.q_nick||p.q_member) + '</b>'
          + '<span style="color:var(--muted)">' + ago(p.q_at) + '</span></div>'
          + '<div class="qb">' + K(String(p.q_body).slice(0,140)) + '</div></div>'
        : '')
    + '<div class="tk-acts">'
    + '<button class="tk-act" onclick="openThread(' + showId + ')">💬 '
    + (p.replies||'') + '</button>'
    + '<button class="tk-act rp' + (p.reposted?' on':'') + '" '
    + 'onclick="repost(' + showId + ')">🔁 ' + (p.reposts||'') + '</button>'
    + '<button class="tk-act' + (p.liked?' on':'') + '" '
    + 'onclick="likeTalk(' + showId + ')">' + (p.liked?'♥':'♡') + ' '
    + (p.likes||'') + '</button>'
    + '<button class="tk-act" onclick="quotePost(' + showId + ')">✍️</button>'
    + (ME && ME.member_id === p.member_id
        ? '<button class="tk-act" onclick="hideTalk(' + p.id + ')">🗑</button>' : '')
    + '</div></div></div>';
}

function tkCount(t){
  var n = 300 - t.value.length;
  var el = $('tkCnt');
  if (el){ el.textContent = n; el.classList.toggle('over', n < 0); }
}

async function sendTalk(){
  var t = $('tkBody').value.trim();
  if (!t || t.length > 300) return;
  var b = {body:t};
  if (TK_MODE === 'group' && TK_GID) b.group_id = TK_GID;
  if (window.TK_QUOTE) b.quote_of = window.TK_QUOTE;
  var d = await P('/api/mp/post', b);
  if (d.ok !== true){
    var m = $('tkMsg'); m.style.color='#c0453b'; m.textContent = d.error; return; }
  window.TK_QUOTE = null;
  loadTalk(TK_MODE, TK_GID);
}

async function likeTalk(id){
  var d = await P('/api/mp/post/' + id + '/like');
  if (d.ok === true) loadTalk(TK_MODE, TK_GID);
}

async function repost(id){
  var d = await P('/api/mp/post', {repost_of:id});
  if (d.ok !== true){ alert(d.error); return; }
  loadTalk(TK_MODE, TK_GID);
}

function quotePost(id){
  window.TK_QUOTE = id;
  var t = $('tkBody');
  if (t){ t.focus(); t.placeholder = 'この つぶやきに ひとこと…'; }
  window.scrollTo({top:0, behavior:'smooth'});
}

async function hideTalk(id){
  if (!confirm('この つぶやきを けしますか？')) return;
  await P('/api/mp/post/' + id + '/hide');
  loadTalk(TK_MODE, TK_GID);
}

function pickTag(t){ TK_TAG = t; loadTalk('tag'); }

async function openThread(id){
  var d = await J('/api/mp/post/' + id);
  if (d.ok !== true){ alert(d.error); return; }
  var p = d.post;
  $('talkBody').innerHTML = '<div class="tl">'
    + '<button class="btn gh sm" onclick="loadTalk(TK_MODE,TK_GID)" '
    + 'style="margin-bottom:12px">← もどる</button>'
    + tkCard(p)
    + (ME ? '<div class="tk-form" style="margin-top:12px">' + avHtml(ME)
        + '<div style="flex:1;min-width:0">'
        + '<textarea id="tkRep" maxlength="320" placeholder="かえす…"></textarea>'
        + '<div class="bt"><button class="btn cy sm" onclick="sendReply(' + id + ')">'
        + 'かえす</button></div></div></div>' : '')
    + '<div style="margin-top:12px">'
    + (d.replies.length ? d.replies.map(tkCard).join('')
       : '<div class="empty">まだ かえしが ありません</div>')
    + '</div></div>';
}

async function sendReply(id){
  var t = $('tkRep').value.trim();
  if (!t) return;
  var d = await P('/api/mp/post', {body:t, reply_to:id});
  if (d.ok !== true){ alert(d.error); return; }
  openThread(id);
}

async function newGroup(){
  var n = prompt('グループの 名前');
  if (!n) return;
  var d = await P('/api/mp/group', {name:n});
  if (d.ok !== true){ alert(d.error); return; }
  loadTalk('group', d.id);
}

/* ============ 大きく うごかす ============ */
async function bigRun(){
  if (FILES[CUR]) FILES[CUR].content = $('eCode').value;
  var risks = checkRisk(FILES);
  if (risks.length && !confirm('⚠ 気を つけて\n\n' + risks.join('\n\n')
      + '\n\nそれでも うごかしますか？')) return;
  $('bigTitle').textContent = $('eTitle').value || 'けっか';
  $('bigWrap').classList.add('on');
  $('bigOut').classList.remove('on');
  $('bigOut').innerHTML = '';
  window.MP_APP_ID = APP.id;
  var sql = tablesToSql(TABLES);
  await runFiles(FILES, {name:DB.name, schema:sql || DB.schema, seed:sql ? '' : DB.seed},
                 $('bigOut'), $('bigFrame'));
  // HTML が 出ているなら 出力欄は かぶせない
  var hasHtml = FILES.some(function(f){
    return f.kind === 'html' && (f.content||'').trim(); });
  if (!hasHtml && $('bigOut').innerHTML) $('bigOut').classList.add('on');
}
function bigClose(){
  $('bigWrap').classList.remove('on');
  $('bigFrame').srcdoc = '';
}
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape' && $('bigWrap').classList.contains('on')) bigClose();
});

/* ============ 公開ページ ============ */
async function openPages(){
  var d = await J('/api/mp/pages');
  if (!d.ok){ alert(d.error); return; }
  $('modalBody').innerHTML =
      '<h1 style="font-size:18px;margin-bottom:4px">🌐 公開ページ</h1>'
    + '<p class="lead">じぶんの URL で、作品だけを 見せられます。'
    + '会員じゃない人にも 見せられます（家族や 友だちに どうぞ）。</p>'
    + '<div class="note">つくれるのは ' + d.used + ' / '
    + (d.limit >= 1e8 ? '無制限' : d.limit) + ' 個です</div>'
    + '<div class="card" style="background:#f7f9fa">'
    + '<b style="font-size:12.5px">＋ あたらしい ページ</b>'
    + '<div class="row" style="margin-top:9px">'
    + '<span style="font-size:12px;color:var(--muted)">/page/</span>'
    + '<input class="inp" id="pgSlug" placeholder="mygame" style="flex:1;min-width:110px">'
    + '<input class="inp" id="pgIcon" placeholder="🎮" maxlength="4" style="max-width:62px">'
    + '</div>'
    + '<div class="row">'
    + '<select class="sel" id="pgApp" style="flex:1;min-width:130px">'
    + (d.my_apps.length
        ? d.my_apps.map(function(a){
            return '<option value="' + a.id + '"' + (a.id===APP.id ? ' selected' : '')
              + '>' + K(a.title) + '</option>'; }).join('')
        : '<option value="">公開ずみの 作品が ありません</option>')
    + '</select>'
    + '<input class="inp" id="pgTitle" placeholder="タブに 出す 名前（任意）" '
    + 'style="flex:1;min-width:130px"></div>'
    + '<div class="row"><button class="btn" onclick="savePage()">つくる</button>'
    + '<span id="pgMsg" style="font-size:12.5px"></span></div></div>'
    + (d.pages.length
        ? '<div class="sec" style="margin-top:16px">つくった ページ</div>'
          + d.pages.map(function(p){
              var url = location.origin + '/page/' + p.slug;
              return '<div class="card" style="display:flex;align-items:center;gap:10px">'
                + '<span style="font-size:22px">' + K(p.favicon || '🎈') + '</span>'
                + '<div style="flex:1;min-width:0">'
                + '<a href="/page/' + K(p.slug) + '" target="_blank" '
                + 'style="color:var(--cy2);font-weight:600;font-size:13px">/page/'
                + K(p.slug) + '</a>'
                + '<div style="font-size:11.5px;color:var(--muted)">'
                + K(p.app_title) + ' · 👁' + p.views + '</div></div>'
                + '<button class="btn gh sm" onclick="copyPage(\'' + K(p.slug) + '\')">'
                + 'リンク</button>'
                + '<button class="btn gh sm" onclick="delPage(\'' + K(p.slug) + '\')">'
                + 'けす</button></div>'; }).join('')
        : '')
    + '<div class="row" style="margin-top:12px">'
    + '<button class="btn gh" onclick="closeModal()">とじる</button></div>';
  $('modal').classList.add('on');
}

async function savePage(){
  var slug = $('pgSlug').value.trim().toLowerCase();
  var app = $('pgApp').value;
  if (!slug){ $('pgMsg').style.color='#c0453b';
    $('pgMsg').textContent='URLの名前を 入れてください'; return; }
  if (!app){ $('pgMsg').style.color='#c0453b';
    $('pgMsg').textContent='先に 作品を 公開してください'; return; }
  var d = await P('/api/mp/page', {slug:slug, app_id:parseInt(app),
    title:$('pgTitle').value, favicon:$('pgIcon').value});
  if (!d.ok){ $('pgMsg').style.color='#c0453b'; $('pgMsg').textContent=d.error; return; }
  openPages();
}
function copyPage(slug){
  var url = location.origin + '/page/' + slug;
  if (navigator.clipboard) navigator.clipboard.writeText(url).then(function(){
    alert('コピーしました\n' + url); });
  else prompt('この リンクを コピーしてください', url);
}
async function delPage(slug){
  if (!confirm('/page/' + slug + ' を けしますか？')) return;
  await fetch('/api/mp/page/' + slug, {method:'DELETE'});
  openPages();
}

/* ============ 2段のメニュー ============ */
var CATS = [
  ['prog', '🧩 プログラミング', 1, [
      ['block','🧩 ブロックで つくる',1],
      ['edit','✏️ コードで つくる',1],
      ['apps','📦 みんなの作品',1],
      ['mine','📁 自分の作品',1],
      ['favs','☆ お気に入り',1]]],
  ['talk', '💬 つぶやき', 1, [['talk','💬 つぶやき',1]]],
  ['wiki', '📖 みんなの図鑑', 1, [['wiki','📖 図鑑',1]]],
  ['shelf','🗂 作品の棚', 1, [['shelf','🗂 棚',1]]],
  ['qa',   '❓ しつもん', 1, [['qa','❓ しつもん',1]]],
  ['draw', '🎨 おえかき', 1, [['draw','🎨 おえかき',1]]],
    ['poll', '🗳 とうひょう', 1, [['poll','🗳 とうひょう',1]]]
];

var CURCAT = 'prog';

/* どの ページが どの カテゴリか */
var PAGE_CAT = {};
CATS.forEach(function(c){ c[3].forEach(function(x){ PAGE_CAT[x[0]] = c[0]; }); });

function drawCat(){
  var el = $('catBar');
  if (!el) return;
  el.innerHTML = CATS.map(function(c){
    return '<button class="' + (c[0]===CURCAT ? 'on' : '') + (c[2] ? '' : ' soon')
      + '" onclick="pickCat(\'' + c[0] + '\')">' + c[1] + '</button>';
  }).join('');
  drawSub();
}

function drawSub(){
  var el = $('subBar');
  if (!el) return;
  var c = CATS.filter(function(x){ return x[0] === CURCAT; })[0];
  if (!c){ el.innerHTML = ''; return; }
  var now = (typeof CURPAGE !== 'undefined') ? CURPAGE : '';
  el.innerHTML = c[3].map(function(x){
    return '<button class="' + (x[0]===now ? 'on' : '') + '" onclick="'
      + (x[2] ? (x[0]==='edit' ? 'newApp()' : "go('" + x[0] + "')")
              : "notReady('" + K(x[1].replace(/^[^ ]+ /, '')) + "')")
      + '">' + K(x[1]) + (x[2] ? '' : ' <span class="soon-tag">準備中</span>') + '</button>';
  }).join('') + (c[2] ? '' : '<span class="note">この ページは いま つくって います</span>');
}

function pickCat(cat){
  var c = CATS.filter(function(x){ return x[0] === cat; })[0];
  if (!c) return;
  CURCAT = cat;
  drawCat();
  if (!c[2]){ return; }              // まだ つくって いない
  var first = c[3].filter(function(x){ return x[2]; })[0];
  if (first) go(first[0]);
}

/* ============ ホーム ============ */



/* ============ セットA・B の画面 ============ */
var CURTAG = '';

async function loadTags(){
  var d = await J('/api/mp/tags');
  if (!d.ok) return;
  var el = $('tagBar');
  if (!d.tags.length){ el.innerHTML = ''; return; }
  el.innerHTML = '<span style="font-size:11.5px;color:var(--muted)">タグ:</span>'
    + '<button class="btn ' + (CURTAG===''?'':'gh') + ' sm" onclick="pickTag(\'\')">'
    + 'ぜんぶ</button>'
    + d.tags.map(function(t){
        return '<button class="btn ' + (CURTAG===t.name?'':'gh') + ' sm" '
          + 'onclick="pickTag(\'' + K(t.name) + '\')">' + K(t.name)
          + ' <span style="opacity:.6">' + t.count + '</span></button>'; }).join('');
}
function pickTag(t){ CURTAG = t; loadApps(); }

async function loadFavs(){
  var d = await J('/api/mp/favs');
  $('favList').innerHTML = (d.ok && d.apps.length)
    ? d.apps.map(function(a){ return appCard(a, false); }).join('')
    : '<div class="empty">まだ ありません。作品の ☆を おしてみてください。</div>';
}

async function toggleFav(id, ev){
  if (ev) ev.stopPropagation();
  var d = await P('/api/mp/app/' + id + '/fav');
  if (!d.ok){ alert(d.error); return; }
  if ($('p-favs').classList.contains('on')) loadFavs();
  else if ($('p-view').classList.contains('on')) viewApp(id);
  else loadApps();
}

async function loadNotices(){
  var d = await J('/api/mp/notices');
  if (!d.ok){ $('noticeList').innerHTML = '<div class="empty">' + K(d.error) + '</div>'; return; }
  var LBL = {like:'♡ いいねが つきました', fork:'🔀 コピーされました',
             featured:'⭐ 注目作品に えらばれました', comment:'💬 コメントが つきました'};
  $('noticeList').innerHTML = d.notices.length
    ? d.notices.map(function(n){
        return '<div class="card" style="' + (n.is_read ? 'opacity:.6' : '') + '">'
          + '<b>' + K(LBL[n.kind] || n.kind) + '</b>'
          + (n.title ? '<div style="font-size:12.5px;margin-top:3px">'
              + (n.app_id ? '<a href="#" onclick="viewApp(' + n.app_id
                  + ');return false" style="color:var(--cy2)">' + K(n.title) + '</a>'
                : K(n.title)) + '</div>' : '')
          + '<div style="font-size:11.5px;color:var(--muted);margin-top:3px">'
          + (n.actor_name ? K(n.actor_name) + ' · ' : '') + K(n.created_at) + '</div></div>';
      }).join('')
    : '<div class="empty">まだ おしらせは ありません</div>';
  await P('/api/mp/notices/read');
  updateBadge(0);
}

function updateBadge(n){
  var b = $('nvBadge');
  if (!b) return;
  b.textContent = n > 0 ? ' ' + n : '';
  b.style.cssText = n > 0
    ? 'background:#dc2626;color:#fff;border-radius:99px;padding:1px 6px;font-size:10px;margin-left:3px'
    : '';
}

async function checkNotices(){
  if (!ME) return;
  var d = await J('/api/mp/notices');
  if (d.ok) updateBadge(d.unread);
}

async function viewAuthor(author){
  var d = await J('/api/mp/u/' + author);
  if (!d.ok){ alert(d.error); return; }
  var a = d.author;
  $('authorBody').innerHTML =
      '<div class="hero"><h1>' + K(a.nickname) + '</h1>'
    + '<p>' + (a.grade ? K(a.grade) + ' · ' : '')
    + 'はじめて ' + a.days + '日目</p>'
    + '<div class="stats">'
    + '<div class="stat"><b>' + a.apps + '</b><span>作品</span></div>'
    + '<div class="stat"><b>' + a.likes + '</b><span>もらった いいね</span></div>'
    + '<div class="stat"><b>' + a.views + '</b><span>見られた</span></div>'
    + '<div class="stat"><b>' + a.forked + '</b><span>コピーされた</span></div>'
    + '</div></div>'
    + '<div class="grid">'
    + (d.apps.length ? d.apps.map(function(x){ return appCard(x, false); }).join('')
                     : '<div class="empty">まだ 公開作品が ありません</div>')
    + '</div>';
  go('author');
  setUrl('/member/u/' + author);
}

/* ============ ＋ボタン（ファイル / フォルダ / データベース） ============ */
function showAdd(){
  $('modalBody').innerHTML =
      '<h1 style="font-size:18px;margin-bottom:12px">なにを 作りますか？</h1>'
    + '<div class="card" style="cursor:pointer" onclick="addFile()">'
    + '<b>📄 ファイル</b><div style="font-size:12px;color:var(--muted)">'
    + 'プログラムを 書く ところ。名前の さいごに .py や .html を つけます'
    + '（つけないと .txt になります）</div></div>'
    + '<div class="card" style="cursor:pointer" onclick="addFolder()">'
    + '<b>📁 フォルダ</b><div style="font-size:12px;color:var(--muted)">'
    + 'ファイルを まとめる 入れもの</div></div>'
    + '<div class="card" style="cursor:pointer" onclick="closeModal();openDb()">'
    + '<b>🗄 データベース</b><div style="font-size:12px;color:var(--muted)">'
    + 'ひょうを つくって、データを ためられます</div></div>'
    + '<div class="row" style="margin-top:8px">'
    + '<button class="btn gh" onclick="closeModal()">やめる</button></div>';
  $('modal').classList.add('on');
}

function addFolder(){
  closeModal();
  var d = prompt('フォルダの 名前\n\n例: lib / data');
  if (!d) return;
  d = d.trim().replace(/^\/+|\/+$/g, '');
  if (!/^[A-Za-z0-9_\-\/]{1,40}$/.test(d)){
    alert('英数字と _ - / だけで つけてください'); return; }
  var f = prompt('その中に つくる ファイルの 名前\n\n例: utils.py');
  if (!f) return;
  f = f.trim();
  if (f.indexOf('.') < 0) f += '.txt';
  var p = d + '/' + f;
  if (!/^[A-Za-z0-9_\-.\/]{1,60}$/.test(p) || p.indexOf('..') >= 0){
    alert('使えない 名前です'); return; }
  if (FILES.some(function(x){ return x.path === p; })){
    alert('同じ 名前が あります'); return; }
  if (FILES[CUR]) FILES[CUR].content = $('eCode').value;
  FILES.push({path:p, content:'', kind:kindOf(p), is_entry:0});
  pickFile(FILES.length - 1);
}

/* ============ データベース（表をつくる） ============ */
var TABLES = [];
var TCUR = 0;

function openDb(){
  closeModal();
  drawDb();
  $('modal').classList.add('on');
}

function drawDb(){
  var h = '<h1 style="font-size:18px;margin-bottom:4px">データベース</h1>'
    + '<p class="lead">ひょうを つくると、Python からも HTML からも 使えます。</p>';
  if (!TABLES.length){
    h += '<div class="empty">まだ ひょうが ありません</div>';
  } else {
    h += '<div class="row">' + TABLES.map(function(t,i){
      return '<button class="btn ' + (i===TCUR?'':'gh') + ' sm" '
        + 'onclick="TCUR=' + i + ';drawDb()">' + K(t.name) + '</button>'; }).join('')
      + '</div>';
    var t = TABLES[TCUR];
    if (t){
      h += '<div style="overflow-x:auto"><table class="otbl" style="width:100%;'
        + 'border-collapse:collapse;font-size:12.5px"><thead><tr>'
        + t.cols.map(function(c,ci){
            return '<th style="text-align:left;padding:6px;border-bottom:1px solid #dde5ea;'
              + 'font-size:11px;color:#7a8794">' + K(c.name)
              + '<div style="font-weight:400">' + c.type + '</div></th>'; }).join('')
        + '<th style="padding:6px"></th></tr></thead><tbody>'
        + t.rows.map(function(r,ri){
            return '<tr>' + t.cols.map(function(c,ci){
              return '<td style="padding:3px;border-bottom:1px solid #eef3f5">'
                + '<input class="inp" style="padding:5px 7px;font-size:12.5px" '
                + 'value="' + K(r[ci]==null?'':r[ci]) + '" '
                + 'oninput="TABLES[' + TCUR + '].rows[' + ri + '][' + ci + ']=this.value">'
                + '</td>'; }).join('')
              + '<td style="padding:3px"><button class="btn gh sm" '
              + 'onclick="delRow(' + ri + ')">×</button></td></tr>'; }).join('')
        + '</tbody></table></div>'
        + '<div class="row" style="margin-top:8px">'
        + '<button class="btn gh sm" onclick="addRow()">＋ 行</button>'
        + '<button class="btn gh sm" onclick="addCol()">＋ 列</button>'
        + '<button class="btn gh sm" onclick="delTable()">この ひょうを けす</button></div>'
        + '<div class="card" style="margin-top:8px">'
        + '<label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px">'
        + '<input type="checkbox" ' + (t.shared ? 'checked ' : '')
        + 'onchange="TABLES[' + TCUR + '].shared = this.checked ? 1 : 0">'
        + '<span><b>みんなで 書きこめる ようにする</b>'
        + '<div style="font-size:11.5px;color:var(--muted)">'
        + 'あそんだ 人が データを ふやせます。図鑑や 記録に つかえます。'
        + '書いた 人の 名前が のこります。</div></span></label></div>';
    }
  }
  h += '<div class="row" style="margin-top:14px">'
    + '<button class="btn" onclick="addTable()">＋ ひょうを つくる</button>'
    + '<button class="btn gh" onclick="closeModal();drawTree()">とじる</button></div>';
  $('modalBody').innerHTML = h;
}

function addTable(){
  var n = prompt('ひょうの 名前（英字ではじめる）\n\n例: friends / kaimono');
  if (!n) return;
  n = n.trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,30}$/.test(n)){
    alert('英字で はじめて、英数字と _ だけに してください'); return; }
  if (TABLES.some(function(t){ return t.name === n; })){
    alert('同じ 名前が あります'); return; }
  TABLES.push({name:n, cols:[{name:'name', type:'TEXT'}], rows:[], shared:0});
  TCUR = TABLES.length - 1;
  drawDb();
}
function addCol(){
  var n = prompt('列の 名前（英字ではじめる）\n\n例: namae / tensu');
  if (!n) return;
  n = n.trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,30}$/.test(n)){ alert('英字で はじめてください'); return; }
  var t = prompt('しゅるい\n\nTEXT = もじ\nINTEGER = せいすう\nREAL = しょうすう', 'TEXT');
  t = (t||'TEXT').toUpperCase();
  if (['TEXT','INTEGER','REAL'].indexOf(t) < 0) t = 'TEXT';
  TABLES[TCUR].cols.push({name:n, type:t});
  TABLES[TCUR].rows.forEach(function(r){ r.push(''); });
  drawDb();
}
function addRow(){
  TABLES[TCUR].rows.push(TABLES[TCUR].cols.map(function(){ return ''; }));
  drawDb();
}
function delRow(i){ TABLES[TCUR].rows.splice(i,1); drawDb(); }
function delTable(){
  if (!confirm(TABLES[TCUR].name + ' を けしますか？')) return;
  TABLES.splice(TCUR,1); TCUR = 0; drawDb();
}

/* 表の定義から SQL を組み立てる（動かすときに使う） */
function tablesToSql(tables){
  var out = [];
  (tables||[]).forEach(function(t){
    out.push('CREATE TABLE ' + t.name + '(' +
      t.cols.map(function(c){ return c.name + ' ' + c.type; }).join(', ') + ');');
    t.rows.forEach(function(r){
      var vals = t.cols.map(function(c, i){
        var v = r[i];
        if (v === '' || v == null) return 'NULL';
        if (c.type === 'TEXT') return "'" + String(v).replace(/'/g, "''") + "'";
        return isNaN(Number(v)) ? "'" + String(v).replace(/'/g,"''") + "'" : Number(v);
      });
      out.push('INSERT INTO ' + t.name + ' VALUES(' + vals.join(', ') + ');');
    });
  });
  return out.join('\n');
}

/* ============ 見本からはじめる ============ */
async function showSamples(){
  var d = await J('/api/mp/samples');
  if (!d.ok){ newBlank(); return; }
  $('modalBody').innerHTML =
      '<h1 style="font-size:18px;margin-bottom:4px">なにから はじめますか？</h1>'
    + '<p class="lead">見本を えらぶと、その つづきから 作れます。'
    + '書きかえて 自分の ものに してください。</p>'
    + d.samples.map(function(x){
        var D = ['','かんたん','ふつう','むずかしい'][x.difficulty||1];
        return '<div class="card" style="cursor:pointer" onclick="useSample('+x.id+')">'
          + '<b>' + K(x.title) + '</b> <span class="tag d' + (x.difficulty||1) + '">'
          + D + '</span>'
          + '<div style="font-size:12px;color:var(--muted);margin-top:3px">'
          + K(x.summary||'') + '</div></div>'; }).join('')
    + '<div class="card" style="cursor:pointer;border-style:dashed" onclick="newBlank()">'
    + '<b>📄 白紙からはじめる</b><div style="font-size:12px;color:var(--muted)">'
    + '自分で ぜんぶ 書きたい人へ</div></div>'
    + '<div class="row" style="margin-top:8px">'
    + '<button class="btn gh" onclick="closeModal()">やめる</button></div>';
  $('modal').classList.add('on');
}

async function useSample(sid){
  var d = await J('/api/mp/sample/' + sid);
  if (!d.ok){ alert(d.error); return; }
  closeModal();
  APP = {id:null};
  FILES = d.files.map(function(f){ return {path:f.path, content:f.content,
    kind:f.kind, is_entry:f.is_entry}; });
  TABLES = [];
  DB = d.db || {name:'app.db', schema:'', seed:''};
  CUR = 0;
  $('eTitle').value = d.title + ' を つくってみた';
  $('eSummary').value = ''; $('eTags').value = d.tags || '';
  $('eDiff').value = String(d.difficulty || 1);
  $('eDel').style.display = 'none';
  $('eMsg').textContent = '見本から はじめました。書きかえて 保存してください';
  $('eOut').textContent = ''; $('preview').style.display = 'none';
  pickFile(0, true); go('edit');
}

/* ============ 複数ファイル対応 ============ */
var FILES = [], CUR = 0;
var DB = {name:'app.db', schema:'', seed:''};
var ICON = {py:'🐍', html:'📄', css:'🎨', js:'⚡', json:'📋',
            md:'📝', txt:'📃', csv:'📊'};

function kindOf(p){
  var e = p.indexOf('.') >= 0 ? p.split('.').pop().toLowerCase() : 'txt';
  return ({htm:'html'})[e] || e;
}
function fileIcon(p){ return ICON[kindOf(p)] || '📃'; }

function drawTree(){
  var el = $('fileTree');
  if (!FILES.length){ el.innerHTML = '<div class="side-note">ファイルなし</div>'; return; }
  var groups = {};
  FILES.forEach(function(f, i){
    var parts = f.path.split('/');
    var dir = parts.length > 1 ? parts.slice(0, -1).join('/') : '';
    (groups[dir] = groups[dir] || []).push({f:f, i:i, name:parts[parts.length-1]});
  });
  var html = '';
  Object.keys(groups).sort().forEach(function(dir){
    if (dir) html += '<div class="ffolder">📁 ' + K(dir) + '</div>';
    groups[dir].forEach(function(x){
      html += '<div class="ffile' + (x.i===CUR?' on':'') + (dir?' sub':'') + '" '
        + 'onclick="pickFile(' + x.i + ')"><span class="ic">' + fileIcon(x.f.path)
        + '</span><span style="overflow:hidden;text-overflow:ellipsis">'
        + K(x.name) + '</span>'
        + (x.f.is_entry ? '<span class="entry">最初</span>' : '') + '</div>';
    });
  });
  el.innerHTML = html;
  $('dbInfo').textContent = TABLES.length
    ? TABLES.map(function(t){ return t.name + '(' + t.rows.length + ')'; }).join(' ')
    : ((DB.schema || DB.seed) ? DB.name : 'なし');
}

function pickFile(i, keep){
  // keep が true のときは、今のテキストを書きもどさない。
  // 見本を読み込んだ直後など、テキストエリアが空のときに使う。
  if (!keep && FILES[CUR]) FILES[CUR].content = $('eCode').value;
  CUR = i;
  $('eCode').value = FILES[i].content || '';
  $('edPath').textContent = FILES[i].path;
  drawTree(); updateEntryBtn(); hlSync();
}

function addFile(){
  if (FILES[CUR]) FILES[CUR].content = $('eCode').value;
  var p = prompt('ファイルの名前\n\nフォルダに入れたいときは / を使います\n'
    + '例: utils.py / lib/tools.py / data/items.json');
  if (!p) return;
  p = p.trim().replace(/^\/+/, '');
  if (!/^[A-Za-z0-9_\-.\/]{1,60}$/.test(p) || p.indexOf('..') >= 0){
    alert('使えない名前です。英数字と _ - . / だけにしてください'); return; }
  if (FILES.some(function(f){ return f.path === p; })){ alert('同じ名前があります'); return; }
  FILES.push({path:p, content:'', kind:kindOf(p), is_entry:0});
  pickFile(FILES.length - 1);
}

function renameFile(){
  if (!FILES[CUR]) return;
  var p = prompt('新しい名前', FILES[CUR].path);
  if (!p) return;
  p = p.trim().replace(/^\/+/, '');
  if (!/^[A-Za-z0-9_\-.\/]{1,60}$/.test(p) || p.indexOf('..') >= 0){
    alert('使えない名前です'); return; }
  if (FILES.some(function(f,i){ return i!==CUR && f.path===p; })){
    alert('同じ名前があります'); return; }
  FILES[CUR].path = p; FILES[CUR].kind = kindOf(p);
  $('edPath').textContent = p; drawTree(); updateEntryBtn();
}

function removeFile(){
  if (FILES.length <= 1){ alert('最後の1つは消せません'); return; }
  if (!confirm(FILES[CUR].path + ' を消しますか？')) return;
  FILES.splice(CUR, 1); CUR = 0; pickFile(0);
}

function openDb(){
  $('modalBody').innerHTML =
      '<h1 style="font-size:18px;margin-bottom:4px">データベース</h1>'
    + '<p class="lead">表を作っておくと、Python からも HTML からも使えます。</p>'
    + '<label class="lb">名前</label>'
    + '<input class="inp" id="dbName" value="' + K(DB.name) + '" style="margin-bottom:10px">'
    + '<label class="lb">表を作る（CREATE TABLE）</label>'
    + '<textarea class="ta" id="dbSchema" style="min-height:90px;'
    + 'font-family:ui-monospace,monospace;font-size:12.5px;margin-bottom:10px">'
    + K(DB.schema) + '</textarea>'
    + '<label class="lb">はじめに入れるデータ（INSERT）</label>'
    + '<textarea class="ta" id="dbSeed" style="min-height:80px;'
    + 'font-family:ui-monospace,monospace;font-size:12.5px;margin-bottom:12px">'
    + K(DB.seed) + '</textarea>'
    + '<div class="row"><button class="btn" onclick="saveDb()">決定</button>'
    + '<button class="btn gh" onclick="closeModal()">やめる</button></div>';
  $('modal').classList.add('on');
}
function closeModal(){ $('modal').classList.remove('on'); }
function saveDb(){
  DB.name = $('dbName').value.trim() || 'app.db';
  DB.schema = $('dbSchema').value; DB.seed = $('dbSeed').value;
  closeModal(); drawTree();
}

function newApp(){
  if (!ME){ go('login'); return; }
  showSamples();
}

function newBlank(){
  closeModal();
  APP = {id:null};
  TABLES = [];
  FILES = [{path:'main.py', content:'# ここに Python を書いてみよう\nprint("こんにちは")\n',
            kind:'py', is_entry:1}];
  DB = {name:'app.db', schema:'', seed:''}; CUR = 0;
  ['eTitle','eSummary','eTags'].forEach(function(i){ $(i).value=''; });
  $('eDiff').value='1'; $('eDel').style.display='none';
  $('eMsg').textContent=''; $('eOut').textContent='';
  $('preview').style.display='none';
  pickFile(0, true); go('edit');
  var d = restore();
  if (d && !d.id && d.files && confirm('前に書きかけのものがあります。もどしますか？')){
    FILES = d.files; DB = d.db || DB;
    $('eTitle').value=d.title||''; $('eSummary').value=d.summary||'';
    $('eTags').value=d.tags||''; $('eDiff').value=d.diff||'1';
    CUR = 0; pickFile(0, true);
  }
}

async function editApp(id){
  var a = (await J('/api/mp/app/'+id)).app;
  if (!a){ alert('開けません'); return; }
  var f = await J('/api/mp/app/'+id+'/files');
  APP = {id:id};
  FILES = (f.files||[]).map(function(x){
    return {path:x.path, content:x.content, kind:x.kind, is_entry:x.is_entry}; });
  if (!FILES.length) FILES = [{path:'main.py', content:'', kind:'py', is_entry:1}];
  DB = (f.dbs && f.dbs[0]) ? {name:f.dbs[0].name, schema:f.dbs[0].schema, seed:f.dbs[0].seed}
                           : {name:'app.db', schema:'', seed:''};
  var tb = await J('/api/mp/app/'+id+'/tables');
  TABLES = (tb.ok ? tb.tables : []) || [];
  TCUR = 0;
  CUR = 0;
  $('eTitle').value=a.title; $('eSummary').value=a.summary||'';
  $('eTags').value=a.tags||''; $('eDiff').value=String(a.difficulty||1);
  $('eDel').style.display='';
  $('eMsg').textContent = a.status==='published' ? '公開中' : '下書き';
  pickFile(0, true); go('edit');
  setUrl('/member/edit/' + id);
}

async function saveApp(pub, confirmed){
  if (FILES[CUR]) FILES[CUR].content = $('eCode').value;
  if (!FILES.some(function(f){ return f.is_entry; })) FILES[0].is_entry = 1;
  var pick = function(k){
    var x = FILES.filter(function(f){ return f.kind===k; })[0];
    return x ? x.content : ''; };
  var d = await P('/api/mp/app', {
    id:APP.id, title:$('eTitle').value.trim(), summary:$('eSummary').value.trim(),
    tags:$('eTags').value.trim(), difficulty:parseInt($('eDiff').value),
    publish:pub, confirmed:confirmed,
    code:pick('py'), html:pick('html'), css:pick('css'), js:pick('js'),
    sql:DB.schema || ''});
  var m = $('eMsg');
  if (!d.ok){
    m.style.color='#c0453b'; m.textContent = d.error;
    if (d.need_agree) setTimeout(function(){ go('rules'); }, 1200);
    if (d.need_confirm && confirm(d.error + '\n\nそれでも公開しますか？')) saveApp(pub, true);
    return;
  }
  APP.id = d.id; $('eDel').style.display='';
  var f = await P('/api/mp/app/'+APP.id+'/files', {files:FILES});
  if (!f.ok){ m.style.color='#c0453b'; m.textContent = f.error; return; }
  var t = await P('/api/mp/app/'+APP.id+'/tables', {tables:TABLES});
  if (!t.ok){ m.style.color='#c0453b'; m.textContent = t.error; return; }
  clearStash();
  m.style.color='#0e7490';
  m.textContent = (d.status==='published' ? '公開しました' : '下書きに保存しました')
    + '（' + f.count + 'ファイル）';
  await refreshMe();
}

async function showVersions(){
  if (!APP.id){ alert('まだ保存していません'); return; }
  var d = await J('/api/mp/app/'+APP.id+'/versions');
  if (!d.ok){ alert(d.error); return; }
  $('modalBody').innerHTML =
      '<h1 style="font-size:18px;margin-bottom:4px">前のに もどす</h1>'
    + '<p class="lead">保存するたびに残しています。最大20回ぶん。</p>'
    + (d.versions.length ? d.versions.map(function(v){
        return '<div class="card" style="display:flex;align-items:center;gap:10px">'
          + '<div style="flex:1"><b style="font-size:12.5px">'+K(v.saved_at)+'</b>'
          + '<div style="font-size:11.5px;color:var(--muted)">'+v.files+'ファイル'
          + (v.note ? ' · '+K(v.note) : '')+'</div></div>'
          + '<button class="btn gh sm" onclick="doRestore('+v.id+')">もどす</button></div>';
      }).join('') : '<div class="empty">まだ履歴がありません</div>')
    + '<div class="row" style="margin-top:10px">'
    + '<button class="btn gh" onclick="closeModal()">とじる</button></div>';
  $('modal').classList.add('on');
}
async function doRestore(vid){
  if (!confirm('この状態にもどしますか？\n（いまの状態も履歴に残ります）')) return;
  var d = await P('/api/mp/app/'+APP.id+'/restore/'+vid);
  if (!d.ok){ alert(d.error); return; }
  closeModal(); editApp(APP.id);
}

function stash(){
  if (!$('p-edit').classList.contains('on')) return;
  if (FILES[CUR]) FILES[CUR].content = $('eCode').value;
  try {
    localStorage.setItem('mp_draft', JSON.stringify({
      id:APP.id, files:FILES, db:DB, title:$('eTitle').value,
      summary:$('eSummary').value, tags:$('eTags').value,
      diff:$('eDiff').value, at:Date.now()}));
  } catch(e){}
}

async function runFiles(files, db, outEl, frameEl){
  outEl.innerHTML = '';
  var find = function(k){ return files.filter(function(f){ return f.kind===k; }); };
  var htmls=find('html'), csss=find('css'), jss=find('js'), pys=find('py');

  if (htmls.length){
    frameEl.style.display = '';
    var head = csss.map(function(f){ return '<style>'+f.content+'</style>'; }).join('');
    var tail = jss.map(function(f){
      return '<scr'+'ipt>try{'+f.content+'}catch(e){'
        + 'document.body.innerHTML+="<pre style=color:red>"+e+"</pre>"}</scr'+'ipt>';
    }).join('');
    var eh = htmls.filter(function(f){ return f.is_entry; })[0] || htmls[0];
    var body = eh.content;
    if (/^\s*<!DOCTYPE/i.test(body) || /^\s*<html/i.test(body)){
      if (head) body = body.replace(/<\/head>/i, head + '</head>');
      if (tail) body = body.replace(/<\/body>/i, tail + '</body>');
      frameEl.srcdoc = body;
    } else {
      frameEl.srcdoc = '<style>body{font-family:sans-serif;padding:12px}</style>'
        + head + body + tail;
    }
    outWrite(outEl, '画面に表示しました（' + eh.path + '）', 'ok');
    if (frameEl.id === 'preview' && $('prevBar')) $('prevBar').style.display = 'flex';
  } else {
    frameEl.style.display = 'none';
    if (frameEl.id === 'preview' && $('prevBar')) $('prevBar').style.display = 'none';
  }

  if (db && (db.schema || db.seed))
    await runSql((db.schema||'') + '\n' + (db.seed||''), outEl);

  if (pys.length){
    outWrite(outEl, '── Python ──');
    try {
      if (!PYO){
        outWrite(outEl, 'Python を用意しています…（初回だけ時間がかかります）');
        PYO = await loadPyodide();
      }
      PYO.setStdout({batched:function(s){ outWrite(outEl, s); }});
      PYO.setStderr({batched:function(s){ outWrite(outEl, s, 'e'); }});
      try { PYO.FS.mkdir('/work'); } catch(e){}
      files.forEach(function(f){
        var parts = f.path.split('/'), dir = '/work';
        for (var i = 0; i < parts.length - 1; i++){
          dir += '/' + parts[i];
          try { PYO.FS.mkdir(dir); } catch(e){}
        }
        try { PYO.FS.writeFile('/work/' + f.path, f.content); } catch(e){}
      });
      await PYO.runPythonAsync('import sys, os\nos.chdir("/work")\n'
        + 'if "/work" not in sys.path: sys.path.insert(0, "/work")\n');
      setupInput();
      // 表があれば Python の sqlite3 からも触れるようにしておく
      if (db && db.schema){
        PYO.globals.set('_mp_sql', db.schema);
        PYO.globals.set('_mp_dbname', db.name || 'app.db');
        await PYO.runPythonAsync(
          'import sqlite3\n' +
          '_c = sqlite3.connect(_mp_dbname)\n' +
          '_c.executescript(_mp_sql)\n' +
          '_c.commit()\n' +
          '_c.close()\n');
      }
      if (window.MP_APP_ID){
        PYO.globals.set('_mp_appid', window.MP_APP_ID);
        try { PYO.FS.writeFile('/work/mirai.py', MIRAI_PY); } catch(e){}
      }
      var ep = pys.filter(function(f){ return f.is_entry; })[0] || pys[0];
      outWrite(outEl, '（' + ep.path + ' を動かします）');
      await PYO.runPythonAsync(ep.content);
    } catch(e){
      outWrite(outEl, String(e).split('\n').slice(-5).join('\n'), 'e');
    }
  }
  if (!outEl.innerHTML) outWrite(outEl, '（何も書かれていません）');
}

async function runCode(){
  window.MP_APP_ID = APP.id;
  if (FILES[CUR]) FILES[CUR].content = $('eCode').value;
  var risks = checkRisk(FILES);
  if (risks.length){
    if (!confirm('⚠ 気を つけて\n\n' + risks.join('\n\n')
        + '\n\nそれでも うごかしますか？')) return;
  }
  var sql = tablesToSql(TABLES);
  await runFiles(FILES, {name:DB.name, schema:sql || DB.schema, seed:sql ? '' : DB.seed},
                 $('eOut'), $('preview'));
  // エラーが出ていなければ「うごいた」とみなす
  var bad = $('eOut').querySelectorAll('.e').length;
  reportRan(bad === 0);
}
async function bigView(){
  var a = window.VIEW_APP || {};
  window.MP_APP_ID = a.id;
  var fs = (window.VIEW_FILES || []);
  $('bigTitle').textContent = a.title || 'けっか';
  $('bigWrap').classList.add('on');
  $('bigOut').classList.remove('on');
  $('bigOut').innerHTML = '';
  await runFiles(fs, window.VIEW_DB, $('bigOut'), $('bigFrame'));
  var hasHtml = fs.some(function(f){
    return f.kind === 'html' && (f.content||'').trim(); });
  if (!hasHtml && $('bigOut').innerHTML) $('bigOut').classList.add('on');
}

function runView(){
  var a = window.VIEW_APP || {};
  window.MP_APP_ID = a.id;
  var fs = (window.VIEW_FILES || []).map(function(f){
    return {path:f.path, content:f.content, kind:f.kind, is_entry:f.is_entry}; });
  runFiles(fs, window.VIEW_DB, $('vOut'), $('vPreview'));
}

setInterval(stash, 15000);
window.addEventListener('beforeunload', stash);
document.addEventListener('visibilitychange', stash);


// ブラウザの「戻る」に合わせる
window.addEventListener('popstate', function(){
  var m = location.pathname.match(/^\/member\/program\/[^/]+\/(\d+)/);
  if (m){ viewApp(parseInt(m[1])); return; }
  m = location.pathname.match(/^\/member\/edit\/(\d+)/);
  if (m){ editApp(parseInt(m[1])); return; }
  var p = {'/member':'home', '/member/mine':'mine', '/member/rules':'rules',
           '/member/login':'login'}[location.pathname];
  if (p) go(p);
});

// URL で作品が指定されていれば、それを開く
var OPEN_APP = {% if open_app %}{{ open_app }}{% else %}null{% endif %};
var OPEN_EDIT = {% if open_edit %}{{ open_edit }}{% else %}null{% endif %};

drawCat();
refreshMe().then(function(){
  gate();
  if (OPEN_APP && ME) viewApp(OPEN_APP);
  else if (OPEN_EDIT && ME) editApp(OPEN_EDIT);
  else if (ME){
    var p = {'/member/mine':'mine', '/member/rules':'rules'}[location.pathname];
    if (p) go(p);
  }
});
