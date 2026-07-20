// ====================================================================
// qzero_mini.js: QZERO Miniのブラウザ内推論エンジン(エッジAI)
// サーバーのmini.pyと同じ計算をJavaScriptで行う。CPUは訪問者持ち!
// ====================================================================
const QzeroMini = (() => {
  let brains = {};    // 世代ごとの脳みそ置き場
  let brain = null;   // いま使ってる脳みそ
  let w2i = null;     // 単語→番号の辞書

  // 脳みそを世代指定でダウンロード(世代ごとに1回だけ)
  async function load(ver) {
    ver = String(ver || '10');
    if (brains[ver]) { brain = brains[ver].b; w2i = brains[ver].d; return true; }
    const r = await fetch('/api/qzero/mini/brain?v=' + ver);
    if (!r.ok) return false;
    const b = await r.json();
    const d = {};
    b.words.forEach((w, i) => { d[w] = i; });
    brains[ver] = { b, d };
    brain = b; w2i = d;
    return true;
  }

  function softmax(xs) {
    const m = Math.max(...xs);
    const es = xs.map(x => Math.exp(Math.min(x - m, 60)));
    const s = es.reduce((a, b) => a + b, 0);
    return es.map(e => e / s);
  }

  // 行列(M) × ベクトル(v)
  function mv(M, v) {
    return M.map(row => row.reduce((acc, x, j) => acc + x * v[j], 0));
  }

  // マルチヘッド注意(mini.pyの_attend_v6と同じ)
  function attend(X, W, DIM, HEADS) {
    const HD = DIM / HEADS;
    const q = mv(W.Wq, X[X.length - 1]);
    const Ks = X.map(x => mv(W.Wk, x));
    const Vs = X.map(x => mv(W.Wv, x));
    const ctx = new Array(DIM).fill(0);
    for (let h = 0; h < HEADS; h++) {
      const lo = h * HD, hi = (h + 1) * HD;
      const sc = Ks.map(k => {
        let s = 0;
        for (let d = lo; d < hi; d++) s += q[d] * k[d];
        return s / Math.sqrt(HD);
      });
      const at = softmax(sc);
      for (let d = lo; d < hi; d++) {
        ctx[d] = at.reduce((acc, a, t) => acc + a * Vs[t][d], 0);
      }
    }
    return mv(W.Wp, ctx);
  }

  // 前向き計算(mini.pyの_forward_v6と同じ: 各層で最後の位置だけ更新)
  function forward(ctx) {
    const b = brain, DIM = b.DIM, HEADS = b.HEADS;
    const n = ctx.length;
    const X = [];
    for (let t = 0; t < n; t++) {
      X.push(b.emb[ctx[t]].map((e, d) => e + b.pos[t][d]));
    }
    for (const L of b.layers) {
      const h = attend(X, L, DIM, HEADS);
      const last = X[n - 1].map((x, d) => x + h[d]);   // 残差(attention)
      // FFN
      const ffnH = L.W1.map((row, j) =>
        Math.max(0, row.reduce((acc, w, d) => acc + w * last[d], 0) + L.b1[j]));
      const ffnO = L.W2.map((row, d) =>
        row.reduce((acc, w, j) => acc + w * ffnH[j], 0) + L.b2[d]);
      X[n - 1] = last.map((x, d) => x + ffnO[d]);      // 残差(FFN)
    }
    return softmax(mv(b.Wo, X[n - 1]));
  }

  // スペースなし入力の自動区切り(長い単語から先に探す)
  function tokenize(text) {
    const sorted = [...brain.words].sort((a, b) => b.length - a.length);
    const tokens = [];
    let rest = text.replace(/[ \u3000]/g, '');
    while (rest) {
      const hit = sorted.find(w => rest.startsWith(w));
      if (hit) { tokens.push(hit); rest = rest.slice(hit.length); }
      else { tokens.push(rest[0]); rest = rest.slice(1); }
    }
    return tokens;
  }

  // 生成(mini.pyのgenerateと同じ: 上位3候補の温度サンプリング+おわりで停止)
  function generate(startText) {
    const b = brain;
    let tokens = /[ \u3000]/.test(startText.trim())
      ? startText.replace(/\u3000/g, ' ').split(' ').filter(t => t)
      : tokenize(startText);
    const unknown = tokens.filter(t => !(t in w2i));
    if (unknown.length) return { ok: false, unknown };
    let ctx = tokens.map(t => w2i[t]);
    if (ctx.length === 0 || ctx.length >= b.MAXLEN) return { ok: false, unknown: [] };
    const result = [...tokens];
    for (let i = 0; i < b.MAXLEN - tokens.length; i++) {
      const out = forward(ctx);
      const top = out.map((p, idx) => [p, idx]).sort((a, b2) => b2[0] - a[0]).slice(0, 3);
      const ws = top.map(([p]) => p * p);  // 確率を2乗して1位を有利に
      let r = Math.random() * ws.reduce((a, b2) => a + b2, 0);
      let nxt = top[0][1];
      for (let k = 0; k < top.length; k++) { r -= ws[k]; if (r <= 0) { nxt = top[k][1]; break; } }
      const w = b.words[nxt];
      if (w === 'おわり') break;  // 文のおわりを自分で判断!
      result.push(w);
      ctx.push(nxt);
      if (ctx.length >= b.MAXLEN) break;
    }
    return { ok: true, text: result.join(' ') };
  }

  function vocabulary() { return brain.words.filter(w => w !== 'おわり'); }
  function info() { return { dim: brain.DIM, vocab: brain.words.length }; }

  return { load, generate, vocabulary, info };
})();
