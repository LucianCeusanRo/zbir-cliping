"""Find FUNNY / viral moments in a piece of stream (Romanian).

Per-second signals (ideas combined from the original gtavi-claudecode moments.py, Auto-Clipper,
SharkyClips and HighlightMiner):
  loud      loudness jump above the local level (screams, rage)
  yell      loud AND bright (energy 1-4 kHz) = shouting, not just game explosions
  laugh     laughter heuristic: 3-7 Hz bursty envelope + "haha" style words
  words     Romanian funny/rage/swear/exclamation/giveaway keywords in the transcript
  rate      speech rate spike (fast excited talk)
  chat      live chat messages per second (hype messages count double)
Clips get build-up before the peak and the payoff after it, snapped to sentence boundaries.
"""
import re
import numpy as np

# weight, regex (lowercase, diacritics-insensitive matching is done on a folded copy)
KEYWORDS = [
    (2.0, r"\b(ha){2,}|\bhah+a*|\bahah\w*|\bhihi\w*|\bmor de r[aâ]s|\bm-am pi[sș]at|\bplm\b|\bxd\b"),          # laughter
    (1.6, r"\b(pula|pizd\w*|muie|futu\w*|fut\b|ba[sș]it\w*|c[aă]cat\w*|dracu\w*|naiba|ce pl\w*|bag[aă]-?mi-?a[sș]|"
          r"bou\b|prost\w*|idiot\w*|nebun\w*|retardat\w*|jeg\w*)"),                                           # swears / rage
    (1.3, r"\b(doamne|aoleu|aolo|vai|mam[aă] mam[aă]|nu se poate|nu-?mi vine s[aă] cred|ce-?i asta|ce e asta|"
          r"ce faci|ce dracu|ce naiba|stai stai|nu nu nu|frate frate|b[aă]i b[aă]i|hai hai|gata gata|fugi|"
          r"ajutor|sari|ce tare|incredibil|halucinant|wow|bro|omg|what)"),                                     # exclamations
    (1.2, r"\b(giveaway|c[aâ][sș]tig\w*|ps5|premiu\w*|tragere|c[aâ][sș]tig[aă]tor\w*|bmw|mercedes|bani|"
          r"donat\w*|super ?chat|abonat\w*)"),                                                                 # giveaway hype
    (0.8, r"\b(poli[tț]i\w*|ma[sș]in\w*|accident|explod\w*|moare|murit|omor\w*|jaf|banc[aă]|evad\w*|"
          r"lucia|jason|vice city|misiun\w*)"),                                                                # GTA action
    (1.0, r"\b(chat\w*|voi|v-am zis|ce zice[tț]i|scrie[tț]i|uita[tț]i-v[aă])"),                                 # banter with chat
]
_RX = [(w, re.compile(p, re.I)) for w, p in KEYWORDS]


def fold(s):
    return s.lower().translate(str.maketrans("ăâîșțşţ", "aaistst"))


def _kw_score(text):
    t = fold(text); s = 0.0; cats = []
    for (w, rx), name in zip(_RX, ["ras", "injuratura", "exclamatie", "giveaway", "actiune", "chat"]):
        n = len(rx.findall(t))
        if n:
            s += w * min(n, 3); cats.append(name)
    return s, cats


def z(a):
    a = np.asarray(a, float)
    sd = a.std()
    return np.zeros_like(a) if sd < 1e-9 else (a - np.median(a)) / sd


def smooth(a, k):
    k = max(1, int(k)); return np.convolve(a, np.ones(k) / k, mode="same")


def audio_features(pcm, sr=16000):
    """Per-second loudness (dB), brightness ratio and laughter-likeness."""
    n = len(pcm) // sr
    x = pcm[: n * sr].reshape(n, sr)
    rms = np.sqrt((x ** 2).mean(1) + 1e-10)
    db = 20 * np.log10(rms)
    # brightness: share of spectral energy in 1-4 kHz (shouting voices) per second
    spec = np.abs(np.fft.rfft(x * np.hanning(sr), axis=1)) ** 2
    f = np.fft.rfftfreq(sr, 1 / sr)
    band = spec[:, (f > 1000) & (f < 4000)].sum(1); tot = spec[:, (f > 80) & (f < 8000)].sum(1) + 1e-9
    bright = band / tot
    # laughter: envelope (100 Hz) modulated at 3-7 Hz, i.e. "ha-ha-ha" bursts
    hop = sr // 100
    env = np.sqrt((pcm[: n * sr].reshape(-1, hop) ** 2).mean(1) + 1e-10).reshape(n, 100)
    laugh = np.zeros(n)
    for i in range(n):
        e = env[max(0, i - 1): i + 1].ravel()
        e = e - e.mean()
        if e.std() < 1e-4:
            continue
        p = np.abs(np.fft.rfft(e * np.hanning(len(e)))) ** 2
        fr = np.fft.rfftfreq(len(e), 0.01)
        laugh[i] = p[(fr >= 3) & (fr <= 7)].sum() / (p[(fr >= 0.5) & (fr <= 15)].sum() + 1e-9)
    return db, bright, laugh


def hook_trim(s0, e0, peak, words, score, mn, mx, n):
    """Hook-first: no dead air at the start (first word within 0.3 s), the payoff fully inside (keep >= 2 s after
    the last strong peak), no long silence at the end. Never shorter than mn / longer than mx."""
    inside = [w for w in words if s0 <= w["s"] < e0]
    if inside:
        first = inside[0]["s"]
        if first - s0 > 0.8 and (e0 - (first - 0.3)) >= mn:
            s0 = max(0.0, first - 0.3)
    # last strong moment after the peak (laugh/yell reaction) must be followed by >= 2 s
    lo, hi = int(peak), int(min(n, s0 + mx))
    if hi > lo + 1:
        seg = score[lo:hi]
        strong = np.where(seg >= 0.7 * score[peak])[0]
        if len(strong):
            need = lo + int(strong[-1]) + 2.0
            if need > e0:
                e0 = min(need, s0 + mx, float(n))
    tail = [w for w in words if s0 <= w["s"] < e0]
    if tail:
        last = tail[-1]["e"]
        if e0 - last > 1.5 and (last + 0.8 - s0) >= mn:
            e0 = last + 0.8
    return round(s0, 2), round(min(e0, float(n)), 2)


def find_moments(pcm, words, sents, chat, new_from, c):
    """pcm: 16 kHz mono of the analysis window; times are window-relative seconds.
    new_from: only peaks at/after this second are new (earlier part is pre-roll from previous chunk)."""
    db, bright, laugh_a = audio_features(pcm)
    n = len(db)
    if n < c["clip_min_sec"]:
        return []
    loud = smooth(np.clip(z(db - smooth(db, 30)), 0, None), 3)
    yell = loud * np.clip(z(bright), 0, None)
    kw = np.zeros(n); rate = np.zeros(n); laugh_w = np.zeros(n); cats_at = [set() for _ in range(n)]
    for w in words:
        i = int(w["s"])
        if 0 <= i < n:
            rate[i] += 1
            s, cats = _kw_score(w["w"])
            kw[i] += s; cats_at[i].update(cats)
            if "ras" in cats:
                laugh_w[i] += 1
    for s in sents:  # multi-word phrases ("nu se poate", "mor de ras")
        sc, cats = _kw_score(s["t"])
        i = int(s["s"])
        if 0 <= i < n and len(s["t"].split()) > 1:
            kw[i] += 0.5 * sc; cats_at[i].update(cats)
        if "!" in s["t"] or "?!" in s["t"]:
            kw[min(n - 1, i)] += 0.6
    # audio laughter only counts when it's loud, strongly rhythmic and NOT plain speech (few words that second)
    sparse = np.array([1.0 if rate[i] <= 1 else 0.3 for i in range(n)])
    laugh = np.clip(z(laugh_a) - 1.5, 0, None) * (loud > 1.0) * sparse + 2 * laugh_w
    rate_z = np.clip(z(smooth(rate, 4)), 0, None)
    chat = np.asarray(chat[:n] if chat is not None and len(chat) else np.zeros(n), float)
    if len(chat) < n:
        chat = np.pad(chat, (0, n - len(chat)))
    chat_z = np.clip(z(smooth(chat, 8)), 0, None) if chat.any() else np.zeros(n)
    # setup -> reaction: a question / short line followed 1-4 s later by laughter or a yell (the "punchline" shape)
    react = np.zeros(n)
    for s in sents:
        e = int(s["e"])
        if 0 <= e < n - 1:
            after = max(float(laugh[e + 1: e + 5].max(initial=0)), float(loud[e + 1: e + 5].max(initial=0)) - 0.5)
            if after > 0.8:
                react[e: e + 4] += (1.3 if "?" in s["t"] else 0.8) * min(2.5, after)
    score = (1.0 * loud + 1.2 * smooth(yell, 3) + 1.3 * smooth(laugh, 4) + 0.9 * smooth(kw, 6)
             + 0.4 * rate_z + 0.7 * smooth(react, 3) + (1.4 * smooth(chat_z, 3) if chat.any() else 0))
    score = smooth(score, 5)
    # pick peaks
    picks, used = [], np.zeros(n, bool)
    tgt, mn, mx = c["clip_target_sec"], c["clip_min_sec"], c["clip_max_sec"]
    gap = c.get("min_gap_between_clips_sec", 20)
    starts = [s["s"] for s in sents]; ends = [s["e"] for s in sents]
    for p in np.argsort(-score):
        if len(picks) >= c["clips_per_chunk_max"] or score[p] < c["min_score"]:
            break
        if p < new_from or used[max(0, p - 3): p + 3].any():
            continue
        s0 = max(0.0, p - 0.6 * tgt); e0 = min(float(n), p + 0.4 * tgt)
        # snap start to the beginning of a sentence (so the clip starts on a full phrase = hook)
        cand = [s for s in starts if s0 - 6 <= s <= p - 2]
        if cand:
            s0 = min(cand, key=lambda s: abs(s - s0))
        cand = [e for e in ends if p + 3 <= e <= s0 + mx]
        if cand:
            e0 = min(cand, key=lambda e: abs(e - (s0 + tgt))) + 0.5
        e0 = min(max(e0, s0 + mn), s0 + mx, float(n))
        if e0 - s0 < mn:
            s0 = max(0.0, e0 - mn)
        s0, e0 = hook_trim(s0, e0, p, words, score, mn, mx, n)
        nwords = sum(1 for w in words if s0 <= w["s"] < e0)
        strong_chat = chat.any() and chat_z[p] > 2
        if nwords < 0.9 * (e0 - s0) and not strong_chat:    # TikTok clips need talking; skip silent/mumble parts
            used[max(0, p - 3): p + 3] = True
            continue
        a, b = int(max(0, s0 - gap)), int(min(n, e0 + gap))
        if used[int(s0):int(e0)].any():
            continue
        used[a:b] = True
        lo, hi = int(s0), int(e0)
        why = []
        if loud[p] > 1: why.append("strigat/spike volum")
        if laugh[lo:hi].max() > 1.0:
            why.append("ras")
            if laugh_w[lo:hi].sum() == 0:
                cats_at[p].add("ras_audio")
        cats = set().union(*cats_at[lo:hi]) if hi > lo else set()
        why += sorted(cats)
        if chat.any() and chat_z[p] > 1: why.append("chat explodeaza")
        picks.append({"start": round(s0, 2), "end": round(e0, 2), "peak": int(p), "score": round(float(score[p]), 2),
                      "why": why or ["energie"], "cats": sorted(cats),
                      "feat": {"loud": round(float(loud[lo:hi].max()), 2), "laugh": round(float(laugh[lo:hi].max()), 2),
                               "chat_z": round(float(chat_z[lo:hi].max()), 2) if chat.any() else None, "nkw": len(cats)},
                      "text": " ".join(w["w"] for w in words if s0 <= w["s"] < e0)})
    return sorted(picks, key=lambda x: -x["score"])
