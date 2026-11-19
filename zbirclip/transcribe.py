"""Romanian speech-to-text with faster-whisper (language forced to 'ro', word timestamps).

Diacritics: Whisper sometimes writes the old cedilla forms (ş ţ) or "ã"; we normalise everything to the
correct comma-below letters (ș ț) and ă, so the burned captions are proper Romanian.
"""
import re, threading, wave
import numpy as np
from . import progress
from .util import cfg, log, setup_cuda_dlls

_model = None
_lock = threading.Lock()

DIACRITICS = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț", "ã": "ă", "Ã": "Ă"})
# typical Whisper hallucinations on silence / music in Romanian
HALLU = re.compile(r"(?i)(subtitrar\w*|mulțumesc pentru vizionare|abonează-te|nu uitați să vă abonați|"
                   r"traducerea|www\.|\.com|amara\.org)")


def fix_ro(s):
    return s.translate(DIACRITICS)


def model():
    global _model
    with _lock:
        if _model is None:
            setup_cuda_dlls()
            from faster_whisper import WhisperModel
            c = cfg()
            dev = c.get("whisper_device", "auto")
            if dev == "auto":
                try:
                    import ctranslate2
                    dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    dev = "cpu"
            ct = c.get("whisper_compute_type", "auto")
            if ct == "auto":
                ct = "float16" if dev == "cuda" else "int8"
            name = c.get("whisper_model", "large-v3")
            log.info("incarc Whisper %s pe %s (%s)...", name, dev, ct)
            try:
                _model = WhisperModel(name, device=dev, compute_type=ct)
            except Exception as e:
                if dev != "cuda":
                    raise
                log.warning("GPU indisponibil pentru Whisper (%s) -> CPU", str(e)[:120])
                _model = WhisperModel(name, device="cpu", compute_type="int8")
        return _model


def load_wav(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


_busy = threading.Lock()        # one transcription at a time (Zbir worker + "Generează clipuri" jobs share the GPU)


def transcribe(wav_path):
    """-> list of words {'w','s','e','p'} (p = probability) and list of segments {'s','e','t'}."""
    if not _busy.acquire(blocking=False):
        progress.stage("transcriere", 0, "aștept placa video (alt job transcrie acum)…")
        _busy.acquire()
    try:
        progress.stage("transcriere", 0, "pornesc Whisper…")
        return _transcribe(wav_path)
    finally:
        _busy.release()


def _mmss(t):
    t = int(t or 0)
    return f"{t // 60}:{t % 60:02d}"


def _transcribe(wav_path):
    c = cfg()
    pcm = load_wav(wav_path)
    segs, info = model().transcribe(
        pcm, language=c.get("language", "ro"), task="transcribe", word_timestamps=True, beam_size=5,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
        condition_on_previous_text=False, initial_prompt=c.get("initial_prompt") or None,
        no_speech_threshold=0.6, compression_ratio_threshold=2.4)
    words, sents = [], []
    total = float(getattr(info, "duration", 0) or len(pcm) / 16000) or 1.0
    for s in segs:
        progress.pct(100 * s.end / total, detail=f"{_mmss(s.end)} / {_mmss(total)} audio")
        txt = _NON_LATIN.sub("", fix_ro(s.text.strip())).strip()
        if not txt or HALLU.search(txt) or s.avg_logprob < -1.2:
            continue
        txt = " ".join(restore_diacritics(x) for x in txt.split())
        sents.append({"s": round(s.start, 2), "e": round(s.end, 2), "t": txt})
        for w in (s.words or []):
            t = clean_word(re.sub(r"(\w)\1{3,}", r"\1\1\1", fix_ro(w.word.strip())))
            if t:
                t = restore_diacritics(t)   # "Aaaaaaaaa" -> "Aaa"
            if t and keep_word(t, w.probability):
                words.append({"w": t, "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 2)})
    return merge_clitics(words), dedupe_loops(sents)


# Whisper sometimes writes a whole segment without diacritics ("sa faca", "pana", "bai").  For such segments we
# restore only words whose diacritic-less form is (practically) never a correct Romanian word in speech.
NO_DIAC = {
    "si": "și", "sa": "să", "daca": "dacă", "pana": "până", "bai": "băi", "pai": "păi", "ma": "mă",
    "intai": "întâi", "stiu": "știu", "stii": "știi", "asa": "așa", "cand": "când", "inca": "încă", "langa": "lângă",
    "fara": "fără", "ati": "ați", "iti": "îți", "imi": "îmi", "isi": "își", "cat": "cât", "intr": "într", "in": "în",
    "ii": "îi", "inainte": "înainte", "ala": "ăla", "aia": "aia", "astia": "ăștia", "alea": "alea", "vad": "văd",
    "faca": "facă", "frane": "frâne", "multumesc": "mulțumesc", "multumim": "mulțumim", "bagat": "băgat",
    "dupa": "după", "intre": "între", "inapoi": "înapoi", "incerc": "încerc", "mancare": "mâncare", "manca": "mânca",
    "doamne": "doamne", "pacat": "păcat", "baieti": "băieți", "baiat": "băiat",
    "omoara": "omoară", "omorat": "omorât", "tine": "tine", "atata": "atâta", "niste": "niște",
    "fi": "fi", "ia": "ia", "aiurea": "aiurea", "pe": "pe", "ce": "ce", "cum": "cum", "vazut": "văzut", "masina": "mașina",
    "masini": "mașini", "facut": "făcut", "faceti": "faceți", "raspund": "răspund", "odata": "odată",
    "gasesti": "găsești", "gasit": "găsit", "placere": "plăcere", "decat": "decât", "seamana": "seamănă",
    "uitati": "uitați", "stiti": "știți", "cauti": "cauți", "copilaria": "copilăria", "vorbesti": "vorbești",
    "fratilor": "fraților", "nimanui": "nimănui", "cateva": "câteva", "dracu": "dracu", "parca": "parcă", "politia": "poliția", "politie": "poliție", "oras": "oraș", "usa": "ușa", 
}
_HAS_DIAC = re.compile("[ăâîșțĂÂÎȘȚ]")


def restore_diacritics(t):
    m = re.match(r"^(\W*)([\w-]+?)(\W*)$", t)
    if not m:
        return t
    pre, core, post = m.groups()
    parts = core.split("-")
    out = []
    for w in parts:
        r = NO_DIAC.get(w.lower())
        if r is None:
            out.append(w)
        elif w.isupper() and len(w) > 1:
            out.append(r.upper())
        elif w[:1].isupper():
            out.append(r[:1].upper() + r[1:])
        else:
            out.append(r)
    return pre + "-".join(out) + post


_FOREIGN = re.compile(r"(?i)(w|y|k|q|th|ck|sh|oo|ee|ing\b|tion\b)")


_NON_LATIN = re.compile(r"[^\u0000-\u024F\u0218-\u021B\u2018-\u201E\u2026\U0001F300-\U0001FAFF]")


def clean_word(t):
    """Remove stray non-Latin hallucinated characters (Whisper sometimes emits Thai/Cyrillic glyphs on noise)."""
    t = _NON_LATIN.sub("", t)
    return t if re.search(r"\w", t) else ""


def keep_word(t, p):
    """Drop words Whisper is unsure about AND that look English (game audio hallucinations: 'slippery',
    'surprisingly'). Unsure Romanian words are kept (noisy stream audio lowers every probability)."""
    if p >= 0.2:
        return True
    if p < 0.02:
        return False
    return not _FOREIGN.search(t) or t.lower() in ("bro", "ok", "okay", "wow", "gta", "jason", "lucia", "show", "like")


def merge_clitics(words):
    """Whisper splits Romanian hyphenated forms: 's' + '-a', 'într' + '-o', 'n' + '-am' -> join them."""
    out = []
    for w in words:
        t = w["w"]
        if out and t.startswith("-") and not out[-1]["w"].endswith((".", "?", "!", ",")):
            # '-am', '-a', '-o' never stand alone in Romanian -> glue to the previous word whatever the gap
            out[-1] = dict(out[-1], w=(out[-1]["w"] + t).replace("--", "-"), e=w["e"])
        elif out and out[-1]["w"].endswith("-") and w["s"] - out[-1]["e"] < 0.5:
            out[-1] = dict(out[-1], w=(out[-1]["w"] + t).replace("--", "-"), e=w["e"])
        else:
            t = t.lstrip("-")
            if t:
                out.append(dict(w, w=t))
    return out


def dedupe_loops(sents):
    """Drop Whisper repetition loops (same sentence 3+ times in a row)."""
    out = []
    for s in sents:
        if len(out) >= 2 and s["t"].lower() == out[-1]["t"].lower() == out[-2]["t"].lower():
            continue
        out.append(s)
    return out
