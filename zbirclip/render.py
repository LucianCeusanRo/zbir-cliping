"""Render a vertical 1080x1920 TikTok clip with big burned-in Romanian word-by-word captions.

Caption/ASS approach adapted from gtavi-claudecode cut.py (MIT): 1-3 word groups, current word highlighted,
pop-in animation, hook box in the first seconds, loudness -14 LUFS. Font: Montserrat Black (bundled, OFL),
which has all Romanian diacritics (ă â î ș ț). NVENC on NVIDIA GPUs, libx264 otherwise.
"""
import re, subprocess, threading
from pathlib import Path
from .util import FONTS as FONT_DIR, cfg, log, run, run_ffmpeg_progress, tool

W, H = 1080, 1920
TOP = 640                      # split layout: camera panel height
CAP_Y = {"split": 1240, "blur": 1300, "fill": 1300}
SWEARS = re.compile(r"(?i)\b(pul\w*|pizd\w*|muie\w*|futu\w*|fut\w*|c[aă]cat\w*|bulan\w*|coaie\w*|curv\w*|"
                    r"fuck\w*|shit\w*|bitch\w*)")
SLURS = re.compile(r"(?i)\b(nigg\w*|ţigan\w*|țigan\w*|tigan\w*|cioar\w*|poponar\w*|retard\w*|bozgor\w*)")
NON_BMP = re.compile(r"[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0F]")   # emoji: libass can't draw colour emoji

_enc = None
_enc_lock = threading.Lock()


def encoder():
    """h264_nvenc if it really works on this PC (test encode), else libx264."""
    global _enc
    with _enc_lock:
        if _enc:
            return _enc
        want = cfg().get("encoder", "auto")
        if want in ("auto", "h264_nvenc"):
            r = subprocess.run([tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.5",
                                "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _enc = "h264_nvenc" if r.returncode == 0 else "libx264"
        else:
            _enc = want
        log.info("encoder video: %s", _enc)
        return _enc


def ts(t):
    t = max(0.0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _mask(m):
    w = m.group(0); return w[0] + "*" * (len(w) - 1)


def clean(s):
    s = SLURS.sub(_mask, s)
    if cfg().get("censor_swears", True):
        s = SWEARS.sub(_mask, s)
    return NON_BMP.sub("", s).strip()


def esc(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(text, width):
    lines, cur = [], ""
    for wd in text.split():
        if cur and len(cur) + 1 + len(wd) > width:
            lines.append(cur); cur = wd
        else:
            cur = (cur + " " + wd).strip()
    return lines + ([cur] if cur else [])


# Bundled caption fonts (fonts/, all SIL OFL, all with ă â î ș ț comma-below).  name -> (size scale, faux bold)
FONTS = {
    "Montserrat Black": (1.0, -1),
    "Anton": (1.18, 0),
    "Bebas Neue": (1.32, 0),
    "Archivo Black": (0.96, 0),
    "Poppins Black": (0.98, 0),
    "Bangers": (1.2, 0),
}


def font_style(name):
    return FONTS.get(name, (1.0, -1))


KW_COLOR = {"injuratura": "&H003B3BFF&", "ras": "&H0055FF55&", "giveaway": "&H00FFC800&", "exclamatie": "&H00FF66FF&",
            "actiune": "&H00309CFF&", "chat": "&H00FFD000&"}        # ASS = BGR: red, green, cyan-blue, pink, orange, light blue


def build_ass(words, dur, hook, layout, fx=None, cats=None):
    """fx: effect toggles (effects.settings()); cats: keyword categories per word (effects.word_cats)."""
    c = cfg()
    fx = fx or {}
    font = c.get("caption_font", "Montserrat Black")
    scale, bold = font_style(font)
    size = int(int(c.get("caption_size", 96)) * scale); hook_size = int(84 * min(scale, 1.15))
    per = int(c.get("caption_words_per_line", 3)); hook_secs = float(c.get("hook_seconds", 2.5))
    cap_y = CAP_Y.get(layout, 1300)
    hook_y = TOP if layout == "split" else 330
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H0000E5FF,&H00000000,&H96000000,{bold},0,0,0,100,100,1,0,1,8,4,5,70,70,0,1
Style: Hook,{font},{hook_size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,{bold},0,0,0,100,100,0,0,3,22,0,5,80,80,0,1
Style: Credit,{font},34,&H40FFFFFF,&H40FFFFFF,&H80000000,&H00000000,-1,0,0,0,100,100,2,0,1,3,0,2,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    cats = cats if cats is not None and len(cats) == len(words) else [[] for _ in words]
    ws = [dict(w, w=clean(w["w"]), cats=k) for w, k in zip(words, cats) if 0 <= w["s"] < dur]
    ws = [w for w in ws if w["w"]]
    groups, cur = [], []
    for w in ws:
        if cur and (len(cur) >= per or w["s"] - cur[-1]["e"] > 0.6 or cur[-1]["w"][-1:] in ".?!,"
                    or sum(len(x["w"]) + 1 for x in cur) + len(w["w"]) > 14):
            groups.append(cur); cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else g[-1]["e"] + 0.4
        g_end = min(g_end, g[-1]["e"] + 0.8, dur)
        for k, w in enumerate(g):
            a = w["s"]; b = g[k + 1]["s"] if k + 1 < len(g) else g_end
            if b <= a:
                continue
            parts = []
            for j, x in enumerate(g):
                t = esc(x["w"].upper().rstrip(",."))
                kc = KW_COLOR.get(x["cats"][0]) if fx.get("keyword_color") and x["cats"] else None
                if j == k:      # the word being said: yellow (or its keyword colour), bigger, with a bounce
                    col = kc or "&H0000E5FF&"
                    bounce = "\\fscx130\\fscy130\\t(0,110,\\fscx110\\fscy110)" if fx.get("pop") else "\\fscx108\\fscy108"
                    parts.append("{\\c" + col + bounce + "}" + t + "{\\c&H00FFFFFF&\\fscx100\\fscy100}")
                elif kc:
                    parts.append("{\\c" + kc + "}" + t + "{\\c&H00FFFFFF&}")
                else:
                    parts.append(t)
            pop = "{\\fscx118\\fscy118\\t(0,100,\\fscx100\\fscy100)}" if k == 0 else ""
            ev.append(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{{\\pos({W // 2},{cap_y})}}{pop}{' '.join(parts)}")
    if hook:
        body = "\\N".join(wrap(esc(clean(hook).upper()), 18))
        if body:
            ev.append(f"Dialogue: 1,{ts(0)},{ts(min(hook_secs, dur))},Hook,,0,0,0,,{{\\pos({W // 2},{hook_y})"
                      f"\\fad(0,200)\\fscx85\\fscy85\\t(0,120,\\fscx100\\fscy100)}}{body}")
    extra = (fx or {}).get("_extra") or {}
    wf = extra.get("wait_for_it")
    if wf and (fx or {}).get("wait_for_it", True):          # payoff comes late -> tease it
        a, b = float(wf[0]), float(wf[1])
        ev.append(f"Dialogue: 3,{ts(a)},{ts(b)},Hook,,0,0,0,,{{\\pos({W // 2},{CAP_Y.get(layout, 1300) - 230})\\fad(150,150)}}STAI AȘA…")
    day = extra.get("ziua")
    if day and (fx or {}).get("ziua_badge"):
        ev.append(f"Dialogue: 3,{ts(0)},{ts(dur)},Credit,,0,0,0,,{{\\an9\\pos({W - 36},{36})\\fs52\\bord4\\c&H00FFFFFF&\\3c&H005522FF&}}ZIUA {int(day)}")
    credit = c.get("credit_text")
    if credit:
        y = TOP - 14 if layout == "split" else H - 380
        c0 = min(hook_secs, dur) if hook else 0
        ev.append(f"Dialogue: 2,{ts(c0)},{ts(dur)},Credit,,0,0,0,,{{\\pos({W // 2},{y})}}{esc(clean(credit))}")
    return hdr + "\n".join(ev) + "\n"


def even(n):
    return int(n) // 2 * 2


def video_filter(layout, src_w, src_h, ass_path, cam_box=None):
    c = cfg()
    if layout == "split":
        x, y, w, h = cam_box or c.get("cam_box", [0.0, 0.118, 0.238, 0.238])
        cw, ch, cx, cy = even(src_w * w), even(src_h * h), even(src_w * x), even(src_h * y)
        gh = H - TOP
        gw = even(src_w * gh / src_h)
        gx = even(max(0, (gw - W) / 2 + gw * 0.04))       # a bit right of centre (cam + minimap are left)
        gx = min(gx, gw - W)
        if cw / max(1, ch) > (W / TOP) * 1.15:
            # wide webcam (Zbir + guest side by side): fit the WHOLE width (nobody cut in half),
            # fill the rest of the panel with a blurred copy of the cam
            v = (f"[0:v]split[a][b];[a]crop={cw}:{ch}:{cx}:{cy},split[c1][c2];"
                 f"[c1]scale={W}:{TOP}:force_original_aspect_ratio=increase,crop={W}:{TOP},boxblur=20:2,eq=brightness=-0.12[cbg];"
                 f"[c2]scale={W}:-2[cfg];[cbg][cfg]overlay=0:(H-h)/2,setsar=1[cam];"
                 f"[b]scale={gw}:{gh},crop={W}:{gh}:{gx}:0,setsar=1[game];[cam][game]vstack[v]")
        else:
            v = (f"[0:v]split[a][b];[a]crop={cw}:{ch}:{cx}:{cy},scale={W}:{TOP}:force_original_aspect_ratio=increase,"
                 f"crop={W}:{TOP},setsar=1[cam];[b]scale={gw}:{gh},crop={W}:{gh}:{gx}:0,setsar=1[game];"
                 f"[cam][game]vstack[v]")
    elif layout == "fill":
        v = f"[0:v]scale=-2:{H},crop={W}:{H},setsar=1[v]"
    else:  # blur
        v = (f"[0:v]split[a][b];[a]scale=-2:{H},crop={W}:{H},boxblur=28:2,eq=brightness=-0.10[bg];"
             f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-200,setsar=1[v]")
    return v + ";" + subs_tail("[v]", ass_path)


def subs_tail(label, ass_path, pre="fps=30,"):
    def q(p):   # ffmpeg filter-arg escaping (drive colon, quotes) - paths may contain spaces/apostrophes
        return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "'\\\\\\''")
    sub, fd = q(ass_path), q(FONT_DIR)     # (FONTS below is the font-preset table, not the folder)
    return f"{label}{pre}subtitles='{sub}':fontsdir='{fd}'[vo]"


def has_audio(path):
    r = run([tool("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path])
    return bool(r.stdout.strip())


def effects_graph(base, p, fx, ass, layout, n_inputs_before=1):
    """Video: base layout -> zoom/shake -> emoji pop-ups -> progress bar -> captions. Returns (graph, extra_inputs)."""
    from . import effects
    extra, g = [], [base]
    cur = "[v]"
    z = effects.zoom_filter(p, W, H)
    g.append(f"{cur}fps=30{(',' + z) if z else ''}[vz]"); cur = "[vz]"
    if p["emojis"]:
        codes = sorted({e["code"] for e in p["emojis"]})
        idx = {}
        for code in codes:
            idx[code] = n_inputs_before + extra.count("-i")
            extra += ["-loop", "1", "-framerate", "30", "-t", f"{p['dur']:.2f}", "-i", str(effects.ASSETS / "emoji" / f"{code}.png")]
        cap_y = CAP_Y.get(layout, 1300)
        for i, e in enumerate(p["emojis"]):
            a, b = e["t"], min(p["dur"], e["t"] + 1.6)
            side = W - 250 if i % 2 == 0 else 60            # alternate right / left of the caption
            yb = cap_y - 330
            g.append(f"[{idx[e['code']]}:v]scale=190:190,format=rgba[em{i}]")
            # pop: drops in from 60 px above with a quick bounce, then sits still
            y = f"{yb}-60*exp(-(t-{a})*9)*cos((t-{a})*18)"
            g.append(f"{cur}[em{i}]overlay=x={side}:y='{y}':enable='between(t,{a},{b})'[ve{i}]"); cur = f"[ve{i}]"
    if fx.get("progress_bar"):
        g.append(f"{cur}drawbox=x=0:y={H - 12}:w='iw*t/{p['dur']:.2f}':h=12:color=0xFE2C55@0.95:t=fill[vp]"); cur = "[vp]"
    g.append(subs_tail(cur, ass, pre=""))
    return ";".join(g), extra


def audio_graph(p, fx, sfx_input_idx):
    """Audio: boost on peaks -> meme SFX mixed in -> limiter (no clipping) -> loudness -14 LUFS."""
    from . import effects
    chain = ["aresample=48000"]
    vexp = effects.volume_expr(p, float(fx.get("boost_db", 4.0)))
    if vexp:
        chain.append(vexp)
    if fx.get("bass", True) and fx.get("boost", True):
        for a, b in (p.get("boost") or [])[:3]:          # "bass boost" on the zoom punch-ins
            chain.append(f"bass=g=7:f=110:w=0.6:enable='between(t,{a:.2f},{b + 0.3:.2f})'")
    g = [f"[0:a:0]{','.join(chain)}[a0]"]
    mix = ["[a0]"]
    for k, (t, i) in enumerate(zip(p["sfx"], sfx_input_idx)):
        ms = int(t * 1000)
        g.append(f"[{i}:a]volume={float(fx.get('sfx_volume', 0.45)):.2f},adelay={ms}|{ms},apad[s{k}]")
        mix.append(f"[s{k}]")
    if len(mix) > 1:
        g.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[am]"); last = "[am]"
    else:
        last = "[a0]"
    g.append(f"{last}alimiter=limit=0.89:level=disabled,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]")
    return ";".join(g)


def probe_size(path):
    import json
    r = run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "json", path])
    s = json.loads(r.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def measure_lufs(path):
    r = subprocess.run([tool("ffmpeg"), "-nostdin", "-hide_banner", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import re as _re
    summ = r.stderr[r.stderr.rfind("Summary"):]
    m = _re.search(r"I:\s+(-?[\d.]+) LUFS", summ); pk = _re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summ)
    return (float(m.group(1)) if m else None), (float(pk.group(1)) if pk and pk.group(1) != "-inf" else None)


def fix_loudness(path, target=-14.0):
    """Second pass: bring the clip to ~-14 LUFS (TikTok) without clipping (true-peak limiter at -1 dBTP)."""
    i, peak = measure_lufs(path)
    if i is None or i < -60 or (abs(i - target) < 0.8 and (peak is None or peak <= -1.0)):
        return i
    tmp = path.with_name(path.stem + ".lufs.mp4")
    gain = max(-12.0, min(12.0, target - i))
    run([tool("ffmpeg"), "-y", "-v", "error", "-i", path, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
         "-af", f"volume={gain:.2f}dB,alimiter=limit=0.84:level=disabled:attack=1:release=60", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", tmp])
    tmp.replace(path)
    return target


def render(src, s0, e0, words_rel, hook, out_mp4: Path, layout=None, cam_box=None, fx=None, extra=None):
    """src: media file; s0/e0: seconds in that file; words_rel: words with times relative to s0."""
    from . import effects
    c = cfg()
    layout = layout or c.get("layout", "split")
    fx = dict(fx or effects.settings(), _extra=extra or {})
    sw, sh = probe_size(src)
    dur = e0 - s0
    p = effects.plan(src, s0, e0, words_rel, fx)
    ass = out_mp4.with_suffix(".ass")
    ass.write_text(build_ass(words_rel, dur, hook, layout, fx, p["cats"]), encoding="utf-8")
    enc = encoder()
    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "9M",
             "-maxrate", "14M", "-bufsize", "20M", "-profile:v", "high"] if enc == "h264_nvenc"
            else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high"])
    base = video_filter(layout, sw, sh, ass, cam_box).rsplit(";", 1)[0]          # layout part, ends in [v]
    vgraph, extra = effects_graph(base, p, fx, ass, layout)
    audio = has_audio(src)
    sfx_idx, sfx_in = [], []
    if audio:
        n_in = 1 + extra.count("-i")
        sfx_idx = []
        for t in p["sfx"]:
            sfx_idx.append(n_in); n_in += 1
            sfx_in += ["-i", str(effects.ASSETS / "sfx" / "boom.wav")]
        graph = vgraph + ";" + audio_graph(p, fx, sfx_idx)
        amap = ["-map", "[ao]"]
    else:
        graph, amap = vgraph, []
    cmd = [tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{s0:.2f}", "-t", f"{dur:.2f}", "-i", src, *extra, *sfx_in,
           "-filter_complex", graph, "-map", "[vo]", *amap, *venc, "-pix_fmt", "yuv420p", "-t", f"{dur:.2f}",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_mp4]
    from . import progress
    run_ffmpeg_progress(cmd, dur, lambda q: progress.pct(q * 0.9), timeout=1800)
    if audio:
        progress.pct(91, detail="normalizez volumul (-14 LUFS)")
        fix_loudness(out_mp4, float(c.get("target_lufs", -14)))
    progress.pct(100)
    save_plan = {k: v for k, v in p.items() if k != "cats"}
    out_mp4.with_suffix(".fx.json").write_text(__import__("json").dumps(save_plan), encoding="utf-8")
    return out_mp4
