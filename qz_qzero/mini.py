# ============================================
# QZERO Mini: 自作トランスフォーマーの文章生成
# 学習済みの脳みそ(qzero_mini_brain.json)を読み込んで動く
# ============================================
import json, math, os

BRAIN_PATH = os.environ.get('QZERO_MINI_BRAIN', '/home/yuto113/qzero_mini_brain.json')
_b = None

def _load():
    global _b
    if _b is None:
        with open(BRAIN_PATH, encoding='utf-8') as f:
            _b = json.load(f)
        _b['w2i'] = {w: i for i, w in enumerate(_b['words'])}
    return _b

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
    q = _mv(b['Wq'], xs[-1], DIM)
    ks = [_mv(b['Wk'], x, DIM) for x in xs]
    vs = [_mv(b['Wv'], x, DIM) for x in xs]
    sc = [sum(q[d] * k[d] for d in range(DIM)) / math.sqrt(DIM) for k in ks]
    at = _softmax(sc)
    cv = [sum(at[t] * vs[t][d] for t in range(n)) for d in range(DIM)]
    return _softmax(_mv(b['Wo'], cv, DIM))

def vocabulary():
    return list(_load()['words'])

def generate(start_text):
    # 「ねこ が」みたいな書き出しから、続きを紡ぐ
    b = _load()
    tokens = [t for t in start_text.split() if t]
    unknown = [t for t in tokens if t not in b['w2i']]
    if unknown:
        return {'ok': False, 'unknown': unknown}
    ctx = [b['w2i'][t] for t in tokens]
    if len(ctx) == 0 or len(ctx) >= b['MAXLEN']:
        return {'ok': False, 'unknown': []}
    result = list(tokens)
    for _ in range(b['MAXLEN'] - len(ctx)):
        out = _forward(ctx, b)
        nxt = out.index(max(out))
        result.append(b['words'][nxt])
        ctx.append(nxt)
        if len(ctx) >= b['MAXLEN']:
            break
    return {'ok': True, 'text': ' '.join(result)}
