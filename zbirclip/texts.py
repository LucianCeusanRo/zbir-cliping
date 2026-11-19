"""Hooks (on-screen, first 2 s) and TikTok titles in Romanian when no LLM is configured."""
import random, re
from .detect import fold

HOOKS = {
    "ras_audio": ["ZBIR A MURIT DE RÂS 😂", "N-AM MAI RÂS AȘA DE MULT", "CE A FOST ASTA? 😂"],
    "ras": ["ZBIR A MURIT DE RÂS 😂", "N-AM MAI RÂS AȘA DE MULT", "ASTA M-A TERMINAT 💀", "CE A FOST ASTA? 😂"],
    "injuratura": ["ZBIR A LUAT-O RAZNA 🤬", "NERVII LUI ZBIR LA GTA 6", "ZBIR NU MAI POATE 😤", "RAGE MAXIM ÎN GTA 6"],
    "exclamatie": ["NU SE POATE CE A PĂȚIT ZBIR", "REACȚIA LUI ZBIR 😱", "STAI SĂ VEZI FINALUL", "NIMENI NU SE AȘTEPTA"],
    "giveaway": ["ZBIR DĂ PS5 CU GTA 6 🎁", "GIVEAWAY NEBUN LA ZBIR", "CINE CÂȘTIGĂ PS5-UL?"],
    "actiune": ["CEA MAI TARE FAZĂ DIN GTA 6", "ZBIR ÎN VICE CITY = HAOS", "GTA 6 CU ZBIR E ALTCEVA"],
    "chat": ["CHATUL L-A ENERVAT PE ZBIR", "ZBIR VS CHATUL 😂", "CE I-A ZIS CHATUL LUI ZBIR"],
    "_": ["ZBIR ÎNCHIS 24/7 PÂNĂ TERMINĂ GTA 6", "MOMENT DE AUR CU ZBIR", "STAI PÂNĂ LA FINAL 😂"],
}
TITLES = {
    "ras_audio": ["Zbir n-a mai putut de râs 😂", "Cel mai amuzant moment din live 😂", "Am plâns de râs la asta 😭",
                  "Râsul lui Zbir e contagios 😂", "Nu te uita dacă nu vrei să râzi 😂"],
    "ras": ["Zbir n-a mai putut de râs 😂", "Am plâns de râs la asta 😭", "Cel mai amuzant moment din live 😂",
            "Asta m-a terminat 💀", "Nu te uita dacă nu vrei să râzi 😂", "Am dat replay de 10 ori 😭"],
    "injuratura": ["Zbir și-a pierdut răbdarea în GTA 6 🤬", "Nervii lui Zbir la GTA 6 😤", "Rage maxim pe live 💀",
                   "GTA 6 l-a scos din minți pe Zbir 🤬", "Când GTA 6 nu te ascultă 😤", "Zbir la 30 de ore fără somn 💀"],
    "exclamatie": ["Reacția lui Zbir e de nota 10 😱", "Nu se aștepta nimeni la asta 😳", "Zbir a rămas fără cuvinte 😂",
                   "Uită-te până la final 😳", "Fața lui Zbir când a văzut asta 😱", "Nu se poate ce s-a întâmplat 😭"],
    "giveaway": ["Zbir dă PS5 cu GTA 6 pe live 🎁", "Giveaway nebun la Zbir 🔥", "Cine câștigă PS5-ul? 🎮",
                 "Zbir a anunțat premiul 🎁 #zbirgta6", "Vrei PS5 cu GTA 6? Uite cum 🎁"],
    "actiune": ["Zbir în Vice City = haos total 🔥", "GTA 6 cu Zbir e altă poveste 😂", "Prima zi în GTA 6 cu Zbir 🔥",
                "Așa se joacă GTA 6 în România 🇷🇴", "Zbir a stricat GTA 6 😂", "Vice City n-a mai văzut așa ceva 🔥"],
    "chat": ["Chatul l-a terminat pe Zbir 😂", "Zbir vs chat, runda 1 🥊", "Chatul n-are milă de Zbir 😂",
             "Zbir citește chatul și regretă 💀", "Chatul a avut dreptate 😂"],
    "_": ["Zbir închis 24/7 până termină GTA 6 😂", "Momente tari din live-ul lui Zbir 🔥", "Ziua X în apartament cu GTA 6 🔒",
          "Zbir nu iese până nu termină GTA 6 🔒", "Live-ul ăsta e istorie 🔥", "POV: ești în chat la Zbir 😂"],
}
# viral title patterns built around a short quote he actually said ("„NU SE POATE, FRATE!” 😭")
QUOTE_TITLES = ["„{q}” 😭", "„{q}” – Zbir, 2026 😂", "Zbir: „{q}” 💀", "Când Zbir zice „{q}” 😂"]
PRIORITY = ["ras", "ras_audio", "giveaway", "injuratura", "chat", "exclamatie", "actiune"]


def category(cats):
    for p in PRIORITY:
        if p in cats:
            return p
    return "_"


def quote_hook(words, peak_rel):
    """Short phrase he actually shouted near the peak, as a quote hook: „NU SE POATE, FRATE!”"""
    near = [w for w in words if abs(w["s"] - peak_rel) < 4]
    if not (3 <= len(near) <= 14):
        return None
    loud = " ".join(w["w"] for w in near[:6]).strip(" ,.")
    if len(loud) < 8 or len(loud) > 40:
        return None
    if not re.search(r"(?i)(nu |ce |doamne|frate|bă|băi|hai|gata|stai|aoleu|mamă|vai)", loud):
        return None
    return f"„{loud.upper()}”"


def quote(words_rel, max_words=6):
    """A short exclamation from the clip, usable inside a title."""
    txt = " ".join(w["w"] for w in words_rel)
    for sent in re.split(r"(?<=[.!?])\s+", txt):
        sent = sent.strip(" ,")
        n = len(sent.split())
        if 2 <= n <= max_words and re.search(r"(?i)(nu se poate|doamne|frate|aoleu|mamă|vai|bă|băi|gata|ce-i asta|ce faci)", sent):
            return sent.rstrip(".")
    return None


def new_title(cat, words_rel=(), avoid=(), rnd=random):
    """A title different from the ones in `avoid` (used by '🔄 Refă textul')."""
    pool = list(TITLES.get(cat, TITLES["_"])) + TITLES["_"][:3]
    q = quote(list(words_rel))
    if q:
        pool += [t.format(q=q) for t in QUOTE_TITLES]
    fresh = [t for t in pool if t not in set(avoid)] or pool
    return rnd.choice(fresh)


# ---------- hashtags: #zbirgta6 ALWAYS first (contest entry) ----------
MANDATORY = "#zbirgta6"
TAG_RE = re.compile(r"(?<![\w#])#\w+")


def norm_tag(t):
    t = str(t).strip().strip(",;")
    t = "#" + t.lstrip("#")
    return t if re.fullmatch(r"#\w+", t) else ""


def order_tags(tags):
    """Dedupe (case-insensitive) and put #zbirgta6 first."""
    out, seen = [MANDATORY], {MANDATORY}
    for t in tags:
        t = norm_tag(t)
        if t and t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out


def pick_hashtags(c, rnd=random):
    """#zbirgta6 + your fixed hashtags + a few random popular ones, max `hashtags_max` in total."""
    fixed = order_tags(c.get("hashtags") or [])
    mx = max(1, int(c.get("hashtags_max", 5)))
    pool = [t for t in order_tags(c.get("extra_hashtags") or [])[1:] if t.lower() not in {f.lower() for f in fixed}]
    rnd.shuffle(pool)
    return (fixed + pool)[:max(mx, 1)] if len(fixed) <= mx else fixed


def finalize_caption(text):
    """Text first, then hashtags, with #zbirgta6 as the FIRST hashtag. Hashtags already in the text are kept (moved
    to the end, after #zbirgta6)."""
    text = str(text or "")
    found = TAG_RE.findall(text)
    body = " ".join(TAG_RE.sub(" ", text).split())
    return (body + " " + " ".join(order_tags(found))).strip()[:2000]


def make(cand, words_rel, rnd=random):
    cat = category(cand.get("cats", []))
    hook = quote_hook(words_rel, cand["peak"] - cand["start"]) or rnd.choice(HOOKS[cat])
    return hook, rnd.choice(TITLES[cat]), cat
