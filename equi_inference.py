# ============================================================
# Equi 1 numpy推論エンジン v2（高速化版）
# KVキャッシュ + レスポンスキャッシュ + float16
# ============================================================
import numpy as np
import json, os, hashlib, time

class EquiInference:
    def __init__(self, weights_path, config_path):
        print('Equi 1 読み込み中...', flush=True)

        if str(config_path).startswith('http'):
            import urllib.request as _u2
            _req2 = _u2.Request(config_path, headers={'User-Agent': 'Qstart/1.6'})
            with _u2.urlopen(_req2, timeout=60) as _r2:
                raw = json.loads(_r2.read().decode('utf-8'))
        else:
            with open(config_path, encoding='utf-8') as f:
                raw = json.load(f)

        self.vocab = raw['vocab']
        self.config = raw['config']
        self.DIM = self.config['DIM']
        self.HEADS = self.config['HEADS']
        self.LAYERS = self.config['LAYERS']
        self.FFN_DIM = self.config['FFN_DIM']
        self.MAXLEN = self.config['MAXLEN']
        self.V = self.config['V']
        self.HEAD_DIM = self.DIM // self.HEADS

        self.w2i = {w: i for i, w in enumerate(self.vocab)}
        self.i2w = {i: w for i, w in enumerate(self.vocab)}
        self.PAD = self.w2i['<PAD>']
        self.BOS = self.w2i['<BOS>']
        self.EOS = self.w2i['<EOS>']
        self.UNK = self.w2i['<UNK>']
        self.QM = self.w2i['？']
        self.AN = self.w2i['：']

        # ★ float16で読み込み（メモリ半減+計算高速化）
        if str(weights_path).startswith('http'):
            import urllib.request as _u, io as _io, time as _t
            print(f'  リモート取得中...', flush=True)
            _t0 = _t.time()
            _req = _u.Request(weights_path, headers={'User-Agent': 'Qstart/1.6'})
            with _u.urlopen(_req, timeout=300) as _r:
                _buf = _io.BytesIO(_r.read())
            print(f'  取得完了 {_buf.getbuffer().nbytes/1e6:.0f}MB ({_t.time()-_t0:.1f}秒)', flush=True)
            raw_weights = dict(np.load(_buf, allow_pickle=False))
            del _buf
        else:
            raw_weights = dict(np.load(weights_path, allow_pickle=False))
        self.W = {}
        for k, v in raw_weights.items():
            self.W[k] = v  # float32のまま（安定性優先）

        # nn.MultiheadAttention 形式(in_proj_weight)を Wq/Wk/Wv に分解
        _conv = 0
        for k in list(self.W.keys()):
            if k.endswith('attn.in_proj_weight'):
                p = k[:-len('in_proj_weight')]
                w = self.W[k]
                d = w.shape[0] // 3
                self.W[p + 'Wq.weight'] = w[:d]
                self.W[p + 'Wk.weight'] = w[d:2*d]
                self.W[p + 'Wv.weight'] = w[2*d:]
                bk = p + 'in_proj_bias'
                if bk in self.W:
                    b = self.W[bk]
                    self.W[p + 'Wq.bias'] = b[:d]
                    self.W[p + 'Wk.bias'] = b[d:2*d]
                    self.W[p + 'Wv.bias'] = b[2*d:]
                _conv += 1
            # out_proj → Wo
            if k.endswith('attn.out_proj.weight'):
                p = k[:-len('out_proj.weight')]
                self.W[p + 'Wo.weight'] = self.W[k]
                if p + 'out_proj.bias' in self.W:
                    self.W[p + 'Wo.bias'] = self.W[p + 'out_proj.bias']
            # ffn.0 / ffn.2 → fc1 / fc2
            if '.ffn.0.' in k:
                self.W[k.replace('.ffn.0.', '.ffn.fc1.')] = self.W[k]
            if '.ffn.2.' in k:
                self.W[k.replace('.ffn.2.', '.ffn.fc2.')] = self.W[k]
                self.W[k.replace('.ffn.2.', '.ffn.3.')] = self.W[k]
        # 出力層の別名
        for a, b in [('head.weight', 'output.weight'), ('head.bias', 'output.bias'),
                     ('ln_f.weight', 'ln_final.weight'), ('ln_f.bias', 'ln_final.bias')]:
            if a in self.W and b not in self.W:
                self.W[b] = self.W[a]
            if b in self.W and a not in self.W:
                self.W[a] = self.W[b]
        if _conv:
            print(f'  MultiheadAttention形式を変換: {_conv}層', flush=True)

        # ★ レスポンスキャッシュ
        self._cache = {}
        self._cache_max = 200
        self.model_name = 'Apex'   # 表示名(load時に上書きできる)

        total = sum(v.size for v in self.W.values())
        print(f'  語彙: {self.V}, パラメータ: {total:,} (float32)')
        print('Equi 1 準備完了!', flush=True)

    # ===== 基本演算 =====
    def layer_norm(self, x, w_key):
        gamma = self.W[w_key + '.weight'].astype(np.float32)
        beta = self.W[w_key + '.bias'].astype(np.float32)
        x32 = x.astype(np.float32)
        mean = x32.mean(axis=-1, keepdims=True)
        var = x32.var(axis=-1, keepdims=True)
        out = gamma * (x32 - mean) / np.sqrt(var + 1e-5) + beta
        return out.astype(np.float32)

    def linear(self, x, w_key):
        W = self.W[w_key + '.weight']
        b = self.W[w_key + '.bias']
        return (x @ W.T + b)

    def gelu(self, x):
        x32 = x.astype(np.float32)
        out = 0.5 * x32 * (1 + np.tanh(np.sqrt(2 / np.pi) * (x32 + 0.044715 * x32**3)))
        return out.astype(np.float32)

    def softmax(self, x, axis=-1):
        x32 = x.astype(np.float32)
        e = np.exp(x32 - x32.max(axis=axis, keepdims=True))
        return (e / e.sum(axis=axis, keepdims=True)).astype(np.float32)

    # ===== ★ KVキャッシュ付きAttention =====
    def attention_cached(self, x_new, layer_idx, kv_cache):
        """1トークンだけ計算（KVキャッシュ使用）"""
        prefix = f'layers.{layer_idx}.attn'
        h, hd = self.HEADS, self.HEAD_DIM

        # 新しいトークンのQ, K, V
        q = self.linear(x_new, f'{prefix}.Wq').reshape(1, h, hd)
        k_new = self.linear(x_new, f'{prefix}.Wk').reshape(1, h, hd)
        v_new = self.linear(x_new, f'{prefix}.Wv').reshape(1, h, hd)

        # キャッシュに追加
        cache_key = f'layer_{layer_idx}'
        if cache_key not in kv_cache:
            kv_cache[cache_key] = {'k': k_new, 'v': v_new}
        else:
            kv_cache[cache_key]['k'] = np.concatenate([kv_cache[cache_key]['k'], k_new], axis=0)
            kv_cache[cache_key]['v'] = np.concatenate([kv_cache[cache_key]['v'], v_new], axis=0)

        K = kv_cache[cache_key]['k']  # (seq_so_far, h, hd)
        V = kv_cache[cache_key]['v']

        # Q: (1, h, hd) -> (h, 1, hd)
        Q = q.transpose(1, 0, 2)
        Kt = K.transpose(1, 0, 2)  # (h, seq, hd)
        Vt = V.transpose(1, 0, 2)

        scores = (Q @ Kt.transpose(0, 2, 1)) / np.sqrt(hd)  # (h, 1, seq)
        weights = self.softmax(scores, axis=-1)
        context = weights @ Vt  # (h, 1, hd)
        context = context.transpose(1, 0, 2).reshape(1, self.DIM)

        return self.linear(context, f'{prefix}.Wo')

    def attention_full(self, x, layer_idx):
        """全トークン計算（初回プリフィル用）"""
        n, d = x.shape
        h, hd = self.HEADS, self.HEAD_DIM
        prefix = f'layers.{layer_idx}.attn'

        Q = self.linear(x, f'{prefix}.Wq').reshape(n, h, hd).transpose(1, 0, 2)
        K = self.linear(x, f'{prefix}.Wk').reshape(n, h, hd).transpose(1, 0, 2)
        V = self.linear(x, f'{prefix}.Wv').reshape(n, h, hd).transpose(1, 0, 2)

        scores = Q @ K.transpose(0, 2, 1) / np.sqrt(hd)
        mask = np.triu(np.ones((n, n)), k=1).astype(bool)
        scores[:, mask] = -1e4
        weights = self.softmax(scores, axis=-1)
        context = weights @ V
        context = context.transpose(1, 0, 2).reshape(n, d)
        return self.linear(context, f'{prefix}.Wo'), K.transpose(1, 0, 2), V.transpose(1, 0, 2)

    def transformer_block(self, x, layer_idx):
        prefix = f'layers.{layer_idx}'
        normed = self.layer_norm(x, f'{prefix}.ln1')
        attn_out, _, _ = self.attention_full(normed, layer_idx)
        x = x + attn_out
        normed = self.layer_norm(x, f'{prefix}.ln2')
        h = self.linear(normed, f'{prefix}.ffn.0')
        h = self.gelu(h)
        h = self.linear(h, f'{prefix}.ffn.3')
        x = x + h
        return x

    def transformer_block_cached(self, x_new, layer_idx, kv_cache):
        """1トークンだけ処理（KVキャッシュ）"""
        prefix = f'layers.{layer_idx}'
        normed = self.layer_norm(x_new, f'{prefix}.ln1')
        attn_out = self.attention_cached(normed, layer_idx, kv_cache)
        x_new = x_new + attn_out
        normed = self.layer_norm(x_new, f'{prefix}.ln2')
        h = self.linear(normed, f'{prefix}.ffn.0')
        h = self.gelu(h)
        h = self.linear(h, f'{prefix}.ffn.3')
        x_new = x_new + h
        return x_new

    def forward_prefill(self, token_ids):
        """プリフィル: 入力全体を一括処理してKVキャッシュを作る"""
        n = len(token_ids)
        tok_emb = self.W['tok_emb.weight'][token_ids]
        pos_emb = self.W['pos_emb.weight'][:n]
        x = (tok_emb + pos_emb).astype(np.float32)

        kv_cache = {}
        for i in range(self.LAYERS):
            prefix = f'layers.{i}'
            normed = self.layer_norm(x, f'{prefix}.ln1')
            attn_out, K, V = self.attention_full(normed, i)
            kv_cache[f'layer_{i}'] = {'k': K, 'v': V}
            x = x + attn_out
            normed = self.layer_norm(x, f'{prefix}.ln2')
            h = self.linear(normed, f'{prefix}.ffn.0')
            h = self.gelu(h)
            h = self.linear(h, f'{prefix}.ffn.3')
            x = x + h

        x = self.layer_norm(x, 'ln_final')
        logits = (x @ self.W['output.weight'].T + self.W['output.bias'])
        return logits, kv_cache, n

    def forward_one(self, token_id, pos, kv_cache):
        """1トークンだけ処理（KVキャッシュ使用）"""
        tok_emb = self.W['tok_emb.weight'][token_id:token_id+1]
        pos_emb = self.W['pos_emb.weight'][pos:pos+1]
        x = (tok_emb + pos_emb).astype(np.float32)

        for i in range(self.LAYERS):
            x = self.transformer_block_cached(x, i, kv_cache)

        x = self.layer_norm(x, 'ln_final')
        logits = (x @ self.W['output.weight'].T + self.W['output.bias'])
        return logits

    # ===== トークナイザ =====
    def hybrid_tokenize(self, text):
        text = text.replace(' ', '')
        result, i = [], 0
        SPECIAL = {'<PAD>', '<BOS>', '<EOS>', '<UNK>', '？', '：'}
        while i < len(text):
            matched = False
            for ln in range(min(8, len(text)-i), 0, -1):
                cand = text[i:i+ln]
                if cand in self.w2i and cand not in SPECIAL:
                    result.append(self.w2i[cand])
                    i += ln
                    matched = True
                    break
            if not matched:
                result.append(self.w2i.get(text[i], self.UNK))
                i += 1
        return result

    def decode(self, ids):
        skip = {self.BOS, self.EOS, self.PAD, self.QM, self.AN}
        return ''.join(self.i2w.get(t, '?') for t in ids if t not in skip)

    # ===== 生成 =====
    def topk_sample(self, logits, k=10, temperature=0.7):
        logits = logits.astype(np.float32) / temperature
        top_idx = np.argsort(logits)[-k:]
        top_vals = logits[top_idx]
        e = np.exp(top_vals - top_vals.max())
        probs = e / e.sum()
        choice = np.random.choice(len(top_idx), p=probs)
        return int(top_idx[choice])

    def chat(self, question, max_tokens=30, greedy=False):
        """★ キャッシュ付きチャット
        greedy=True にすると常に最も確率が高いトークンを選ぶ。
        採点のように答えがぶれてはいけない用途で使う。
        """
        cache_key = hashlib.md5((question + ('|G' if greedy else '')).encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        def pick(lg):
            if greedy:
                return int(np.argmax(lg))
            return self.topk_sample(lg)

        tok = [self.BOS, self.QM] + self.hybrid_tokenize(question) + [self.AN]

        logits, kv_cache, pos = self.forward_prefill(tok)
        next_logits = logits[-1].astype(np.float32)
        nxt = pick(next_logits)

        result_ids = []
        for _ in range(max_tokens):
            if nxt in (self.EOS, self.PAD) or pos >= self.MAXLEN:
                break
            result_ids.append(nxt)
            logits = self.forward_one(nxt, pos, kv_cache)
            next_logits = logits[0].astype(np.float32)
            nxt = pick(next_logits)
            pos += 1

        reply = self.decode(result_ids)

        # 作者名を会社名に置き換える
        reply = reply.replace('yutoが作った自作の', 'Qzero会社が作った')
        reply = reply.replace('yutoが作りました', 'Qzero会社が作りました')
        reply = reply.replace('yutoが作った', 'Qzero会社が作った')
        # モデル名を実際のものに置き換える(Pure が Apex と名乗らないように)
        if getattr(self, 'model_name', 'Apex') not in ('', 'Apex'):
            _mn = self.model_name
            reply = reply.replace('Qstart Apex', 'Qstart ' + _mn)
            reply = reply.replace('Apexです', _mn + 'です')
            reply = reply.replace('Apexといい', _mn + 'といい')

        if self._cache_max > 0:
            while len(self._cache) >= self._cache_max:
                del self._cache[next(iter(self._cache))]
            self._cache[cache_key] = reply

        return reply


    def generate(self, start, max_tokens=30):
        tok = [self.BOS] + self.hybrid_tokenize(start)
        logits, kv_cache, pos = self.forward_prefill(tok)
        next_logits = logits[-1].astype(np.float32)
        nxt = self.topk_sample(next_logits)

        result_ids = list(tok[1:])
        for _ in range(max_tokens):
            if nxt in (self.EOS, self.PAD) or pos >= self.MAXLEN:
                break
            result_ids.append(nxt)
            logits = self.forward_one(nxt, pos, kv_cache)
            next_logits = logits[0].astype(np.float32)
            nxt = self.topk_sample(next_logits)
            pos += 1

        return self.decode(result_ids)


if __name__ == '__main__':
    import sys
    weights = sys.argv[1] if len(sys.argv) > 1 else 'weights.npz'
    config = sys.argv[2] if len(sys.argv) > 2 else 'config.json'

    equi = EquiInference(weights, config)

    print('\n=== 速度テスト ===')
    import time
    for q in ['こんにちは', '日本の首都はどこ', 'AIとは何', '猫は何を食べる']:
        t0 = time.time()
        a = equi.chat(q)
        t1 = time.time()
        print(f'  Q: {q}')
        print(f'  A: {a}')
        print(f'  ⏱ {t1-t0:.2f}秒')
        print()

    # 2回目（キャッシュヒット）
    print('=== キャッシュテスト（2回目は瞬時） ===')
    for q in ['こんにちは', '日本の首都はどこ']:
        t0 = time.time()
        a = equi.chat(q)
        t1 = time.time()
        print(f'  Q: {q} → {a} ({t1-t0:.4f}秒)')
