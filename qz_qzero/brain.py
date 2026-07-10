# ============================================
# Cerebro の脳みそ(独立モジュール)
# クイズシェア本体とは切り離した、AI専用のコード
# 将来ここごと別サーバー(Render等)に引っ越せる設計
# ============================================
import json, re, math, os

BRAIN_PATH = os.environ.get('QZERO_BRAIN', '/home/yuto113/qzero_brain.json')
_brain = None

def _load():
    global _brain
    if _brain is None:
        with open(BRAIN_PATH, encoding='utf-8') as f:
            _brain = json.load(f)
    return _brain

def to_words(sentence):
    # 問題文を隣り合う2文字のペアに分ける(学習時と同じルール)
    s = re.sub(r'\s+', '', sentence)
    return [s[i:i+2] for i in range(len(s) - 1)] if len(s) >= 2 else [s]

def predict_subject(text):
    # 問題文の教科を予測して、教科と確信度(%)を返す
    b = _load()
    words = to_words(text)
    scores = {}
    for subject, cnt in b['subjects'].items():
        score = math.log(cnt / b['total_docs'])
        wc = b['word_count'][subject]
        total_words = sum(wc.values())
        V = b['vocab_size']
        for w in words:
            score += math.log((wc.get(w, 0) + 1) / (total_words + V))
        scores[subject] = score
    # logのスコアを、合計100%の確信度に変換する(softmax)
    best = max(scores.values())
    exp = {s: math.exp(v - best) for s, v in scores.items()}
    total = sum(exp.values())
    ranked = sorted(exp.items(), key=lambda x: -x[1])
    top_subject, top_exp = ranked[0]
    confidence = round(top_exp / total * 100, 1)
    return {'subject': top_subject, 'confidence': confidence,
            'ranking': [{'subject': s, 'percent': round(e / total * 100, 1)} for s, e in ranked[:3]]}

# ---- 意図(インテント)を読む: 人が何をしたいかを言葉から当てる ----
def detect_intent(text):
    t = text.strip()
    low = t.lower()
    # あいさつ
    if re.search(r'こんにち|こんばん|おはよう|やあ|ハロー|hello|hi|はじめまして', low):
        return 'greeting'
    # 自己紹介・使い方
    if re.search(r'なにができ|何ができ|使い方|つかいかた|だれ|誰|自己紹介|help|ヘルプ|できること', low):
        return 'about'
    # クイズを探して(◯◯のクイズ)
    if re.search(r'クイズ.*(出|だ|ちょうだい|ください|みせ|見せ|さがし|探)|(出題|問題).*(して|出)', low):
        return 'find_quiz'
    # 教科あて(これ何科?)
    if re.search(r'何科|なにか|なんか|教科|きょうか|ジャンル|分類|判定', low):
        return 'classify'
    return 'unknown'

def similarity(a, b):
    # 2つの文の「にてる度」を、共通の文字ペアの割合で測る(0〜1)
    wa, wb = set(to_words(a)), set(to_words(b))
    if not wa or not wb:
        return 0.0
    common = len(wa & wb)
    return common / max(len(wa), len(wb))

def match_pattern(text, patterns, threshold=0.5):
    # 会話パターン(こう来たら・こう返す)の中から、今の入力に合うものを探す
    # 判定は「文の似てる度」と「キーワードが含まれるか」の合わせ技
    best, best_score = None, 0.0
    for p in patterns:
        trigger = p['trigger']
        # (A) 似てる度
        sim = similarity(text, trigger)
        # (B) キーワード: triggerの言葉がそのままtextに入っていたら加点
        kw = 1.0 if (len(trigger) >= 2 and trigger in text) else 0.0
        score = max(sim, kw)
        if score > best_score:
            best_score, best = score, p
    if best and best_score >= threshold:
        return best
    return None

def match_memory(text, memories, threshold=0.6):
    # 覚えた質問の中から、今の質問に一番にてるものを探す
    # threshold(しきい値)以上ならヒット。きびしめ=0.6
    best, best_sim = None, 0.0
    for mem in memories:
        sim = similarity(text, mem['question'])
        if sim > best_sim:
            best_sim, best = sim, mem
    if best and best_sim >= threshold:
        return best
    return None

def strip_command(text):
    # 「◯◯のクイズ出して」から ◯◯(お題)を取り出す
    t = re.sub(r'(の)?(クイズ|問題|もんだい).*$', '', text)
    t = re.sub(r'(を|について|に関する|の)$', '', t.strip())
    return t.strip()
