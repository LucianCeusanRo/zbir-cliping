"""Chunk -> moments -> rendered clips (registered in the DB, queued for posting)."""
import random, re, time
from pathlib import Path
import numpy as np
import json
from . import db, detect, progress, styles, layout, llm, render, texts, viral, youtube
from .transcribe import transcribe, load_wav, fix_ro
from .util import DATA, cfg, log, run, tool, ffprobe_duration, save_json, load_json

CLIPS = DATA / "clips"
PREROLL = 40.0          # seconds of the previous chunk glued in front, so boundary moments aren't cut
SRC_PAD = 12.0          # seconds kept around each clip so '✂️ Refă clipul' can move the start/end later


def hms(t):
    t = int(t); return f"{t // 3600}h{t % 3600 // 60:02d}m{t % 60:02d}s"


def _window(chunk):
    """Build the analysis window: last PREROLL s of the previous chunk + this chunk."""
    cur = Path(chunk["path"])
    prev = db.q("""SELECT * FROM chunks WHERE video_id=? AND id<>? AND t1 BETWEEN ? AND ? AND status IN ('done','processing','queued')
                   ORDER BY t1 DESC LIMIT 1""", (chunk["video_id"], chunk["id"], chunk["t0"] - 8, chunk["t0"] + 8), one=True)
    if not prev or not Path(prev["path"]).exists():
        return cur, chunk["t0"], 0.0, None
    pd = ffprobe_duration(prev["path"])
    pre = min(PREROLL, pd)
    lst = cur.with_suffix(".concat.txt")
    p1 = str(Path(prev["path"]).resolve()).replace("\\", "/").replace("'", r"'\''")
    p2 = str(cur.resolve()).replace("\\", "/").replace("'", r"'\''")
    lst.write_text(f"file '{p1}'\ninpoint {pd - pre:.2f}\nfile '{p2}'\n", encoding="utf-8")
    win = cur.with_suffix(".window.mkv")
    try:
        run([tool("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0", "-map", "0:a:0?",
             "-c", "copy", win])
    except Exception as e:
        log.warning("nu pot lipi bucata anterioara (%s), procesez fara", str(e)[:100])
        return cur, chunk["t0"], 0.0, None
    finally:
        lst.unlink(missing_ok=True)
    # stream time of the window start = chunk start - real preroll
    real_pre = max(0.0, ffprobe_duration(win) - ffprobe_duration(cur))
    return win, chunk["t0"] - real_pre, real_pre, win


def build_caption(title, rnd=random):
    """TikTok description: template + hashtags. #zbirgta6 is ALWAYS the first hashtag; total <= hashtags_max."""
    c = cfg()
    tags = " ".join(texts.pick_hashtags(c, rnd))
    try:
        cap = c.get("caption_template", "{title} {hashtags}").format(title=title, hashtags=tags)
    except (KeyError, IndexError, ValueError):
        cap = f"{title} {tags}"
    return texts.finalize_caption(cap + " " + tags)      # tags missing from a custom template are still added


def save_source(src, s0, e0, total, out):
    """Small re-encoded copy of the clip +/- SRC_PAD s (the big live chunks are deleted after processing)."""
    ps, pe = max(0.0, s0 - SRC_PAD), min(total, e0 + SRC_PAD)
    dst = out.with_suffix(".src.mp4")
    venc = (["-c:v", "h264_nvenc", "-preset", "p3", "-cq", "24"] if render.encoder() == "h264_nvenc"
            else ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"])
    try:
        run([tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{ps:.2f}", "-t", f"{pe - ps:.2f}", "-i", src, "-map", "0:v:0",
             "-map", "0:a:0?", *venc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", dst], timeout=900)
        return dst, ps, pe
    except Exception as e:
        log.warning("nu pot salva sursa pentru re-taiere (%s)", str(e)[:100])
        dst.unlink(missing_ok=True)
        return None, ps, pe


def apply_corrections(words, corr):
    for c in corr or []:
        try:
            i = int(c["i"]); w = fix_ro(str(c["w"]).strip())
            if 0 <= i < len(words) and w and len(w) < 40:
                words[i]["w"] = w
        except Exception:
            pass


def series_day(v, s_abs, c):
    """'ZIUA N' of the 24/7 apartment series: from series_start (YYYY-MM-DD in config) or the live's start."""
    import datetime as _dt
    st = c.get("series_start")
    try:
        t0 = _dt.datetime.strptime(str(st), "%Y-%m-%d").timestamp() if st else (v["start_ts"] if v and v["start_ts"] else None)
    except ValueError:
        t0 = v["start_ts"] if v and v["start_ts"] else None
    if not t0 or not v or not v["start_ts"]:
        return None
    return int((v["start_ts"] + s_abs - t0) // 86400) + 1


def process_chunk(chunk, overrides=None):
    """overrides: settings for this call only (manual jobs: more clips per chunk, lower threshold)."""
    c = dict(cfg(), **(overrides or {}))
    vid = chunk["video_id"]
    v = db.video(vid)
    src, win_t0, preroll, tmp = _window(chunk)
    wav = Path(src).with_suffix(".wav")
    made = 0
    progress.update(chunk=f"{hms(chunk['t0'])} – {hms(chunk['t1'])}", chunk_video=vid, chunk_kind=chunk["kind"] if "kind" in chunk.keys() else None,
                    clip_n=0, clip_m=0)
    progress.stage("transcriere", 0, "extrag sunetul…")
    try:
        try:
            run([tool("ffmpeg"), "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav])
        except RuntimeError as e:
            if "does not contain any stream" in str(e):
                raise RuntimeError("bucata nu are sunet (stream fara audio)") from None
            raise
        pcm = load_wav(wav)
        t = time.time()
        words, sents = transcribe(wav)
        if not words and len(pcm) and float(np.sqrt(np.mean((pcm.astype(np.float32) / (32768 if pcm.dtype.kind == "i" else 1)) ** 2))) > 0.01:
            log.warning("transcrierea a dat 0 cuvinte desi se aude ceva -> reincerc o data")
            words, sents = transcribe(wav)
        log.info("transcriere %s [%s]: %d cuvinte in %.0fs", vid, hms(chunk["t0"]), len(words), time.time() - t)
        dur = len(pcm) / 16000
        chat = youtube.chat_counts(DATA / vid, v["start_ts"] if v else None, win_t0, win_t0 + dur)
        progress.stage("detectare", None, f"{len(words)} cuvinte, caut momente amuzante…")
        cands = detect.find_moments(pcm, words, sents, chat, preroll, c)
        use_llm = c.get("use_llm", True) and llm.available()
        progress.update(clip_n=0, clip_m=len(cands))
        for i, cand in enumerate(cands, 1):
            s_abs, e_abs = win_t0 + cand["start"], win_t0 + cand["end"]
            progress.update(clip_n=i - 1, clip_m=len(cands))
            if db.overlaps_existing(vid, s_abs, e_abs):
                continue
            if use_llm:
                progress.stage("ai", None, f"moment {i}/{len(cands)} [{hms(s_abs)}]")
            wrel = [dict(w, s=round(w["s"] - cand["start"], 2), e=round(w["e"] - cand["start"], 2))
                    for w in words if cand["start"] <= w["s"] < cand["end"]]
            dup = db.similar_text_exists(vid, " ".join(w["w"] for w in wrel), s_abs,
                                         thr=float(c.get("dedupe_similarity", 0.8)))
            if dup:
                log.info("  moment %s sarit: aproape identic cu clipul #%s", hms(s_abs), dup)
                continue
            style, style_all = styles.categorize(" ".join(w["w"] for w in wrel), cand.get("cats"), cand.get("feat"))
            if not styles.enabled_categories().get(style, True):
                log.info("  moment %s sarit: stilul %s e dezactivat", hms(s_abs), style)
                continue
            hook, title, cat = texts.make(cand, wrel)
            score = cand["score"]
            llm_sc = None
            if use_llm:
                j = llm.judge(cand, wrel)
                if j:
                    try:
                        sc = float(j.get("scor", 0))
                    except Exception:
                        sc = 0
                    if sc < c.get("llm_min_score", 5) or j.get("categorie") == "plictisitor":
                        log.info("  LLM a respins momentul %s (scor %.0f)", hms(s_abs), sc)
                        progress.count("rejected")
                        continue
                    score = score + sc
                    llm_sc = sc
                    if c.get("llm_fix_transcript", True):
                        apply_corrections(wrel, j.get("corecturi"))
                    try:   # LLM may tighten the cut so the hook lands in the first 1-2 s
                        ns, ne = float(j.get("start", 0)), float(j.get("end", cand["end"] - cand["start"]))
                        if 0 <= ns < ne and c["clip_min_sec"] <= ne - ns <= c["clip_max_sec"] and ne <= cand["end"] - cand["start"] + 10:
                            cand = dict(cand, start=cand["start"] + ns, end=min(dur, cand["start"] + ne))
                            wrel = [dict(w, s=round(w["s"] - ns, 2), e=round(w["e"] - ns, 2)) for w in wrel if ns <= w["s"] < ne]
                            s_abs, e_abs = win_t0 + cand["start"], win_t0 + cand["end"]
                    except Exception:
                        pass
                    hook = (j.get("hook") or hook).strip()
                    title = (j.get("titlu") or title).strip()
            caption = build_caption(title)
            CLIPS.mkdir(parents=True, exist_ok=True)
            out = CLIPS / f"{vid}_{hms(s_abs)}.mp4"
            t = time.time()
            progress.stage("randare", 0, f"clip {i}/{len(cands)} [{hms(s_abs)}] · {cand['end'] - cand['start']:.0f}s",
                           clip_n=i, clip_m=len(cands))
            lay, box = layout.decide(src, cand["start"], cand["end"], key=vid)
            peak_rel = cand.get("peak", cand["start"]) - cand["start"]
            hs = float(c.get("hook_seconds", 2.5))
            extra = {"ziua": series_day(v, s_abs, c)}
            if peak_rel > hs + 4.5:                              # payoff comes late -> "STAI AȘA…" tease
                extra["wait_for_it"] = (hs + 0.2, min(peak_rel - 0.6, hs + 3.0))
            render.render(src, cand["start"], cand["end"], wrel, hook, out, lay, box, extra=extra)
            log.info("  CLIP %s [%s] (%.0fs, scor %.1f, %s) randat in %.0fs -> %s", hms(s_abs), lay, cand["end"] - cand["start"],
                     score, ", ".join(cand["why"]), time.time() - t, out.name)
            thumb = out.with_suffix(".jpg")
            try:
                run([tool("ffmpeg"), "-y", "-v", "error", "-ss", "1.5", "-i", out, "-frames:v", "1", "-vf", "scale=360:-2", thumb])
            except Exception:
                thumb = None
            status = "pending"          # waits for your review in the app (or auto-post, if ON)
            feat = cand.get("feat") or {}
            vpct, vfac = viral.compute(feat, cand["end"] - cand["start"], hook.startswith("„"), cat,
                                       sum(1 for w in wrel if w["s"] < 2), llm_sc)
            srcf, ps, pe = save_source(src, cand["start"], cand["end"], dur, out)
            db.add_clip(video_id=vid, start=s_abs, end=e_abs, score=score, file=str(out), title=title,
                        caption=caption, text=" ".join(w["w"] for w in wrel), why=", ".join(cand["why"]),
                        thumb=str(thumb) if thumb else None, hook=hook, layout=lay, status=status,
                        viral=vpct, factors=json.dumps(vfac), cat=cat, src=str(srcf) if srcf else None,
                        style=style, styles=",".join(style_all))
            save_json(out.with_suffix(".json"), {
                "video": vid, "start": s_abs, "end": e_abs, "score": score, "hook": hook, "title": title, "cat": cat,
                "layout": lay, "cam_box": box, "caption": caption, "why": cand["why"], "words": wrel,
                "feat": feat, "llm": llm_sc, "viral": vpct, "factors": vfac,
                # for re-cutting: position of the clip inside the saved source, and the transcript around it
                "src_abs0": win_t0 + ps, "src_s": cand["start"] - ps, "src_e": cand["end"] - ps, "src_dur": pe - ps,
                "src_words": [dict(w, s=round(w["s"] - ps, 2), e=round(w["e"] - ps, 2)) for w in words if ps <= w["s"] < pe],
                "src_sents": [dict(x, s=round(x["s"] - ps, 2), e=round(x["e"] - ps, 2)) for x in sents if x["e"] > ps and x["s"] < pe]})
            made += 1
            progress.count("made")
    finally:
        wav.unlink(missing_ok=True)
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    return made


def cleanup_sources(vid, keep=2):
    """Delete processed source chunks (they're big) except the newest few needed as pre-roll and, if
    keep_raw_chunks_hours > 0, the ones processed in the last N hours."""
    hours = float(cfg().get("keep_raw_chunks_hours", 0) or 0)
    rows = db.q("SELECT id, path, created FROM chunks WHERE video_id=? AND status IN ('done','error') ORDER BY t1 DESC", (vid,))
    for r in rows[keep:]:
        if hours and (r["created"] or 0) > time.time() - hours * 3600:
            continue
        Path(r["path"]).unlink(missing_ok=True)
    cleanup_clip_sources()


def _clip_files(r):
    out = []
    for f in (r["file"], r["thumb"], r["src"] if "src" in r.keys() else None):
        if f:
            out.append(Path(f))
    if r["file"]:
        for ext in (".json", ".ass", ".fx.json"):
            out.append(Path(r["file"]).with_suffix(ext))
    return out


def _drop_clip_files(r, keep_thumb=True):
    for f in _clip_files(r):
        if keep_thumb and r["thumb"] and f == Path(r["thumb"]):
            continue
        f.unlink(missing_ok=True)
    db.x("UPDATE clips SET src=NULL WHERE id=?", (r["id"],))


def janitor():
    """Disk housekeeping (every few minutes): settings 'delete_after_post', 'skipped_retention_days',
    'disk_budget_gb'. The db rows always stay (so nothing is ever clipped/posted twice)."""
    c = cfg()
    freed = 0
    if c.get("delete_after_post", False):
        days = float(c.get("delete_after_post_days", 1))
        for r in db.q("SELECT * FROM clips WHERE status='posted' AND posted_at < ?", (time.time() - days * 86400,)):
            if r["file"] and Path(r["file"]).exists():
                freed += Path(r["file"]).stat().st_size; _drop_clip_files(r)
    sk = float(c.get("skipped_retention_days", 7) or 0)
    if sk > 0:
        for r in db.q("SELECT * FROM clips WHERE status='skipped' AND created < ?", (time.time() - sk * 86400,)):
            if r["file"] and Path(r["file"]).exists():
                freed += Path(r["file"]).stat().st_size; _drop_clip_files(r, keep_thumb=False)
    budget = float(c.get("disk_budget_gb", 0) or 0)
    if budget > 0:
        def used():
            return sum(f.stat().st_size for f in DATA.rglob("*") if f.is_file() and "tiktok_profile" not in f.parts)
        total = used()
        if total > budget * 1e9:
            # oldest first: processed raw chunks -> posted clips -> skipped -> recut sources -> old low-viral pending
            victims = [Path(r["path"]) for r in db.q("SELECT path FROM chunks WHERE status IN ('done','error') ORDER BY created")]
            rows = (db.q("SELECT * FROM clips WHERE status IN ('posted','unconfirmed') ORDER BY posted_at")
                    + db.q("SELECT * FROM clips WHERE status='skipped' ORDER BY created"))
            for p in victims:
                if total <= budget * 1e9:
                    break
                if p.exists():
                    s = p.stat().st_size; p.unlink(missing_ok=True); total -= s; freed += s
            for r in rows:
                if total <= budget * 1e9:
                    break
                for f in _clip_files(r):
                    if f.exists() and not (r["thumb"] and f == Path(r["thumb"])):
                        s = f.stat().st_size; f.unlink(missing_ok=True); total -= s; freed += s
                db.x("UPDATE clips SET src=NULL WHERE id=?", (r["id"],))
            if total > budget * 1e9:
                cleanup_clip_sources(max_gb=0)
                total = used()
            if total > budget * 1e9:
                log.warning("folderul de date (%.1f GB) tot depaseste bugetul de %.0f GB (clipuri noi/in coada nu le sterg)",
                            total / 1e9, budget)
    if freed:
        log.info("curatenie: am eliberat %.2f GB", freed / 1e9)
    return freed


def cleanup_clip_sources(max_gb=None):
    """Re-cut sources: drop them for posted/deleted clips, and the oldest ones above `recut_sources_max_gb`."""
    max_gb = cfg().get("recut_sources_max_gb", 15) if max_gb is None else max_gb
    for r in db.q("SELECT id, src FROM clips WHERE src IS NOT NULL AND status IN ('posted','deleted')"):
        Path(r["src"]).unlink(missing_ok=True); db.x("UPDATE clips SET src=NULL WHERE id=?", (r["id"],))
    rows = [r for r in db.q("SELECT id, src FROM clips WHERE src IS NOT NULL ORDER BY created DESC") if Path(r["src"]).exists()]
    total = 0
    for r in rows:
        total += Path(r["src"]).stat().st_size
        if total > max_gb * 1e9:
            Path(r["src"]).unlink(missing_ok=True); db.x("UPDATE clips SET src=NULL WHERE id=?", (r["id"],))


# ---------------- '🔄 Refă textul' and '✂️ Refă clipul' ----------------
class ClipError(Exception):
    pass


def _meta(r):
    p = Path(r["file"]).with_suffix(".json")
    return p, (load_json(p, {}) if p.exists() else {})


def retext(cid, rnd=random):
    """New title + caption + hashtags (a different variant on every press). #zbirgta6 stays the first hashtag."""
    r = db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)
    if not r:
        raise ClipError("clip inexistent")
    if r["status"] in ("posted", "posting"):
        raise ClipError("clipul e deja postat")
    p, m = _meta(r)
    cat = r["cat"] or m.get("cat") or "_"
    words = m.get("words") or []
    title = None
    if cfg().get("use_llm", True) and llm.available():
        title = llm.retitle(r["text"] or "", r["title"] or "")
    title = title or texts.new_title(cat, words, avoid=[r["title"]], rnd=rnd)
    cap = build_caption(title, rnd)
    db.x("UPDATE clips SET title=?, caption=? WHERE id=?", (title, cap, cid))
    if m:
        m.update(title=title, caption=cap); save_json(p, m)
    return {"title": title, "caption": cap}


def snap_start(t, sents, words, win=3.0):
    """Move t to the nearest sentence start (±win s), else to the end of a pause between words."""
    c = [x["s"] for x in sents if abs(x["s"] - t) <= win]
    if c:
        return max(0.0, min(c, key=lambda s: abs(s - t)) - 0.1)
    gaps = [b["s"] - 0.15 for a, b in zip(words, words[1:]) if b["s"] - a["e"] >= 0.3 and abs(b["s"] - t) <= win]
    return max(0.0, min(gaps, key=lambda s: abs(s - t))) if gaps else max(0.0, t)


def snap_end(t, sents, words, win=3.0):
    c = [x["e"] for x in sents if abs(x["e"] - t) <= win]
    if c:
        return min(c, key=lambda e: abs(e - t)) + 0.35
    gaps = [a["e"] + 0.3 for a, b in zip(words, words[1:]) if b["s"] - a["e"] >= 0.3 and abs(a["e"] - t) <= win]
    return min(gaps, key=lambda e: abs(e - t)) if gaps else t


def recut(cid, dstart=0.0, dend=0.0, snap=True):
    """Re-cut a clip: move start/end by dstart/dend seconds (negative = earlier), optionally snapped to
    sentence/silence boundaries, then re-render (captions follow the new cut)."""
    r = db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)
    if not r:
        raise ClipError("clip inexistent")
    if r["status"] in ("posted", "posting", "deleted"):
        raise ClipError("clipul nu mai poate fi modificat (" + r["status"] + ")")
    p, m = _meta(r)
    if not r["src"] or not Path(r["src"]).exists() or "src_s" not in m:
        raise ClipError("sursa clipului nu mai există (clip vechi) – nu se poate re-tăia")
    c = cfg()
    sw, ss, total = m.get("src_words") or [], m.get("src_sents") or [], float(m["src_dur"])
    ns, ne = float(m["src_s"]) + float(dstart), float(m["src_e"]) + float(dend)
    if snap:
        ns, ne = snap_start(ns, ss, sw), snap_end(ne, ss, sw)
    ns, ne = max(0.0, ns), min(total, ne)
    mn, mx = max(5, c.get("clip_min_sec", 20) - 10), c.get("clip_max_sec", 60) + 30
    if ne - ns < mn:
        raise ClipError(f"clipul ar avea doar {ne - ns:.0f}s (minim {mn}s)")
    if ne - ns > mx:
        ne = ns + mx
    words = [dict(w, s=round(w["s"] - ns, 2), e=round(w["e"] - ns, 2)) for w in sw if ns <= w["s"] < ne - 0.1]
    ver = int(r["version"] or 1) + 1
    stem = re.sub(r"_v\d+$", "", Path(r["file"]).stem)
    out = Path(r["file"]).with_name(f"{stem}_v{ver}.mp4")
    render.render(r["src"], ns, ne, words, r["hook"] or "", out, m.get("layout") or r["layout"], m.get("cam_box"))
    thumb = out.with_suffix(".jpg")
    try:
        run([tool("ffmpeg"), "-y", "-v", "error", "-ss", "1.5", "-i", out, "-frames:v", "1", "-vf", "scale=360:-2", thumb])
    except Exception:
        thumb = None
    feat = m.get("feat") or {}
    vpct, vfac = viral.compute(feat, ne - ns, (r["hook"] or "").startswith("„"), r["cat"] or m.get("cat") or "_",
                               sum(1 for w in words if w["s"] < 2), m.get("llm")) if feat else (r["viral"], m.get("factors"))
    a0 = float(m["src_abs0"])
    old = Path(r["file"])
    m.update(start=a0 + ns, end=a0 + ne, src_s=ns, src_e=ne, words=words, viral=vpct, factors=vfac)
    save_json(out.with_suffix(".json"), m)
    db.x("UPDATE clips SET file=?, thumb=?, start=?, end=?, text=?, version=?, viral=?, factors=? WHERE id=?",
         (str(out), str(thumb) if thumb else None, a0 + ns, a0 + ne, " ".join(w["w"] for w in words), ver, vpct,
          json.dumps(vfac) if vfac else None, cid))
    if old != out:
        for ext in (".mp4", ".jpg", ".json", ".ass"):
            old.with_suffix(ext).unlink(missing_ok=True)
    log.info("clip %d re-taiat: %.1fs - %.1fs (%.0fs)", cid, ns, ne, ne - ns)
    return {"start": a0 + ns, "end": a0 + ne, "dur": round(ne - ns, 1), "file": out.name}


# ---------------- "TOP 5" compilations ----------------
def compile_top(n=5, hours=24, style=None, out_dir=None):
    """Best n clips (viral %) of the last `hours` -> one TOP-N clip (#n ... #1, best last) with a title card.
    Added to the gallery as a new pending clip (style 'top5'). Returns the new clip id."""
    rows = db.q("SELECT * FROM clips WHERE status NOT IN ('deleted') AND created>=? AND (style IS NULL OR style!='top5')",
                (time.time() - hours * 3600,))
    rows = [r for r in rows if r["file"] and Path(r["file"]).exists() and (style is None or r["style"] == style)]
    rows.sort(key=lambda r: -db.eff_viral(r))
    pick = rows[:n]
    if len(pick) < 2:
        raise ClipError("prea puține clipuri bune în ultimele ore pentru o compilație")
    total = sum(min(20.0, (r["end"] - r["start"])) for r in pick)
    while total > 60 and len(pick) > 2:     # TikTok sweet spot: keep it under ~60 s
        pick = pick[:-1]; total = sum(min(20.0, (r["end"] - r["start"])) for r in pick)
    pick = list(reversed(pick))             # countdown: #N first, #1 (best) last
    k = len(pick)
    out_dir = Path(out_dir or CLIPS); out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"top{k}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    ass = out.with_suffix(".ass")
    ev, t = [], 0.0
    segs = []
    for i, r in enumerate(pick):
        d = min(20.0, float(r["end"] - r["start"]))
        segs.append(d)
        num = k - i
        ev.append(f"Dialogue: 1,{render.ts(t)},{render.ts(t + 1.6)},Num,,0,0,0,,{{\\pos({render.W // 2},{render.H // 2 - 120})\\fad(0,250)}}#{num}")
        t += d
    title = f"TOP {k} MOMENTE ZBIR"
    ev.append(f"Dialogue: 2,{render.ts(0)},{render.ts(1.8)},Title,,0,0,0,,{{\\pos({render.W // 2},{260})}}{render.esc(title)}")
    font = cfg().get("caption_font", "Montserrat Black")
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {render.W}
PlayResY: {render.H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Num,{font},260,&H0000E5FF,&H0000E5FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,14,6,5,40,40,0,1
Style: Title,{font},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,9,4,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n", encoding="utf-8")
    cmd = [tool("ffmpeg"), "-y", "-v", "error"]
    for r, d in zip(pick, segs):
        cmd += ["-t", f"{d:.2f}", "-i", r["file"]]
    parts = "".join(f"[{i}:v]scale={render.W}:{render.H},setsar=1,fps=30[v{i}];[{i}:a]aresample=48000[a{i}];" for i in range(k))
    cat = "".join(f"[v{i}][a{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=1[vc][ac]"
    graph = parts + cat + ";" + render.subs_tail("[vc]", ass, pre="")
    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "9M"] if render.encoder() == "h264_nvenc"
            else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"])
    run(cmd + ["-filter_complex", graph, "-map", "[vo]", "-map", "[ac]", *venc, "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out], timeout=1800)
    thumb = out.with_suffix(".jpg")
    try:
        run([tool("ffmpeg"), "-y", "-v", "error", "-ss", "0.8", "-i", out, "-frames:v", "1", "-vf", "scale=360:-2", thumb])
    except Exception:
        thumb = None
    best = pick[-1]
    v = max(db.eff_viral(r) for r in pick)
    cap = build_caption(f"Top {k} momente cu Zbir din apartament 🔥")
    cid = db.add_clip(video_id="compilatii", start=0, end=sum(segs), score=float(best["score"] or 0),
                      file=str(out), title=f"Top {k} momente", caption=cap, text=" | ".join((r["hook"] or "") for r in pick),
                      why=f"compilație din clipurile #{', #'.join(str(r['id']) for r in pick)}", thumb=str(thumb) if thumb else None,
                      hook=title, layout="top", status="pending", viral=min(99, v + 5), cat="_", style="top5", styles="top5")
    log.info("compilatie TOP %d gata: %s (clipuri %s)", k, out.name, [r["id"] for r in pick])
    return cid
