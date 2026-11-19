"""'Șansă de viral' 0-100% for a clip, from the same signals that picked it, with a per-factor breakdown for the UI.
Heuristic (not a promise): strong hook + emotion + keywords + chat spike + a TikTok-friendly length."""

STRONG = {"ras", "ras_audio", "injuratura", "giveaway"}
LABELS = {"hook": "Hook (primele 2 s)", "emotie": "Emoție / volum (țipete, râs)", "cuvinte": "Cuvinte cheie (râs, nervi, „nu se poate”)",
          "chat": "Chatul explodează", "durata": "Durată potrivită (20–35 s ideal)", "ai": "Nota AI (LLM)"}
WEIGHTS = {"hook": 0.25, "emotie": 0.30, "cuvinte": 0.15, "chat": 0.15, "durata": 0.15}


def length_factor(dur):
    if 18 <= dur <= 35:
        return 1.0
    if dur < 18:
        return max(0.3, dur / 18)
    if dur <= 45:
        return 0.8
    if dur <= 60:
        return 0.55
    return 0.35


def compute(feat, dur, hook_is_quote=False, cat="_", words_first2=0, llm_score=None):
    """feat: {'loud','laugh','chat_z','nkw'} from detect.find_moments. Returns (percent, factors{name: 0-100})."""
    f = {
        "hook": min(1.0, 0.35 + 0.3 * bool(hook_is_quote) + 0.2 * (cat in STRONG) + 0.15 * min(1.0, words_first2 / 3)),
        "emotie": min(1.0, 0.6 * min(1.0, float(feat.get("loud") or 0) / 3) + 0.4 * min(1.0, float(feat.get("laugh") or 0) / 2)),
        "cuvinte": min(1.0, float(feat.get("nkw") or 0) / 3),
        "durata": length_factor(dur),
    }
    w = dict(WEIGHTS)
    if feat.get("chat_z") is None:          # no chat data for this part -> don't punish, just ignore the factor
        w.pop("chat")
    else:
        f["chat"] = min(1.0, float(feat["chat_z"]) / 3)
    base = sum(w[k] * f[k] for k in w) / sum(w.values())
    if llm_score is not None:
        f["ai"] = max(0.0, min(1.0, float(llm_score) / 10))
        base = 0.6 * base + 0.4 * f["ai"]
    pct = int(round(5 + 90 * base))
    return pct, {k: int(round(100 * v)) for k, v in f.items()}


def fallback(score):
    """Old clips without stored factors: rough mapping from the detector score."""
    return int(max(5, min(95, 20 + 12 * float(score or 0))))


def tooltip(factors):
    if not factors:
        return "estimare din scorul detectorului"
    return "\n".join(f"{LABELS.get(k, k)}: {v}%" for k, v in factors.items())
