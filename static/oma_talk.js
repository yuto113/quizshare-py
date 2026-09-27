/* ============ 💬 つぶやき（X風） ============ */
var TK_MODE = 'all', TK_GID = null, TK_TAG = null, TK_LAST = null;

function avHtml(m, small){
  var kind = m.avatar_kind || 'letter';
  var col = m.color || pickColor(m.member_id || m.nickname || 'x');
  var cls = 'av' + (small ? ' sm' : '');
  var on = 'onclick="goOwner(\'' + K(m.member_id||'') + '\');event.stopPropagation()"';
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
  var C = ['#0891b2','#7c3aed','#db2777','#3d8460','#ea580c','#0284c7',
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
    + '<div class="tk-hd"><b onclick="goOwner(\'' + K(src.member_id) + '\')">'
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
