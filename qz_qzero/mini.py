# ============================================
# QZERO Mini: 自作トランスフォーマーの文章生成
# 学習済みの脳みそ(qzero_mini_brain.json)を読み込んで動く
# ============================================
import json, math, os, random

BRAIN_PATH = os.environ.get('QZERO_MINI_BRAIN', '/home/yuto113/qzero_mini_brain.json')

# 選べる世代の台帳(新しい世代を作ったらここに1行足す)
BRAINS = {
    '12':  '/home/yuto113/qzero_mini_brain_v12.json',
    '11':  '/home/yuto113/qzero_mini_brain_v11.json',
    '10':  '/home/yuto113/qzero_mini_brain_v10.json',
    '9':   '/home/yuto113/qzero_mini_brain_v9.json',
    '8':   '/home/yuto113/qzero_mini_brain_v8.json',
    '7':   '/home/yuto113/qzero_mini_brain_v7.json',
    '6':   '/home/yuto113/qzero_mini_brain_v6.json',
    '5':   '/home/yuto113/qzero_mini_brain_v5.json',
    '4.1': '/home/yuto113/backups/qzero_mini_brain_v41.json',
    '3':   '/home/yuto113/backups/qzero_mini_brain_v3.json',
}
DEFAULT_VERSION = '12'
_cache = {}

def _load(version=None):
    v = version if version in BRAINS else DEFAULT_VERSION
    if v not in _cache:
        with open(BRAINS[v], encoding='utf-8') as f:
            b = json.load(f)
        b['w2i'] = {w: i for i, w in enumerate(b['words'])}
        _cache[v] = b
    return _cache[v]

def versions():
    return [v for v in BRAINS if os.path.exists(BRAINS[v])]

def _softmax(xs):
    m = max(xs)
    es = [math.exp(min(x - m, 60)) for x in xs]
    s = sum(es)
    return [e / s for e in es]

def _mv(M, v, dim):
    return [sum(M[i][j] * v[j] for j in range(dim)) for i in range(len(M))]

def _forward(ctx, b):
    DIM = b['DIM']
    n = len(ctx)
    xs = [[b['emb'][ctx[t]][d] + b['pos'][t][d] for d in range(DIM)] for t in range(n)]
    if 'layers' in b:
        return _forward_v6(xs, b)
    q = _mv(b['Wq'], xs[-1], DIM)
    ks = [_mv(b['Wk'], x, DIM) for x in xs]
    vs = [_mv(b['Wv'], x, DIM) for x in xs]
    sc = [sum(q[d] * k[d] for d in range(DIM)) / math.sqrt(DIM) for k in ks]
    at = _softmax(sc)
    cv = [sum(at[t] * vs[t][d] for t in range(n)) for d in range(DIM)]
    return _softmax(_mv(b['Wo'], cv, DIM))

def _attend_v6(xs, W, DIM, HEADS):
    # マルチヘッド注意(最後の位置ぶんだけ計算)
    HD = DIM // HEADS
    n = len(xs)
    q = _mv(W['Wq'], xs[-1], DIM)
    ks = [_mv(W['Wk'], x, DIM) for x in xs]
    vs = [_mv(W['Wv'], x, DIM) for x in xs]
    ctx_vec = [0.0] * DIM
    for h in range(HEADS):
        lo, hi = h * HD, (h + 1) * HD
        sc = [sum(q[d] * k[d] for d in range(lo, hi)) / math.sqrt(HD) for k in ks]
        at = _softmax(sc)
        for d in range(lo, hi):
            ctx_vec[d] = sum(at[t] * vs[t][d] for t in range(n))
    return _mv(W['Wp'], ctx_vec, DIM)

def _forward_v6(xs, b):
    DIM, HEADS = b['DIM'], b['HEADS']
    X = [list(x) for x in xs]
    for L in b['layers']:
        h = _attend_v6(X, L, DIM, HEADS)
        X[-1] = [X[-1][d] + h[d] for d in range(DIM)]   # 残差接続(attention)
        # FFN(v7以降)
        if 'W1' in L:
            DM = len(X[-1])
            ffn_h = [max(0, sum(L['W1'][j][d] * X[-1][d] for d in range(DM)) + L['b1'][j]) for j in range(len(L['W1']))]
            ffn_o = [sum(L['W2'][d][j] * ffn_h[j] for j in range(len(ffn_h))) + L['b2'][d] for d in range(DM)]
            X[-1] = [X[-1][d] + ffn_o[d] for d in range(DM)]   # 残差接続(FFN)
    return _softmax(_mv(b['Wo'], X[-1], DIM))


def vocabulary(version=None):
    return [w for w in _load(version)['words'] if w != 'おわり']

def info(version=None):
    b = _load(version)
    return {'dim': b['DIM'], 'vocab': len(b['words'])}

def tokenize(text):
    # 「ねこが」みたいなスペースなし入力を、知ってる単語で自動で区切る
    # 長い単語から先に探す(「にんじん」を「に」で切らないため)
    b = _load()
    words_sorted = sorted(b['words'], key=len, reverse=True)
    tokens = []
    rest = text.replace(' ', '').replace('　', '')
    while rest:
        for w in words_sorted:
            if rest.startswith(w):
                tokens.append(w)
                rest = rest[len(w):]
                break
        else:
            # どの単語にも当てはまらない → 1文字だけ「知らない言葉」として取り出す
            tokens.append(rest[0])
            rest = rest[1:]
    return tokens

def generate(start_text, version=None):
    # 「ねこ が」でも「ねこが」でもOK: スペースがあればそれで、なければ自動区切り
    b = _load(version)
    if ' ' in start_text.strip() or '　' in start_text.strip():
        tokens = [t for t in start_text.replace('　', ' ').split() if t]
    else:
        tokens = tokenize(start_text)
    unknown = [t for t in tokens if t not in b['w2i']]
    if unknown:
        return {'ok': False, 'unknown': unknown}
    ctx = [b['w2i'][t] for t in tokens]
    if len(ctx) == 0 or len(ctx) >= b['MAXLEN']:
        return {'ok': False, 'unknown': []}
    result = list(tokens)
    for _ in range(b['MAXLEN'] - len(ctx)):
        out = _forward(ctx, b)
        # 温度サンプリング: 上位3候補から確率に応じたくじ引きで選ぶ
        # (確率を2乗して1位を有利にしつつ、2位3位にもチャンスを残す)
        top = sorted(range(len(out)), key=lambda i: -out[i])[:3]
        ws = [out[i] ** 2 for i in top]
        nxt = random.choices(top, weights=ws)[0]
        w = b['words'][nxt]
        if w == 'おわり':
            break  # 文のおわりを自分で判断できるようになった!
        result.append(w)
        ctx.append(nxt)
        if len(ctx) >= b['MAXLEN']:
            break
    return {'ok': True, 'text': ' '.join(result)}
