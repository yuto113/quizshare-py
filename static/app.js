// ================================================================
// クイズシェア 共通JS
// ページの一部だけを更新(リロードなし)するためにJSが必要。
// # ページを丸ごとリロードしたくない(タイマーが0に戻ったり入力が消えたりする)ので、
// #   fetchでサーバーと通信する方法を使っています。
// ================================================================

// サーバーと話す関数(JSONでやりとり)
// # fetchはブラウザが持っている「サーバーに手紙を送る」機能。
// #   Pythonだけではフォーム送信=リロードになるため、ここだけJSを使う。
window.api = {
  async call(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    };
    if (body) opts.body = JSON.stringify(body);
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new Error('通信に失敗したよ。電波を確認してね');
    }
    let data = null;
    try { data = await res.json(); } catch {}
    if (!res.ok || !data || data.ok === false) {
      // # GETで401のとき、エントリーページにいたら無視する(自動チェックのエラーを隠す)
      // # POSTのときは無視しない(ログイン失敗はちゃんとトーストを出す)
      if (res.status === 401 && method === 'GET' && (window.location.pathname === '/' || window.location.pathname === '/library')) return {};
      throw new Error((data && data.error) || `エラーが起きたよ (${res.status})`);
    }
    return data;
  },
  get:   (p)    => window.api.call('GET',    p),
  post:  (p, b) => window.api.call('POST',   p, b),
  patch: (p, b) => window.api.call('PATCH',  p, b),
  del:   (p, b) => window.api.call('DELETE', p, b),
};

// トースト通知(画面上に少しの間だけ出るメッセージ)
// # DOMに要素を追加する操作はJSじゃないとできないのでJSを使っています。
window.toast = function(message, type = 'success') {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    host.className = 'toast-host';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = 'toast ' + (type === 'error' ? 'error' : type === 'success' ? 'success' : '');
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 350);
  }, 2500);
};

// モーダルを開いたり閉じたりする関数
// # 画面遷移せずにフタをかぶせるように表示したいのでJSを使う。
window.openModal = function(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'flex';
};
window.closeModal = function(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
};

// 時間を「12.3秒」や「1分23秒」に整形する
window.formatTime = function(ms) {
  if (!ms || ms <= 0) return '-';
  const s = ms / 1000;
  if (s < 60) return s.toFixed(1) + '秒';
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return m + '分' + ss + '秒';
};

// 星マークのHTMLを作る(★★★☆☆みたいな感じ)
window.starsHtml = function(value, max = 5) {
  let out = '<span class="stars">';
  for (let i = 1; i <= max; i++) {
    if (i <= Math.round(value)) out += '★';
    else out += '<span class="empty">★</span>';
  }
  out += '</span>';
  return out;
};

// HTMLに埋め込むときの危険な文字をエスケープする
// # <script>とかを書かれたときに実行されないように変換する大事な作業
window.escapeHtml = function(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

// ログアウトボタンの共通処理
// # どのページでもヘッダーの「ログアウト」を押したら同じ動作にしたいので共通化
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-action="logout"]');
  if (!btn) return;
  e.preventDefault();
  if (!confirm('ログアウトしますか?')) return;
  try {
    const r = await window.api.post('/api/logout');
    window.location.href = r.redirect || '/';
  } catch (err) {
    window.toast(err.message, 'error');
  }
});

// モーダルの外側クリックで閉じる
document.addEventListener('click', (e) => {
  if (e.target.classList && e.target.classList.contains('modal-overlay')) {
    e.target.style.display = 'none';
  }
});

// ============================================================
// 著作権同意 & Claude確認モーダル
// ============================================================

// 著作権同意チェック（ライブラリページで表示）
window.checkLibraryConsent = function() {
  return new Promise((resolve) => {
    if (localStorage.getItem('library_copyright_agreed') === '1') {
      resolve(true);
      return;
    }
    const modal = document.createElement('div');
    modal.id = 'copyright-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;';
    modal.innerHTML = `
      <div style="background:#fff;border-radius:20px;padding:28px 24px;max-width:480px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,0.2);">
        <div style="font-size:2rem;text-align:center;margin-bottom:12px;">📚</div>
        <h2 style="font-size:1.2rem;font-weight:900;text-align:center;margin:0 0 12px;">引用ライブラリ 利用規約</h2>
        <div style="background:#fff8e1;border:1px solid #ffe082;border-radius:10px;padding:12px 14px;font-size:0.88rem;color:#555;margin-bottom:16px;line-height:1.7;">
          <p style="margin:0 0 8px;"><strong>⚠️ 著作権について</strong></p>
          <p style="margin:0 0 8px;">このライブラリの問題はAI（Claude）が作成したものです。引用した問題をグループで使用する際は、以下の点に同意してください。</p>
          <ul style="margin:0;padding-left:18px;">
            <li>問題文・解説の著作権はQuizShareに帰属します</li>
            <li>商業目的での無断転用は禁止です</li>
            <li>教育・学習目的での利用を歓迎します</li>
            <li>AIが作成した内容のため、誤りが含まれる可能性があります</li>
          </ul>
        </div>
        <label style="display:flex;align-items:center;gap:8px;margin-bottom:16px;cursor:pointer;">
          <input type="checkbox" id="agree-copyright" style="width:18px;height:18px;accent-color:#e91e63;">
          <span style="font-size:0.9rem;">上記の内容に同意します</span>
        </label>
        <label style="display:flex;align-items:center;gap:8px;margin-bottom:20px;cursor:pointer;">
          <input type="checkbox" id="agree-no-show" style="width:18px;height:18px;accent-color:#e91e63;">
          <span style="font-size:0.85rem;color:#888;">次回からこの画面を表示しない（このデバイスで）</span>
        </label>
        <button id="agree-btn" disabled onclick="window._onLibraryAgree()"
          style="width:100%;padding:12px;background:#e91e63;color:white;border:none;border-radius:10px;font-size:1rem;font-weight:bold;cursor:pointer;opacity:0.5;transition:opacity 0.2s;">
          ✅ 同意してライブラリを使う
        </button>
        <button onclick="window._onLibraryDisagree()"
          style="width:100%;padding:10px;background:none;border:none;color:#aaa;cursor:pointer;font-size:0.85rem;margin-top:8px;">
          キャンセル
        </button>
      </div>
    `;
    document.body.appendChild(modal);
    document.getElementById('agree-copyright').addEventListener('change', function() {
      const btn = document.getElementById('agree-btn');
      btn.disabled = !this.checked;
      btn.style.opacity = this.checked ? '1' : '0.5';
    });
    window._onLibraryAgree = function() {
      if (document.getElementById('agree-no-show').checked) {
        localStorage.setItem('library_copyright_agreed', '1');
      }
      modal.remove();
      resolve(true);
    };
    window._onLibraryDisagree = function() {
      modal.remove();
      resolve(false);
    };
  });
};

// Claude確認モーダル（引用時に表示）
window.checkClaudeNotice = function() {
  return new Promise((resolve) => {
    if (localStorage.getItem('claude_notice_agreed') === '1') {
      resolve(true);
      return;
    }
    const modal = document.createElement('div');
    modal.id = 'claude-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;';
    modal.innerHTML = `
      <div style="background:#fff;border-radius:20px;padding:28px 24px;max-width:440px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,0.2);">
        <div style="font-size:2rem;text-align:center;margin-bottom:12px;">🤖</div>
        <h2 style="font-size:1.1rem;font-weight:900;text-align:center;margin:0 0 12px;">AIが作成したクイズについて</h2>
        <div style="background:#e8f5e9;border:1px solid #a5d6a7;border-radius:10px;padding:12px 14px;font-size:0.88rem;color:#555;margin-bottom:16px;line-height:1.7;">
          <p style="margin:0 0 8px;">このクイズはAI（Claude）によって自動生成されています。</p>
          <ul style="margin:0;padding-left:18px;">
            <li>内容に誤りが含まれる可能性があります</li>
            <li>重要な情報は必ず公式の資料で確認してください</li>
            <li>引用後は内容を確認・編集することをおすすめします</li>
          </ul>
        </div>
        <label style="display:flex;align-items:center;gap:8px;margin-bottom:20px;cursor:pointer;">
          <input type="checkbox" id="claude-no-show" style="width:18px;height:18px;accent-color:#4caf50;">
          <span style="font-size:0.85rem;color:#888;">次回からこの画面を表示しない（このデバイスで）</span>
        </label>
        <button onclick="window._onClaudeOk()"
          style="width:100%;padding:12px;background:#4caf50;color:white;border:none;border-radius:10px;font-size:1rem;font-weight:bold;cursor:pointer;">
          ✅ 確認しました・引用する
        </button>
        <button onclick="window._onClaudeCancel()"
          style="width:100%;padding:10px;background:none;border:none;color:#aaa;cursor:pointer;font-size:0.85rem;margin-top:8px;">
          キャンセル
        </button>
      </div>
    `;
    document.body.appendChild(modal);
    window._onClaudeOk = function() {
      if (document.getElementById('claude-no-show').checked) {
        localStorage.setItem('claude_notice_agreed', '1');
      }
      modal.remove();
      resolve(true);
    };
    window._onClaudeCancel = function() {
      modal.remove();
      resolve(false);
    };
  });
};

// ============================================================
// テーマシステム
// ============================================================
const THEMES = {
  default:   { name: 'デフォルト', bg: '#fdf6f0', style: '' },
  modern:    { name: '近代化', bg: '#0f0f1a', style: 'dark', textColor: '#e0e0ff' },
  classic:   { name: 'クラシック', bg: '#f5f0e8', style: 'sepia', textColor: '#3d2b1f' },
  spring:    { name: '春', bg: 'linear-gradient(135deg,#ffe0f0 0%,#fff0f5 50%,#ffe8f0 100%)', style: 'seasonal' },
  summer:    { name: '夏', bg: 'linear-gradient(135deg,#e0f0ff 0%,#f0f8ff 50%,#e8f0ff 100%)', style: 'seasonal' },
  autumn:    { name: '秋', bg: 'linear-gradient(135deg,#fff3e0 0%,#fff8e8 50%,#ffe8d0 100%)', style: 'seasonal' },
  winter:    { name: '冬', bg: 'linear-gradient(135deg,#e8f4ff 0%,#f0f8ff 50%,#e0ecff 100%)', style: 'seasonal' },
};

const SEASON_EMOJIS = {
  spring:      ['🌸','🌷','🦋','🌺','🌼'],
  summer:      ['🌊','☀️','🐚','🌴','⭐'],
  autumn:      ['🍁','🍂','🎃','🌙','🍄'],
  winter:      ['❄️','⛄','🎄','✨','🌟'],
  newyear:     ['🎍','🎋','🎎','🎏','🎑'],
  setsubun:    ['👹','🫘','✨','🌟','⭐'],
  hinamatsuri: ['🎎','🌸','🍡','🏮','💮'],
  childrensday:['🎏','🎐','🎋','🌿','⛎'],
  tanabata:    ['⭐','🌟','💫','🎋','🌌'],
  halloween:   ['🎃','👻','🕷️','🦇','🌙'],
  christmas:   ['🎄','🎁','⭐','🎅','❄️'],
  shichigosan: ['🎑','👘','🍬','🏮','🌸'],
  sakura:      ['🌸','🌺','🌷','🦋','💮'],
  ocean:       ['🌊','🐚','🐠','🐋','⭐'],
  forest:      ['🌲','🌿','🦋','🍃','🌱'],
  lavender:    ['💜','🌸','✨','🦋','🌺'],
  candy:       ['🍬','🍭','🎀','💕','⭐'],
  mint:        ['🌿','💚','🍃','✨','🌱'],
  galaxy:      ['🌌','⭐','💫','🌟','✨'],
  neon:        ['💚','⚡','🌟','✨','💡'],
  retro:       ['📺','🎮','⭐','🌟','🎯'],
  pastel:      ['🎀','🌸','💕','🌈','✨'],
  sunset:      ['🌅','🌇','🌸','⭐','🌟'],
  aurora:      ['🌌','💙','💚','⭐','✨'],
  washi:       ['📄','🎋','🌸','🎎','✨'],
  matcha:      ['🍵','🌿','🍃','💚','🌱'],
  indigo:      ['🟦','💙','🌊','⭐','✨'],
};

// 特別な日の定義
const SPECIAL_DAYS = [
  { month:1,  day:1,  theme:'newyear',  name:'お正月',    emoji:['🎍','🎋','🎎','🎏','🎑'] },
  { month:2,  day:3,  theme:'setsubun', name:'節分',      emoji:['👹','🫘','⬼','🌟','✨'] },
  { month:3,  day:3,  theme:'hinamatsuri', name:'ひな祭り', emoji:['🎎','🌸','🍡','🏮','💮'] },
  { month:4,  day:1,  theme:'spring',   name:'エイプリルフール', emoji:['🌸','😄','🎭','🌷','🦋'] },
  { month:5,  day:5,  theme:'childrensday', name:'こどもの日', emoji:['🎏','⛎','🎐','🎋','🌿'] },
  { month:7,  day:7,  theme:'tanabata', name:'七夕',      emoji:['⭐','🌟','💫','🎋','🌌'] },
  { month:8,  day:15, theme:'summer',   name:'お盆',      emoji:['🏮','👻','🌻','🎆','🌊'] },
  { month:9,  day:15, theme:'autumn',   name:'お月見',    emoji:['🌕','🍡','🐰','⭐','🍂'] },
  { month:10, day:31, theme:'halloween', name:'ハロウィン', emoji:['🎃','👻','🕷️','🦇','🌙'] },
  { month:11, day:15, theme:'shichigosan', name:'七五三',  emoji:['🎑','👘','🍬','🏮','🌸'] },
  { month:12, day:24, theme:'christmas', name:'クリスマスイブ', emoji:['🎄','⭐','🎅','🦌','❄️'] },
  { month:12, day:25, theme:'christmas', name:'クリスマス', emoji:['🎄','🎁','⭐','🎅','❄️'] },
  { month:12, day:31, theme:'newyear',  name:'大晦日',    emoji:['🎉','🎆','🎇','✨','🔔'] },
];

// 特別なテーマの背景
const SPECIAL_THEMES = {
  newyear:     { bg:'linear-gradient(135deg,#fff5e0,#ffe8d0)', style:'' },
  setsubun:    { bg:'linear-gradient(135deg,#fff0e8,#ffe8d8)', style:'' },
  hinamatsuri: { bg:'linear-gradient(135deg,#ffe0f0,#fff0f8)', style:'' },
  childrensday:{ bg:'linear-gradient(135deg,#e0ffe8,#f0fff5)', style:'' },
  tanabata:    { bg:'linear-gradient(135deg,#0a0020,#1a0040)', style:'dark' },
  halloween:   { bg:'linear-gradient(135deg,#1a0a00,#2d1500)', style:'dark' },
  christmas:   { bg:'linear-gradient(135deg,#e8fff0,#f0fff5)', style:'' },
  shichigosan: { bg:'linear-gradient(135deg,#fff0f8,#ffe8f5)', style:'' },
};

function getSpecialDay() {
  const now = new Date();
  const m = now.getMonth() + 1;
  const d = now.getDate();
  return SPECIAL_DAYS.find(s => s.month === m && s.day === d) || null;
}

function getCurrentSeason() {
  const special = getSpecialDay();
  if (special) return special.theme;
  const m = new Date().getMonth() + 1;
  if (m >= 3 && m <= 5) return 'spring';
  if (m >= 6 && m <= 8) return 'summer';
  if (m >= 9 && m <= 11) return 'autumn';
  return 'winter';
}

function applyTheme(themeKey) {
  const theme = THEMES[themeKey] || THEMES.default;
  const body = document.body;

  // 既存の絵文字・テーマクラスを除去
  document.querySelectorAll('.seasonal-emoji').forEach(e => e.remove());
  body.classList.remove('theme-dark','theme-sepia','theme-seasonal');

  if (theme.style === 'dark') {
    body.classList.add('theme-dark');
    body.style.background = theme.bg;
  } else if (theme.style === 'sepia') {
    body.classList.add('theme-sepia');
    body.style.background = theme.bg;
  } else {
    body.style.background = theme.bg;
  }

  // 絵文字はSEASON_EMOJISにキーがあれば常に表示
  if (SEASON_EMOJIS[themeKey]) {
    spawnSeasonalEmojis(themeKey);
  }

  localStorage.setItem('quiz_theme', themeKey);
}

function spawnSeasonalEmojis(season) {
  document.querySelectorAll('.seasonal-emoji').forEach(e => e.remove());
  const emojis = SEASON_EMOJIS[season] || [];
  for (let i = 0; i < 12; i++) {
    const el = document.createElement('div');
    el.className = 'seasonal-emoji';
    el.textContent = emojis[Math.floor(Math.random() * emojis.length)];
    el.style.cssText = `position:fixed; font-size:${1.2 + Math.random()*1.5}rem; opacity:${0.08 + Math.random()*0.12};
      top:${Math.random()*100}vh; left:${Math.random()*100}vw; pointer-events:none; z-index:0;
      animation: floatEmoji ${8+Math.random()*8}s ease-in-out infinite ${Math.random()*5}s alternate;`;
    document.body.appendChild(el);
  }
}

function checkAutoTheme() {
  // 特別テーマをTHEMESに登録
  Object.assign(THEMES, SPECIAL_THEMES);
  // 特別な日の絵文字をSEASON_EMOJISに登録
  SPECIAL_DAYS.forEach(s => { SEASON_EMOJIS[s.theme] = s.emoji; });

  if (localStorage.getItem('quiz_auto_theme') === '1') {
    const special = getSpecialDay();
    if (special) {
      // 特別な日のバナーを表示
      showSpecialDayBanner(special.name);
    }
    applyTheme(getCurrentSeason());
  } else {
    const saved = localStorage.getItem('quiz_theme') || 'default';
    Object.assign(THEMES, SPECIAL_THEMES);
    applyTheme(saved);
  }
}

function showSpecialDayBanner(name) {
  if (document.getElementById('special-day-banner')) return;
  const banner = document.createElement('div');
  banner.id = 'special-day-banner';
  banner.style.cssText = 'position:fixed;top:60px;left:50%;transform:translateX(-50%);background:rgba(255,255,255,0.95);border-radius:20px;padding:8px 20px;font-size:0.85rem;font-weight:bold;box-shadow:0 4px 16px rgba(0,0,0,0.15);z-index:8000;animation:fadeIn 0.3s ease;pointer-events:none;';
  banner.textContent = '🎉 今日は' + name + 'です！';
  document.body.appendChild(banner);
  setTimeout(() => banner.remove(), 4000);
}

// ページ読み込み時にテーマ適用
document.addEventListener('DOMContentLoaded', checkAutoTheme);
