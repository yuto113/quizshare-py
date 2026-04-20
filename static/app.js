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
