var BLK_LANGS = [
  ['py',   '🐍 Python'],
  ['html', '📄 HTML'],
  ['js',   '⚡ JavaScript'],
  ['sql',  '🗄 SQL']
];

/* [キー] = {lbl, cls, cat, f:['なまえ|ラベル|しょきち'], open:段さげ} */
var BLK_DEF = {

  /* ===== Python ===== */
  py_print:   {lg:'py', cat:'きほん', lbl:'💬 かく', f:['text|かく もじ|こんにちは']},
  py_printv:  {lg:'py', cat:'きほん', lbl:'💬 へんすうを かく', f:['name|なまえ|kazu']},
  py_var:     {lg:'py', cat:'きほん', lbl:'📦 いれる', f:['name|なまえ|kazu','val|なかみ|10']},
  py_input:   {lg:'py', cat:'きほん', lbl:'⌨️ きく', f:['name|なまえ|namae','ask|しつもん|なまえは？']},
  py_inputn:  {lg:'py', cat:'きほん', lbl:'🔢 かずを きく', f:['name|なまえ|kazu','ask|しつもん|すうじを どうぞ']},
  py_comment: {lg:'py', cat:'きほん', lbl:'# メモ', cls:'kw-com', f:['text|メモ|ここで なにを するか']},
  py_blank:   {lg:'py', cat:'きほん', lbl:'␣ あきぎょう', f:[]},

  py_for:     {lg:'py', cat:'くりかえし', lbl:'🔁 ◯かい くりかえす', cls:'kw-for', f:['n|かいすう|5'], open:1},
  py_forrange:{lg:'py', cat:'くりかえし', lbl:'🔁 ◯から◯まで', cls:'kw-for', f:['a|から|1','b|まで|10'], open:1},
  py_forlist: {lg:'py', cat:'くりかえし', lbl:'📋 ひとつずつ', cls:'kw-for', f:['name|なまえ|x','list|リスト|kudamono'], open:1},
  py_while:   {lg:'py', cat:'くりかえし', lbl:'🔄 …のあいだ', cls:'kw-for', f:['cond|じょうけん|kazu < 10'], open:1},
  py_break:   {lg:'py', cat:'くりかえし', lbl:'🛑 とめる', cls:'kw-for', f:[]},
  py_continue:{lg:'py', cat:'くりかえし', lbl:'⏭ つぎへ', cls:'kw-for', f:[]},

  py_if:      {lg:'py', cat:'もし', lbl:'❓ もし …なら', cls:'kw-if', f:['cond|じょうけん|kazu > 5'], open:1},
  py_elif:    {lg:'py', cat:'もし', lbl:'❔ そうでなく …なら', cls:'kw-if', f:['cond|じょうけん|kazu == 5'], open:1},
  py_else:    {lg:'py', cat:'もし', lbl:'↔️ そうでなければ', cls:'kw-if', f:[], open:1},

  py_calc:    {lg:'py', cat:'けいさん', lbl:'🔢 けいさん', f:['name|なまえ|kotae','expr|しき|1 + 2']},
  py_add:     {lg:'py', cat:'けいさん', lbl:'➕ ふやす', f:['name|なまえ|kazu','n|いくつ|1']},
  py_random:  {lg:'py', cat:'けいさん', lbl:'🎲 ランダムな かず', f:['name|なまえ|deta','from|から|1','to|まで|6']},
  py_choice:  {lg:'py', cat:'けいさん', lbl:'🎯 ランダムに えらぶ', f:['name|なまえ|atari','list|リスト|kudamono']},
  py_round:   {lg:'py', cat:'けいさん', lbl:'🔻 しほんごにゅう', f:['name|なまえ|kotae','val|なに|3.14159','n|けた|2']},
  py_abs:     {lg:'py', cat:'けいさん', lbl:'± ぜったいち', f:['name|なまえ|kotae','val|なに|-5']},

  py_list:    {lg:'py', cat:'まとめる', lbl:'📚 リスト', f:['name|なまえ|kudamono','items|なかみ（コンマ）|りんご,みかん,ぶどう']},
  py_append:  {lg:'py', cat:'まとめる', lbl:'➕ リストに たす', f:['name|リスト|kudamono','val|なに|バナナ']},
  py_len:     {lg:'py', cat:'まとめる', lbl:'📏 かず を しらべる', f:['name|なまえ|n','list|なに|kudamono']},
  py_sort:    {lg:'py', cat:'まとめる', lbl:'🔤 ならべかえる', f:['name|リスト|kazu_list']},
  py_sum:     {lg:'py', cat:'まとめる', lbl:'➕ ぜんぶ たす', f:['name|なまえ|gokei','list|リスト|kazu_list']},
  py_maxmin:  {lg:'py', cat:'まとめる', lbl:'⬆ いちばん おおきい', f:['name|なまえ|saidai','list|リスト|kazu_list']},
  py_dict:    {lg:'py', cat:'まとめる', lbl:'🗂 じしょ', f:['name|なまえ|hito','items|なかみ（なまえ:あたい）|namae:たろう,toshi:12']},
  py_dictget: {lg:'py', cat:'まとめる', lbl:'🔍 じしょから とる', f:['name|なまえ|x','dict|じしょ|hito','key|かぎ|namae']},

  py_str:     {lg:'py', cat:'もじ', lbl:'🔤 もじを つなぐ', f:['name|なまえ|bun','a|まえ|こんにちは','b|うしろ|namae']},
  py_upper:   {lg:'py', cat:'もじ', lbl:'🔠 大文字に', f:['name|なまえ|s','val|もじ|hello']},
  py_replace: {lg:'py', cat:'もじ', lbl:'🔁 もじを かえる', f:['name|なまえ|s','val|もとの もじ|bun','a|さがす|あ','b|かえる|い']},
  py_split:   {lg:'py', cat:'もじ', lbl:'✂️ もじを わける', f:['name|なまえ|parts','val|もじ|bun','sep|くぎり|,']},
  py_tostr:   {lg:'py', cat:'もじ', lbl:'🔤 もじに する', f:['name|なまえ|s','val|なに|kazu']},
  py_toint:   {lg:'py', cat:'もじ', lbl:'🔢 かずに する', f:['name|なまえ|n','val|なに|s']},

  py_def:     {lg:'py', cat:'かたまり', lbl:'🧰 しごとを つくる', cls:'kw-if', f:['name|なまえ|aisatsu','args|うけとる もの|namae'], open:1},
  py_return:  {lg:'py', cat:'かたまり', lbl:'↩️ かえす', f:['val|なに|kotae']},
  py_call:    {lg:'py', cat:'かたまり', lbl:'▶️ しごとを よぶ', f:['name|なまえ|aisatsu','args|わたす もの|"みらい"']},
  py_import:  {lg:'py', cat:'かたまり', lbl:'📥 とりこむ', f:['name|なまえ|random']},
  py_try:     {lg:'py', cat:'かたまり', lbl:'🛡 やってみる', cls:'kw-if', f:[], open:1},
  py_except:  {lg:'py', cat:'かたまり', lbl:'⚠️ だめだったら', cls:'kw-if', f:[], open:1},

  py_sleep:   {lg:'py', cat:'そのた', lbl:'⏱ まつ', f:['sec|びょう|1']},
  py_mirai_add:{lg:'py', cat:'そのた', lbl:'💾 データを ためる', f:['table|ひょう|zukan','vals|なかみ（なまえ=あたい）|namae="カブトムシ"']},
  py_mirai_all:{lg:'py', cat:'そのた', lbl:'📂 データを よむ', f:['name|なまえ|rows','table|ひょう|zukan']},

  /* ===== HTML ===== */
  h_h1:       {lg:'html', cat:'もじ', lbl:'📰 大きな みだし', f:['text|もじ|こんにちは']},
  h_h2:       {lg:'html', cat:'もじ', lbl:'📄 みだし', f:['text|もじ|しょうかい']},
  h_p:        {lg:'html', cat:'もじ', lbl:'📝 ぶんしょう', f:['text|もじ|ここに 文を 書きます']},
  h_br:       {lg:'html', cat:'もじ', lbl:'↵ かいぎょう', f:[]},
  h_hr:       {lg:'html', cat:'もじ', lbl:'➖ よこせん', f:[]},
  h_b:        {lg:'html', cat:'もじ', lbl:'🅱 ふとじ', f:['text|もじ|だいじ']},

  h_button:   {lg:'html', cat:'ぶひん', lbl:'🔘 ボタン', f:['text|もじ|おす','fn|おしたら よぶ|oshita']},
  h_input:    {lg:'html', cat:'ぶひん', lbl:'⌨️ 入力らん', f:['id|なまえ|namae','ph|うすい もじ|なまえを どうぞ']},
  h_inputn:   {lg:'html', cat:'ぶひん', lbl:'🔢 かず入力', f:['id|なまえ|kazu','val|さいしょ|0']},
  h_select:   {lg:'html', cat:'ぶひん', lbl:'▼ えらぶ', f:['id|なまえ|erabu','items|えらぶ もの|あか,あお,きいろ']},
  h_check:    {lg:'html', cat:'ぶひん', lbl:'☑ チェック', f:['id|なまえ|ok','text|もじ|どういする']},
  h_textarea: {lg:'html', cat:'ぶひん', lbl:'📋 おおきな入力', f:['id|なまえ|memo','ph|うすい もじ|じゆうに どうぞ']},

  h_img:      {lg:'html', cat:'みため', lbl:'🖼 え', f:['src|URL|https://placehold.jp/300x200.png','alt|せつめい|え']},
  h_div:      {lg:'html', cat:'みため', lbl:'📦 いれもの', f:['id|なまえ|box'], open:1},
  h_span:     {lg:'html', cat:'みため', lbl:'🏷 ふだ', f:['id|なまえ|ten','text|もじ|0']},
  h_canvas:   {lg:'html', cat:'みため', lbl:'🎨 おえかき板', f:['id|なまえ|c','w|よこ|300','h|たて|200']},
  h_ul:       {lg:'html', cat:'みため', lbl:'📋 かじょうがき', f:['items|なかみ（コンマ）|いち,に,さん']},
  h_table:    {lg:'html', cat:'みため', lbl:'📊 ひょう', f:['head|みだし（コンマ）|なまえ,てん','rows|なかみ（; でぎょう）|たろう,90;はなこ,85']},
  h_a:        {lg:'html', cat:'みため', lbl:'🔗 リンク', f:['text|もじ|ここを おす','url|いき先|https://example.com']},
  h_style:    {lg:'html', cat:'みため', lbl:'🎨 いろを きめる', f:['sel|なに|body','prop|なにを|background','val|どう|#f0f9ff']},

  /* ===== JavaScript ===== */
  j_log:      {lg:'js', cat:'きほん', lbl:'💬 かく', f:['text|もじ|こんにちは']},
  j_alert:    {lg:'js', cat:'きほん', lbl:'🔔 しらせる', f:['text|もじ|やったね']},
  j_var:      {lg:'js', cat:'きほん', lbl:'📦 いれる', f:['name|なまえ|kazu','val|なかみ|10']},
  j_comment:  {lg:'js', cat:'きほん', lbl:'// メモ', cls:'kw-com', f:['text|メモ|ここで なにを するか']},

  j_get:      {lg:'js', cat:'がめん', lbl:'🎯 ぶひんを とる', f:['name|なまえ|el','id|なまえ|box']},
  j_settext:  {lg:'js', cat:'がめん', lbl:'✏️ もじを かえる', f:['id|なまえ|box','text|もじ|かわったよ']},
  j_gettext:  {lg:'js', cat:'がめん', lbl:'📖 入力を よむ', f:['name|なまえ|s','id|なまえ|namae']},
  j_setcolor: {lg:'js', cat:'がめん', lbl:'🎨 いろを かえる', f:['id|なまえ|box','col|いろ|#22d3ee']},
  j_setbg:    {lg:'js', cat:'がめん', lbl:'🖌 せなかの いろ', f:['col|いろ|#f0f9ff']},
  j_hide:     {lg:'js', cat:'がめん', lbl:'👻 かくす', f:['id|なまえ|box']},
  j_show:     {lg:'js', cat:'がめん', lbl:'👁 みせる', f:['id|なまえ|box']},
  j_add:      {lg:'js', cat:'がめん', lbl:'➕ うしろに たす', f:['id|なまえ|box','html|なかみ|<p>あたらしい</p>']},

  j_click:    {lg:'js', cat:'うごき', lbl:'👆 おされたら', cls:'kw-if', f:['id|ボタンの なまえ|btn'], open:1},
  j_fn:       {lg:'js', cat:'うごき', lbl:'🧰 しごとを つくる', cls:'kw-if', f:['name|なまえ|oshita'], open:1},
  j_timer:    {lg:'js', cat:'うごき', lbl:'⏰ ◯びょう ごとに', cls:'kw-for', f:['sec|びょう|1'], open:1},
  j_wait:     {lg:'js', cat:'うごき', lbl:'⏱ ◯びょう あとに', cls:'kw-for', f:['sec|びょう|1'], open:1},
  j_key:      {lg:'js', cat:'うごき', lbl:'⌨️ キーが おされたら', cls:'kw-if', f:[], open:1},

  j_if:       {lg:'js', cat:'もし', lbl:'❓ もし', cls:'kw-if', f:['cond|じょうけん|kazu > 5'], open:1},
  j_else:     {lg:'js', cat:'もし', lbl:'↔️ そうでなければ', cls:'kw-if', f:[], open:1},
  j_for:      {lg:'js', cat:'もし', lbl:'🔁 ◯かい くりかえす', cls:'kw-for', f:['n|かいすう|5'], open:1},

  j_random:   {lg:'js', cat:'けいさん', lbl:'🎲 ランダムな かず', f:['name|なまえ|deta','from|から|1','to|まで|6']},
  j_calc:     {lg:'js', cat:'けいさん', lbl:'🔢 けいさん', f:['name|なまえ|kotae','expr|しき|1 + 2']},
  j_num:      {lg:'js', cat:'けいさん', lbl:'🔢 かずに する', f:['name|なまえ|n','val|なに|s']},

  /* ===== SQL ===== */
  s_create:   {lg:'sql', cat:'ひょう', lbl:'🗄 ひょうを つくる', f:['name|なまえ|friends','cols|はしら（なまえ かた, …）|namae TEXT, toshi INTEGER']},
  s_insert:   {lg:'sql', cat:'ひょう', lbl:'➕ データを いれる', f:['name|ひょう|friends','vals|あたい|\'たろう\', 12']},
  s_select:   {lg:'sql', cat:'さがす', lbl:'🔍 ぜんぶ みる', f:['name|ひょう|friends']},
  s_selectw:  {lg:'sql', cat:'さがす', lbl:'🔍 じょうけんで さがす', f:['name|ひょう|friends','cond|じょうけん|toshi > 10']},
  s_order:    {lg:'sql', cat:'さがす', lbl:'🔤 ならべて みる', f:['name|ひょう|friends','col|なにで|toshi','dir|むき|DESC']},
  s_count:    {lg:'sql', cat:'さがす', lbl:'🔢 かぞえる', f:['name|ひょう|friends']},
  s_sum:      {lg:'sql', cat:'さがす', lbl:'➕ ごうけい', f:['name|ひょう|friends','col|なにを|toshi']},
  s_update:   {lg:'sql', cat:'なおす', lbl:'✏️ かきかえる', f:['name|ひょう|friends','set|なにを|toshi = 13','cond|どれを|namae = \'たろう\'']},
  s_delete:   {lg:'sql', cat:'なおす', lbl:'🗑 けす', f:['name|ひょう|friends','cond|どれを|toshi < 5']}
};

function addBlk(type){
  var d = BLK_DEF[type];
  if (!d) return;
  var v = {};
  (d.f || []).forEach(function(x){ var p = x.split('|'); v[p[0]] = p[2] || ''; });
  // まえの ブロックが「…なら」などなら、じどうで 1つ 下げる
  var lv = 0;
  if (BLK.length){
    var prev = BLK[BLK.length - 1];
    var pd = BLK_DEF[prev.type];
    lv = prev.lv + (pd && pd.open ? 1 : 0);
  }
  BLK.push({id: ++BLK_ID, type: type, v: v, lv: lv});
  drawBlk();
}

var BLK_LG = 'py';

function drawPalette(){
  var el = document.querySelector('.blk-palette');
  if (!el) return;
  var tabs = '<div class="blk-lgs">' + BLK_LANGS.map(function(l){
    return '<button class="blk-lg' + (l[0]===BLK_LG ? ' on' : '') + '" '
      + 'onclick="setBlkLang(\'' + l[0] + '\')">' + l[1] + '</button>';
  }).join('') + '</div>';

  var cats = {};
  Object.keys(BLK_DEF).forEach(function(k){
    var d = BLK_DEF[k];
    if (d.lg !== BLK_LG) return;
    (cats[d.cat] = cats[d.cat] || []).push([k, d]);
  });
  var body = Object.keys(cats).map(function(c){
    return '<div class="blk-cat">' + K(c) + '</div>'
      + cats[c].map(function(x){
          return '<div class="blk-item" onclick="addBlk(\'' + x[0] + '\')">'
            + K(x[1].lbl) + '</div>'; }).join('');
  }).join('');
  el.innerHTML = tabs + body;
}

function setBlkLang(lg){ BLK_LG = lg; drawPalette(); }

function drawBlk(){
  var el = $('blkList');
  if (!BLK.length){
    el.innerHTML = '<div class="blk-empty">左から ブロックを えらんで ください</div>';
    $('blkCode').textContent = '';
    return;
  }
  el.innerHTML = BLK.map(function(b, i){
    var d = BLK_DEF[b.type];
    if (!d) return '';
    var fields = (d.f || []).map(function(x){
      var p = x.split('|');
      var wide = ['text','expr','cond','items','ask','html','vals','cols',
                  'rows','head','set','url','src','args','sep'].indexOf(p[0]) >= 0;
      return '<span style="font-size:11px;color:#7a8794">' + K(p[1]) + '</span>'
        + '<input' + (wide ? ' class="wide"' : '') + ' value="' + K(b.v[p[0]] || '')
        + '" oninput="BLK[' + i + '].v[\'' + p[0] + '\']=this.value;blkCode()">';
    }).join('');
    return '<div class="blk ' + (d.cls||'') + ' lv' + Math.min(b.lv, 3) + '">'
      + '<span class="lbl">' + K(d.lbl) + '</span>' + fields
      + '<span class="sp"></span>'
      + '<span class="mv" onclick="blkIndent(' + i + ',-1)">◀</span>'
      + '<span class="mv" onclick="blkIndent(' + i + ',1)">▶</span>'
      + '<span class="mv" onclick="blkMove(' + i + ',-1)">▲</span>'
      + '<span class="mv" onclick="blkMove(' + i + ',1)">▼</span>'
      + '<span class="x" onclick="blkDel(' + i + ')">×</span></div>';
  }).join('');
  blkCode();
}

function blkMove(i, d){
  var j = i + d;
  if (j < 0 || j >= BLK.length) return;
  var t = BLK[i]; BLK[i] = BLK[j]; BLK[j] = t;
  drawBlk();
}
function blkIndent(i, d){
  BLK[i].lv = Math.max(0, Math.min(2, (BLK[i].lv || 0) + d));
  drawBlk();
}
function blkDel(i){ BLK.splice(i, 1); drawBlk(); }
function blkClear(){
  if (BLK.length && !confirm('ぜんぶ けしますか？')) return;
  BLK = []; drawBlk(); $('blkOut').textContent = '';
}

/* ブロックを Python の コードに する */
function blkCode(){
  var NL = String.fromCharCode(10);
  var py = [], html = [], js = [], sql = [];
  var needRandom = false, needTime = false, needMirai = false;

  BLK.forEach(function(b){
    var d = BLK_DEF[b.type];
    if (!d) return;
    var v = b.v, t = b.type, pad = '    '.repeat(b.lv || 0);
    var jpad = '  '.repeat(b.lv || 0);
    var line = null;

    /* ---- Python ---- */
    if (d.lg === 'py'){
      switch (t){
        case 'py_print':   line = 'print("' + q(v.text) + '")'; break;
        case 'py_printv':  line = 'print(' + (v.name||'x') + ')'; break;
        case 'py_var':     line = v.name + ' = ' + lit(v.val); break;
        case 'py_input':   line = v.name + ' = input("' + q(v.ask) + '")'; break;
        case 'py_inputn':  line = v.name + ' = int(input("' + q(v.ask) + '"))'; break;
        case 'py_comment': line = '# ' + (v.text||''); break;
        case 'py_blank':   line = ''; break;
        case 'py_for':     line = 'for i in range(' + (v.n||'5') + '):'; break;
        case 'py_forrange':line = 'for i in range(' + (v.a||1) + ', ' + (v.b||10) + ' + 1):'; break;
        case 'py_forlist': line = 'for ' + (v.name||'x') + ' in ' + (v.list||'[]') + ':'; break;
        case 'py_while':   line = 'while ' + (v.cond||'True') + ':'; break;
        case 'py_break':   line = 'break'; break;
        case 'py_continue':line = 'continue'; break;
        case 'py_if':      line = 'if ' + (v.cond||'True') + ':'; break;
        case 'py_elif':    line = 'elif ' + (v.cond||'True') + ':'; break;
        case 'py_else':    line = 'else:'; break;
        case 'py_calc':    line = v.name + ' = ' + (v.expr||'0'); break;
        case 'py_add':     line = v.name + ' = ' + v.name + ' + ' + (v.n||1); break;
        case 'py_random':  needRandom = true;
          line = v.name + ' = random.randint(' + (v.from||1) + ', ' + (v.to||6) + ')'; break;
        case 'py_choice':  needRandom = true;
          line = v.name + ' = random.choice(' + (v.list||'[]') + ')'; break;
        case 'py_round':   line = v.name + ' = round(' + lit(v.val) + ', ' + (v.n||2) + ')'; break;
        case 'py_abs':     line = v.name + ' = abs(' + lit(v.val) + ')'; break;
        case 'py_list':    line = v.name + ' = [' + items(v.items) + ']'; break;
        case 'py_append':  line = v.name + '.append(' + lit(v.val) + ')'; break;
        case 'py_len':     line = v.name + ' = len(' + (v.list||'[]') + ')'; break;
        case 'py_sort':    line = v.name + '.sort()'; break;
        case 'py_sum':     line = v.name + ' = sum(' + (v.list||'[]') + ')'; break;
        case 'py_maxmin':  line = v.name + ' = max(' + (v.list||'[]') + ')'; break;
        case 'py_dict':    line = v.name + ' = {' + dict(v.items) + '}'; break;
        case 'py_dictget': line = v.name + ' = ' + (v.dict||'{}') + '["' + q(v.key) + '"]'; break;
        case 'py_str':     line = v.name + ' = ' + lit(v.a) + ' + str(' + (v.b||'""') + ')'; break;
        case 'py_upper':   line = v.name + ' = ' + lit(v.val) + '.upper()'; break;
        case 'py_replace': line = v.name + ' = ' + (v.val||'""') + '.replace("'
          + q(v.a) + '", "' + q(v.b) + '")'; break;
        case 'py_split':   line = v.name + ' = ' + (v.val||'""') + '.split("'
          + q(v.sep) + '")'; break;
        case 'py_tostr':   line = v.name + ' = str(' + (v.val||'""') + ')'; break;
        case 'py_toint':   line = v.name + ' = int(' + (v.val||'0') + ')'; break;
        case 'py_def':     line = 'def ' + (v.name||'shigoto') + '(' + (v.args||'') + '):'; break;
        case 'py_return':  line = 'return ' + (v.val||''); break;
        case 'py_call':    line = (v.name||'shigoto') + '(' + (v.args||'') + ')'; break;
        case 'py_import':  line = 'import ' + (v.name||'random'); break;
        case 'py_try':     line = 'try:'; break;
        case 'py_except':  line = 'except Exception as e:'; break;
        case 'py_sleep':   needTime = true; line = 'time.sleep(' + (v.sec||1) + ')'; break;
        case 'py_mirai_add': needMirai = true;
          line = 'mirai.add("' + q(v.table) + '", ' + (v.vals||'') + ')'; break;
        case 'py_mirai_all': needMirai = true;
          line = v.name + ' = mirai.all("' + q(v.table) + '")'; break;
      }
      if (line !== null) py.push(pad + line);
      return;
    }

    /* ---- HTML ---- */
    if (d.lg === 'html'){
      var ip = '  '.repeat(b.lv || 0);
      switch (t){
        case 'h_h1':     line = '<h1>' + esc(v.text) + '</h1>'; break;
        case 'h_h2':     line = '<h2>' + esc(v.text) + '</h2>'; break;
        case 'h_p':      line = '<p>' + esc(v.text) + '</p>'; break;
        case 'h_br':     line = '<br>'; break;
        case 'h_hr':     line = '<hr>'; break;
        case 'h_b':      line = '<b>' + esc(v.text) + '</b>'; break;
        case 'h_button': line = '<button onclick="' + esc(v.fn) + '()">'
          + esc(v.text) + '</button>'; break;
        case 'h_input':  line = '<input id="' + esc(v.id) + '" placeholder="'
          + esc(v.ph) + '">'; break;
        case 'h_inputn': line = '<input id="' + esc(v.id) + '" type="number" value="'
          + esc(v.val) + '">'; break;
        case 'h_select': line = '<select id="' + esc(v.id) + '">'
          + (v.items||'').split(',').map(function(x){
              return '<option>' + esc(x.trim()) + '</option>'; }).join('')
          + '</select>'; break;
        case 'h_check':  line = '<label><input type="checkbox" id="' + esc(v.id)
          + '"> ' + esc(v.text) + '</label>'; break;
        case 'h_textarea': line = '<textarea id="' + esc(v.id) + '" placeholder="'
          + esc(v.ph) + '"></textarea>'; break;
        case 'h_img':    line = '<img src="' + esc(v.src) + '" alt="'
          + esc(v.alt) + '" style="max-width:100%">'; break;
        case 'h_div':    line = '<div id="' + esc(v.id) + '">'; break;
        case 'h_span':   line = '<span id="' + esc(v.id) + '">'
          + esc(v.text) + '</span>'; break;
        case 'h_canvas': line = '<canvas id="' + esc(v.id) + '" width="'
          + esc(v.w) + '" height="' + esc(v.h)
          + '" style="border:1px solid #ccc"></canvas>'; break;
        case 'h_ul':     line = '<ul>' + (v.items||'').split(',').map(function(x){
            return '<li>' + esc(x.trim()) + '</li>'; }).join('') + '</ul>'; break;
        case 'h_table':
          var th = (v.head||'').split(',').map(function(x){
            return '<th>' + esc(x.trim()) + '</th>'; }).join('');
          var tr = (v.rows||'').split(';').map(function(r){
            return '<tr>' + r.split(',').map(function(c){
              return '<td>' + esc(c.trim()) + '</td>'; }).join('') + '</tr>'; }).join('');
          line = '<table border="1"><tr>' + th + '</tr>' + tr + '</table>'; break;
        case 'h_a':      line = '<a href="' + esc(v.url) + '">' + esc(v.text) + '</a>'; break;
        case 'h_style':  line = '<style>' + esc(v.sel) + '{' + esc(v.prop) + ':'
          + esc(v.val) + '}</style>'; break;
      }
      if (line !== null) html.push(ip + line);
      return;
    }

    /* ---- JavaScript ---- */
    if (d.lg === 'js'){
      switch (t){
        case 'j_log':     line = 'console.log("' + q(v.text) + '");'; break;
        case 'j_alert':   line = 'alert("' + q(v.text) + '");'; break;
        case 'j_var':     line = 'let ' + v.name + ' = ' + lit(v.val) + ';'; break;
        case 'j_comment': line = '// ' + (v.text||''); break;
        case 'j_get':     line = 'let ' + v.name + ' = document.getElementById("'
          + q(v.id) + '");'; break;
        case 'j_settext': line = 'document.getElementById("' + q(v.id)
          + '").textContent = "' + q(v.text) + '";'; break;
        case 'j_gettext': line = 'let ' + v.name + ' = document.getElementById("'
          + q(v.id) + '").value;'; break;
        case 'j_setcolor':line = 'document.getElementById("' + q(v.id)
          + '").style.color = "' + q(v.col) + '";'; break;
        case 'j_setbg':   line = 'document.body.style.background = "'
          + q(v.col) + '";'; break;
        case 'j_hide':    line = 'document.getElementById("' + q(v.id)
          + '").style.display = "none";'; break;
        case 'j_show':    line = 'document.getElementById("' + q(v.id)
          + '").style.display = "";'; break;
        case 'j_add':     line = 'document.getElementById("' + q(v.id)
          + '").innerHTML += `' + (v.html||'') + '`;'; break;
        case 'j_click':   line = 'document.getElementById("' + q(v.id)
          + '").onclick = function(){'; break;
        case 'j_fn':      line = 'function ' + (v.name||'shigoto') + '(){'; break;
        case 'j_timer':   line = 'setInterval(function(){'; break;
        case 'j_wait':    line = 'setTimeout(function(){'; break;
        case 'j_key':     line = 'document.onkeydown = function(e){'; break;
        case 'j_if':      line = 'if (' + (v.cond||'true') + '){'; break;
        case 'j_else':    line = '} else {'; break;
        case 'j_for':     line = 'for (let i = 0; i < ' + (v.n||5) + '; i++){'; break;
        case 'j_random':  line = 'let ' + v.name + ' = Math.floor(Math.random() * ('
          + (v.to||6) + ' - ' + (v.from||1) + ' + 1)) + ' + (v.from||1) + ';'; break;
        case 'j_calc':    line = 'let ' + v.name + ' = ' + (v.expr||'0') + ';'; break;
        case 'j_num':     line = 'let ' + v.name + ' = Number(' + (v.val||'0') + ');'; break;
      }
      if (line !== null) js.push(jpad + line);
      return;
    }

    /* ---- SQL ---- */
    if (d.lg === 'sql'){
      switch (t){
        case 's_create':  line = 'CREATE TABLE ' + (v.name||'t') + '('
          + (v.cols||'') + ');'; break;
        case 's_insert':  line = 'INSERT INTO ' + (v.name||'t') + ' VALUES('
          + (v.vals||'') + ');'; break;
        case 's_select':  line = 'SELECT * FROM ' + (v.name||'t') + ';'; break;
        case 's_selectw': line = 'SELECT * FROM ' + (v.name||'t') + ' WHERE '
          + (v.cond||'1=1') + ';'; break;
        case 's_order':   line = 'SELECT * FROM ' + (v.name||'t') + ' ORDER BY '
          + (v.col||'id') + ' ' + (v.dir||'ASC') + ';'; break;
        case 's_count':   line = 'SELECT COUNT(*) FROM ' + (v.name||'t') + ';'; break;
        case 's_sum':     line = 'SELECT SUM(' + (v.col||'n') + ') FROM '
          + (v.name||'t') + ';'; break;
        case 's_update':  line = 'UPDATE ' + (v.name||'t') + ' SET ' + (v.set||'')
          + ' WHERE ' + (v.cond||'1=1') + ';'; break;
        case 's_delete':  line = 'DELETE FROM ' + (v.name||'t') + ' WHERE '
          + (v.cond||'1=0') + ';'; break;
      }
      if (line !== null) sql.push(line);
    }
  });

  // JavaScript の かたまりを 閉じる
  var jsCode = js.join(NL);
  var open2 = (jsCode.match(/\{/g)||[]).length - (jsCode.match(/\}/g)||[]).length;
  for (var k = 0; k < open2; k++) jsCode += NL + '}';
  if (jsCode) jsCode = jsCode.replace(/\}\s*$/,'};').replace(/\};\s*\};/g,'};');

  var head = [];
  if (needRandom) head.push('import random');
  if (needTime) head.push('import time');
  if (needMirai) head.push('import mirai');
  if (head.length) head.push('');

  window.BLK_OUT = {
    py: head.concat(py).join(NL),
    html: html.join(NL),
    js: jsCode,
    sql: sql.join(NL)
  };

  var show = [];
  if (window.BLK_OUT.html) show.push('--- index.html ---' + NL + window.BLK_OUT.html);
  if (window.BLK_OUT.js)   show.push('--- script.js ---' + NL + window.BLK_OUT.js);
  if (window.BLK_OUT.py)   show.push('--- main.py ---' + NL + window.BLK_OUT.py);
  if (window.BLK_OUT.sql)  show.push('--- データベース ---' + NL + window.BLK_OUT.sql);
  var code = show.join(NL + NL);
  $('blkCode').textContent = code || '（まだ ありません）';
  return code;
}

function esc(s){
  return String(s == null ? '' : s)
    .split('&').join('&amp;').split('<').join('&lt;').split('>').join('&gt;');
}
function q(s){
  var t = String(s == null ? '' : s);
  t = t.split(String.fromCharCode(92)).join(String.fromCharCode(92, 92));
  return t.split('"').join(String.fromCharCode(92, 34));
}
function items(s){
  return String(s||'').split(',').map(function(x){
    return '"' + q(x.trim()) + '"'; }).filter(function(x){ return x !== '""'; }).join(', ');
}
function dict(s){
  return String(s||'').split(',').map(function(x){
    var p = x.split(':');
    if (p.length < 2) return '';
    return '"' + q(p[0].trim()) + '": ' + lit(p.slice(1).join(':').trim());
  }).filter(Boolean).join(', ');
}

function esc2q(s){
  var t = String(s == null ? '' : s);
  t = t.split(String.fromCharCode(92)).join(String.fromCharCode(92, 92));
  t = t.split(String.fromCharCode(34)).join(String.fromCharCode(92, 34));
  return t;
}
function lit(s){
  s = String(s == null ? '' : s).trim();
  if (s === '') return '""';
  if (/^-?[0-9]+(\.[0-9]+)?$/.test(s)) return s;         // かず
  if (/^(True|False|None)$/.test(s)) return s;
  if (/^[\[\{\(]/.test(s)) return s;                      // リストなど
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(s)) return s;       // へんすうの なまえ
  return '"' + esc2q(s) + '"';
}

/* ブロックの まま うごかす */
async function blkRun(){
  blkCode();
  var o = window.BLK_OUT || {};
  if (!o.py && !o.html && !o.js && !o.sql){ return; }
  var NL = String.fromCharCode(10);
  var fs = [];
  if (o.html) fs.push({path:'index.html', content:o.html, kind:'html', is_entry:1});
  if (o.js)   fs.push({path:'script.js', content:o.js, kind:'js', is_entry:0});
  if (o.py)   fs.push({path:'main.py', content:o.py, kind:'py',
                       is_entry: o.html ? 0 : 1});

  var frame = $('blkFrame');
  if (!frame){
    frame = document.createElement('iframe');
    frame.id = 'blkFrame';
    frame.setAttribute('sandbox', 'allow-scripts allow-modals');
    frame.style.cssText = 'width:100%;height:320px;border:1px solid var(--line);'
      + 'border-radius:10px;background:#fff;margin-top:10px;display:none';
    $('blkOut').parentNode.insertBefore(frame, $('blkOut'));
  }
  window.MP_APP_ID = null;
  await runFiles(fs, {name:'app.db', schema:o.sql || '', seed:''},
                 $('blkOut'), frame);
}

/* コードに して、いつもの エディタへ */
function blkToCode(){
  blkCode();
  var o = window.BLK_OUT || {};
  if (!o.py && !o.html && !o.js && !o.sql){
    alert('まだ ブロックが ありません'); return;
  }
  if (!ME){ go('login'); return; }
  var NL = String.fromCharCode(10);

  APP = {id: null};
  FILES = [];
  if (o.html) FILES.push({path:'index.html', content:o.html + NL,
                          kind:'html', is_entry:1});
  if (o.css)  FILES.push({path:'style.css', content:o.css + NL, kind:'css', is_entry:0});
  if (o.js)   FILES.push({path:'script.js', content:o.js + NL, kind:'js', is_entry:0});
  if (o.py)   FILES.push({path:'main.py', content:o.py + NL, kind:'py',
                          is_entry: o.html ? 0 : 1});
  if (!FILES.length) FILES.push({path:'main.py', content:'', kind:'py', is_entry:1});

  TABLES = [];
  DB = {name:'app.db', schema: o.sql || '', seed:''};
  CUR = 0;
  $('eTitle').value = 'ブロックで つくった プログラム';
  $('eSummary').value = ''; $('eTags').value = 'はじめて';
  $('eDiff').value = '1'; $('eDel').style.display = 'none';
  $('eMsg').style.color = '#0e7490';
  $('eMsg').textContent = 'ブロックから ' + FILES.length
    + 'つの ファイルを つくりました。ここから 自由に 書きかえられます';
  $('eOut').textContent = ''; $('preview').style.display = 'none';
  pickFile(0, true); go('edit');
}

/* ============ コメント ============ */
async function loadComments(aid){
  var el = $('comBox');
  if (!el) return;
  var d = await J('/api/mp/app/' + aid + '/comments');
  if (!d.ok){ el.innerHTML = '<div class="empty">' + K(d.error) + '</div>'; return; }
  var form = ME
    ? '<div class="com-form">'
      + '<textarea id="comBody" maxlength="400" '
      + 'placeholder="ここが いいね、こうしたら もっと よくなるかも…"></textarea>'
      + '<div class="row" style="margin:8px 0 0">'
      + '<button class="com-btn" onclick="sendComment(' + aid + ')">おくる</button>'
      + '<span style="font-size:11.5px;color:#86a893">'
      + '本名や 住所は 書かないでください</span>'
      + '<span class="sp" style="flex:1"></span>'
      + '<span id="comMsg" style="font-size:12px"></span></div></div>'
    : '';
  el.innerHTML = form + (d.comments.length
    ? d.comments.map(function(x){
        var mine = ME && (ME.member_id === x.member_id);
        var can = mine || d.is_owner;
        return '<div class="com"><div class="who">'
          + '<span style="cursor:pointer;text-decoration:underline" '
          + 'onclick="viewAuthor(\'' + K(x.member_id) + '\')">'
          + K(x.nickname || x.member_id) + '</span>'
          + '<span class="when">' + K(x.created_at) + '</span>'
          + (can ? '<button class="btn gh sm" style="float:right" '
              + 'onclick="hideComment(' + x.id + ',' + aid + ')">けす</button>' : '')
          + '</div><div class="body">' + K(x.body) + '</div></div>';
      }).join('')
    : '<div class="empty" style="padding:22px 0">まだ コメントは ありません</div>');
}

async function sendComment(aid){
  var t = $('comBody').value.trim();
  if (!t){ return; }
  var d = await P('/api/mp/app/' + aid + '/comments', {body:t});
  var m = $('comMsg');
  if (!d.ok){ m.style.color = '#c0453b'; m.textContent = d.error; return; }
  $('comBody').value = '';
  loadComments(aid);
}

async function hideComment(cid, aid){
  if (!confirm('この コメントを けしますか？')) return;
  var d = await P('/api/mp/comment/' + cid + '/hide');
  if (!d.ok){ alert(d.error); return; }
  loadComments(aid);
}

