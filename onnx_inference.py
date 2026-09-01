# ============================================================
# ONNX Runtime 推論エンジン
# numpy版と同じインターフェースで、より速く動く
# ============================================================
import numpy as np
import json, os, hashlib, io, urllib.request


class OnnxInference:
    def __init__(self, onnx_path, config_path):
        print('ONNX 読み込み中...', flush=True)

        # config
        if str(config_path).startswith('http'):
            req = urllib.request.Request(config_path, headers={'User-Agent': 'Qstart/1.6'})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = json.loads(r.read().decode('utf-8'))
        else:
            with open(config_path, encoding='utf-8') as f:
                raw = json.load(f)

        self.vocab = raw['vocab']
        self.word_tokens = set(raw.get('word_tokens', []))
        self.config = raw['config']
        self.MAXLEN = self.config['MAXLEN']
        self.V = self.config['V']
        self.w2i = {w: i for i, w in enumerate(self.vocab)}
        self.i2w = {i: w for i, w in enumerate(self.vocab)}
        self.PAD = self.w2i['<PAD>']
        self.BOS = self.w2i['<BOS>']
        self.EOS = self.w2i['<EOS>']
        self.UNK = self.w2i['<UNK>']
        self.QM = self.w2i['？']
        self.AN = self.w2i['：']

        # モデル本体
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1      # PythonAnywhereは1コア想定
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        if str(onnx_path).startswith('http'):
            import time as _t
            print('  リモート取得中...', flush=True)
            t0 = _t.time()
            req = urllib.request.Request(onnx_path, headers={'User-Agent': 'Qstart/1.6'})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            print(f'  取得完了 {len(data)/1e6:.0f}MB ({_t.time()-t0:.1f}秒)', flush=True)
            self.sess = ort.InferenceSession(data, opts,
                                             providers=['CPUExecutionProvider'])
        else:
            self.sess = ort.InferenceSession(onnx_path, opts,
                                             providers=['CPUExecutionProvider'])

        self.model_name = 'Apex'
        self._cache = {}
        self._cache_max = 200
        print(f'  語彙: {self.V}  準備完了!', flush=True)

    def hybrid_tokenize(self, s):
        out, i, L = [], 0, len(s)
        while i < L:
            hit = None
            for n in (4, 3, 2):
                w = s[i:i+n]
                if w in self.word_tokens:
                    hit = w; break
            if hit:
                out.append(self.w2i[hit]); i += len(hit)
            else:
                out.append(self.w2i.get(s[i], self.UNK)); i += 1
        return out

    def decode(self, ids):
        return ''.join(self.i2w.get(i, '') for i in ids if i > 7)

    def chat(self, question, max_tokens=30, greedy=False):
        key = hashlib.md5((question + ('|G' if greedy else '')).encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]

        t = [self.BOS, self.QM] + self.hybrid_tokenize(question) + [self.AN]
        result = []
        for _ in range(max_tokens):
            if len(t) >= self.MAXLEN:
                break
            x = np.array([t], dtype=np.int64)
            logits = self.sess.run(None, {'tokens': x})[0][0, -1]
            if greedy:
                nxt = int(np.argmax(logits))
            else:
                k = 20
                idx = np.argpartition(-logits, k)[:k]
                p = np.exp(logits[idx] - logits[idx].max())
                p = p / p.sum()
                nxt = int(np.random.choice(idx, p=p))
            if nxt in (self.EOS, self.PAD, self.BOS):
                break
            result.append(nxt)
            t.append(nxt)

        reply = self.decode(result)
        reply = reply.replace('yutoが作った自作の', 'Qzero会社が作った')
        reply = reply.replace('yutoが作りました', 'Qzero会社が作りました')
        reply = reply.replace('yutoが作った', 'Qzero会社が作った')
        if getattr(self, 'model_name', 'Apex') not in ('', 'Apex'):
            mn = self.model_name
            reply = reply.replace('Qstart Apex', 'Qstart ' + mn)
            reply = reply.replace('Apexです', mn + 'です')
            reply = reply.replace('Apexといい', mn + 'といい')

        if self._cache_max > 0:
            while len(self._cache) >= self._cache_max:
                del self._cache[next(iter(self._cache))]
            self._cache[key] = reply
        return reply
