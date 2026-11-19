"""Clip STYLES = what kind of moment a clip is (auto-tagged) + which viral editing styles are on.

Content categories (one primary per clip, picked from transcript / audio / chat signals):
  rage, fail, soc, chat, giveaway, cringe, roast, haos_gta, story, moment (fallback)
Editing styles (toggles; most map to effects.py switches):
  zoom_bass (punch-in zoom + bass/volume boost), wait_for_it ("stai așa… 👀" when the payoff comes late),
  karaoke (word-by-word highlight + bounce), sfx (vine boom / pop), ziua (series badge "ZIUA N"),
  split (cam on top, game below = split reaction), top5 (compilations "TOP 5" made from the best clips)
A disabled CONTENT category is not clipped anymore (and its existing clips are not auto-posted).
"""
import re
from .util import cfg

CATEGORIES = [   # key, label, emoji, regex on folded transcript (diacritics removed)
    ("rage", "Rage / țipete", "🤬", r"\b(pula|pizd\w*|muie|futu\w*|dracu\w*|naiba|ma enervez|m-am enervat|nervi|termin\w* cu|bag\w*-?mi)"),
    ("fail", "Fail", "💥", r"\b(am murit|iar am|nu iar|de ce|am ratat|ratat|am pierdut|am cazut|a cazut|crash|busit|m-a lovit|accident|praf|varza|ce prost am)"),
    ("soc", "Reacție șoc", "😱", r"\b(nu se poate|nu-?mi vine sa cred|ce-?i asta|ce e asta|doamne|aoleu|aolo|wow|omg|ce naiba|halucinant|incredibil|serios\?)"),
    ("chat", "Chat / donații", "💬", r"\b(chat\w*|donat\w*|super ?chat|abonat\w*|membr\w*|multumesc pentru|ce zice\w*|scrie\w*|voi ziceti)"),
    ("giveaway", "Giveaway", "🎁", r"\b(giveaway|ps5|premiu\w*|tragere|castig\w*|concurs|cadou\w*)"),
    ("cringe", "Cringe / awkward", "😬", r"\b(penibil|jenant|rusine|cringe|awkward|ce-am zis|n-am zis|stai ca|ups|pardon)"),
    ("roast", "Glume / roast", "😂", r"\b(ha){2,}|\bhah+a*|mor de ras|fraier\w*|prost\w*|bou\b|tantalau|ce glum\w*|glum\w*|ma-ta|taica-tu"),
    ("haos_gta", "Momente GTA haotice", "🚓", r"\b(politi\w*|masin\w*|explod\w*|jaf|banca|evad\w*|fugi|impusc\w*|stele|wanted|lucia|jason|vice city)"),
    ("story", "Story time", "📖", r"\b(cand eram|odata|stai sa va zic|povest\w*|am fost la|imi aduc aminte|tin minte|era o data|va zic ceva)"),
    ("moment", "Moment tare", "⚡", None),
]
LABEL = {k: f"{e} {l}" for k, l, e, _ in CATEGORIES}
_RX = {k: re.compile(r, re.I) for k, _, _, r in CATEGORIES if r}

EDIT_STYLES = [  # key, label, effects keys it controls
    ("zoom_bass", "Zoom-in dramatic + bass boost", ["zoom", "boost", "bass"]),
    ("wait_for_it", "„Stai așa… 👀” (wait for it) când poanta vine mai târziu", ["wait_for_it"]),
    ("karaoke", "Subtitrare karaoke (cuvântul spus colorat + bounce)", ["pop", "keyword_color"]),
    ("sfx", "Sunete meme (vine boom / pop)", ["sfx"]),
    ("emoji", "Emoji-uri reacție", ["emoji"]),
    ("shake", "Tremurat pe rage", ["shake"]),
    ("ziua", "Serie „ZIUA N” (badge în colț)", ["ziua_badge"]),
    ("split", "Split reacție (camera sus, jocul jos)", []),
    ("top5", "Compilații „TOP 5” (buton în pagina Stiluri)", []),
]


def fold(s):
    return (s or "").lower().translate(str.maketrans("ăâîșțşţ", "aaistst"))


def categorize(text, cats=(), feat=None):
    """-> (primary category key, all matching keys). Audio/chat signals break ties."""
    t = fold(text)
    feat = feat or {}
    hits = {}
    for k, rx in _RX.items():
        n = len(rx.findall(t))
        if n:
            hits[k] = n
    cats = set(cats or [])
    if "injuratura" in cats: hits["rage"] = hits.get("rage", 0) + 2
    if "ras" in cats or "ras_audio" in cats: hits["roast"] = hits.get("roast", 0) + 1.5
    if "giveaway" in cats: hits["giveaway"] = hits.get("giveaway", 0) + 2
    if "actiune" in cats: hits["haos_gta"] = hits.get("haos_gta", 0) + 1
    if "exclamatie" in cats: hits["soc"] = hits.get("soc", 0) + 1
    if "chat" in cats or (feat.get("chat_z") or 0) > 1.5: hits["chat"] = hits.get("chat", 0) + 1.5
    loud = float(feat.get("loud") or 0)
    if loud > 2.0:
        for k in ("rage", "soc", "fail"):
            if k in hits:
                hits[k] += 1
        if not hits:
            hits["soc"] = 1
    if not hits:
        nwords = len(t.split())
        if nwords > 70 and loud < 1.0:
            return "story", ["story"]
        return "moment", ["moment"]
    order = sorted(hits, key=lambda k: -hits[k])
    return order[0], order


def enabled_categories():
    c = cfg().get("styles") or {}
    return {k: bool(c.get(k, True)) for k, *_ in CATEGORIES}


def enabled_edit_styles():
    c = cfg().get("edit_styles") or {}
    return {k: bool(c.get(k, k not in ("ziua",))) for k, *_ in EDIT_STYLES}


def fx_overrides():
    """Edit-style toggles -> effects.py switches (applied on top of the Effects settings)."""
    on = enabled_edit_styles()
    out = {}
    for k, _, keys in EDIT_STYLES:
        if not on.get(k, True):
            for fk in keys:
                out[fk] = False
    if on.get("ziua"):
        out.setdefault("ziua_badge", True)
    return out


def stats():
    from . import db
    rows = db.q("SELECT style, COUNT(*) n, AVG(viral) v, SUM(CASE WHEN status IN ('posted','unconfirmed') THEN 1 ELSE 0 END) p "
                "FROM clips WHERE status!='deleted' GROUP BY style")
    by = {r["style"] or "moment": r for r in rows}
    en = enabled_categories()
    return [{"key": k, "label": f"{e} {l}", "enabled": en[k], "count": by[k]["n"] if k in by else 0,
             "avg_viral": round(by[k]["v"] or 0) if k in by and by[k]["v"] is not None else None,
             "posted": by[k]["p"] if k in by else 0} for k, l, e, _ in CATEGORIES]
