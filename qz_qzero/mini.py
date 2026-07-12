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

def info():
    b = _load()
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

def generate(start_text):
    # 「ねこ が」でも「ねこが」でもOK: スペースがあればそれで、なければ自動区切り
    b = _load()
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
        nxt = out.index(max(out))
        w = b['words'][nxt]
        if w == 'おわり':
            break  # 文のおわりを自分で判断できるようになった!
        result.append(w)
        ctx.append(nxt)
        if len(ctx) >= b['MAXLEN']:
            break
    return {'ok': True, 'text': ' '.join(result)}
