"""Automated tests (unittest). Run via `python -m zbirclip selftest` (isolated temp ZBIR_HOME)."""
import random, re, json, os, shutil, subprocess, sys, threading, time, unittest, urllib.error, urllib.request, wave
from pathlib import Path
import numpy as np

assert os.environ.get("ZBIR_HOME"), "run through `python -m zbirclip selftest`"
from zbirclip import util, db, detect, render, transcribe, texts, pipeline, youtube, app, web, layout, tiktok, viral, effects, jobs, progress, styles, updater, facecam  # noqa

QUICK = os.environ.get("ZBIR_QUICK") == "1"
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def sine_wav(path, secs=120, bursts=(40, 80)):
    sr = 16000; t = np.arange(secs * sr) / sr
    x = 0.02 * np.random.RandomState(0).randn(len(t))
    for b in bursts:                       # loud, bright "screams" with 5 Hz bursts (laugh-like)
        m = (t >= b) & (t < b + 4)
        x[m] += 0.6 * np.sin(2 * np.pi * 1800 * t[m]) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 5 * t[m])))
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
    return x.astype(np.float32)


def fake_words(secs=120):
    words, sents = [], []
    vocab = ["hai", "frate", "că", "mergem", "acolo", "și", "vedem", "ce", "facem", "acum", "bă", "uite"]
    t = 0.5
    while t < secs - 1:
        w = vocab[int(t * 7) % len(vocab)]
        if 40 <= t < 44 or 80 <= t < 84:
            w = ["doamne", "nu", "se", "poate", "hahaha", "aoleu"][int(t * 3) % 6]
        words.append({"w": w, "s": round(t, 2), "e": round(t + 0.3, 2), "p": 0.9}); t += 0.45
    for i in range(0, len(words), 8):
        g = words[i:i + 8]
        sents.append({"s": g[0]["s"], "e": g[-1]["e"], "t": " ".join(x["w"] for x in g) + "."})
    return words, sents


class T1Config(unittest.TestCase):
    def test_hashtag_forced(self):
        util.save_settings({"hashtags": ["#gta6"]})
        self.assertIn("#zbirgta6", util.cfg()["hashtags"])
        self.assertIn("#gta6", util.cfg()["hashtags"])
        util.save_settings({"hashtags": ["#zbirgta6"]})

    def test_caption_always_has_tags(self):
        util.save_settings({"hashtags": ["#zbirgta6", "#fyp"], "caption_template": "{title}"})
        cap = pipeline.build_caption("Zbir a luat-o razna 😂")
        self.assertIn("#zbirgta6", cap); self.assertIn("#fyp", cap)
        util.save_settings({"caption_template": "{broken"})
        self.assertIn("#zbirgta6", pipeline.build_caption("x"))
        self.assertIn("#zbirgta6", web.ensure_tags("am sters hashtag-ul"))
        self.assertEqual(web.ensure_tags("ok #zbirgta6 #fyp").count("#zbirgta6"), 1)
        util.save_settings({"caption_template": "{title} {hashtags}", "hashtags": ["#zbirgta6"]})


class T2State(unittest.TestCase):
    def test_gaps_and_dedupe(self):
        v = "vidA"
        db.upsert_video(v, "t", "c", 1000.0, "live")
        db.add_chunk(v, 0, 300, "a", "dvr"); db.add_chunk(v, 600, 900, "b", "live")
        self.assertEqual(db.gaps(v, 0, 900), [[300, 600]])
        db.add_clip(video_id=v, start=100, end=140, score=3, file="f", title="t", caption="c")
        self.assertTrue(db.overlaps_existing(v, 110, 150))
        self.assertFalse(db.overlaps_existing(v, 141, 180))
        self.assertFalse(db.overlaps_existing("other", 110, 150))

    def test_chunk_retry_then_error(self):
        cid = db.add_chunk("vidB", 0, 60, "x", "live")
        db.retry_or_fail(cid, "boom")
        self.assertEqual(db.q("SELECT status FROM chunks WHERE id=?", (cid,), one=True)["status"], "queued")
        db.retry_or_fail(cid, "boom")
        self.assertEqual(db.q("SELECT status FROM chunks WHERE id=?", (cid,), one=True)["status"], "error")

    def test_post_order_manual_first(self):
        a = db.add_clip(video_id="vidC", start=0, end=30, score=9, file="a", title="", caption="")
        b = db.add_clip(video_id="vidC", start=40, end=70, score=1, file="b", title="", caption="")
        self.assertIsNone(db.next_clip_to_post(auto=False))            # manual mode: nothing until approved
        self.assertEqual(db.next_clip_to_post(auto=True)["id"], a)     # auto: best score
        db.x("UPDATE clips SET status='queued', queued_at=? WHERE id=?", (time.time(), b))
        self.assertEqual(db.next_clip_to_post(auto=True)["id"], b)     # approved one goes first
        db.x("UPDATE clips SET status='skipped' WHERE video_id='vidC'")

    def test_crash_recovery(self):
        cid = db.add_chunk("vidD", 0, 60, "x", "live")
        db.x("UPDATE chunks SET status='processing' WHERE id=?", (cid,))
        db._conn.close(); db._conn = None                               # simulate crash + restart
        self.assertEqual(db.q("SELECT status FROM chunks WHERE id=?", (cid,), one=True)["status"], "queued")
        db.x("UPDATE chunks SET status='done' WHERE id=?", (cid,))


class T2bOrphans(unittest.TestCase):
    def test_stale_children_killed_after_crash(self):
        import sys
        code = "import subprocess,sys,os; sys.path.insert(0, os.getcwd()); from zbirclip import util; " \
               "util.spawn([sys.executable, '-c', 'import time; time.sleep(120)  # yt_dlp']); os._exit(0)"
        subprocess.run([sys.executable, "-c", code], check=True, cwd=os.getcwd())
        pids = json.loads(util.PIDS.read_text())
        self.assertTrue(pids)
        util.kill_stale_children()
        time.sleep(1)
        for pid in pids:
            if sys.platform != "win32":
                self.assertFalse(Path(f"/proc/{pid}").exists() and "yt_dlp" in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore"))


class T3Romanian(unittest.TestCase):
    def test_clitics_any_gap_and_non_latin(self):
        from zbirclip.transcribe import merge_clitics, clean_word
        W = lambda t, a, b: {"w": t, "s": a, "e": b}
        got = [w["w"] for w in merge_clitics([W("V", 0, .2), W("-am", 1.5, 1.8), W("gata.", 2, 2.2), W("-a", 3, 3.1)])]
        self.assertEqual(got, ["V-am", "gata.", "a"])
        self.assertEqual(clean_word(".อะไร"), "")
        from zbirclip.transcribe import restore_diacritics as rd
        self.assertEqual(" ".join(rd(x) for x in "Pana si sa faca, bai! Daca-i".split()), "Până și să facă, băi! Dacă-i")
        self.assertEqual(rd("casa"), "casa")
        self.assertEqual(clean_word("Țeapă!"), "Țeapă!")

    def test_diacritics(self):
        self.assertEqual(transcribe.fix_ro("şi ţară Ş Ţ ã"), "și țară Ș Ț ă")
        self.assertEqual("ș ț ă î â".upper(), "Ș Ț Ă Î Â")

    def test_clitics_and_loops(self):
        w = [{"w": "s", "s": 0, "e": .2}, {"w": "-a", "s": .2, "e": .4}, {"w": "dus", "s": .5, "e": .8}]
        self.assertEqual([x["w"] for x in transcribe.merge_clitics(w)], ["s-a", "dus"])
        s = [{"s": i, "e": i + 1, "t": "Nu știu ce văd"} for i in range(5)]
        self.assertEqual(len(transcribe.dedupe_loops(s)), 2)

    def test_hallucination_filter(self):
        self.assertFalse(transcribe.keep_word("surprisingly", 0.1))
        self.assertTrue(transcribe.keep_word("frate", 0.1))
        self.assertTrue(transcribe.keep_word("anything", 0.9))

    def test_keywords(self):
        s, cats = detect._kw_score("Doamne, nu se poate! hahaha")
        self.assertGreater(s, 2); self.assertIn("ras", cats); self.assertIn("exclamatie", cats)

    def test_ass_has_diacritics_and_masks(self):
        util.save_settings({"censor_swears": True})
        ass = render.build_ass([{"w": "Ce", "s": 0.1, "e": 0.3}, {"w": "pula", "s": 0.3, "e": 0.6},
                                {"w": "știi", "s": 0.7, "e": 1.0}, {"w": "țară", "s": 1.1, "e": 1.4}], 5, "ZBIR A ÎNNEBUNIT 😂", "split")
        self.assertIn("ȘTII", ass); self.assertIn("ȚARĂ", ass); self.assertIn("ZBIR A ÎNNEBUNIT", ass)
        self.assertNotIn("PULA", ass); self.assertIn("P***", ass); self.assertNotIn("😂", ass)


class T4Detect(unittest.TestCase):
    def test_finds_moments_in_limits(self):
        pcm = sine_wav(Path(util.DATA) / "t.wav")
        words, sents = fake_words()
        c = dict(util.cfg())
        cands = detect.find_moments(pcm, words, sents, None, 0, c)
        self.assertTrue(cands, "no moments found")
        for x in cands:
            self.assertGreaterEqual(x["end"] - x["start"], c["clip_min_sec"] - 0.01)
            self.assertLessEqual(x["end"] - x["start"], c["clip_max_sec"] + 0.01)
        peaks = sorted(x["peak"] for x in cands)
        self.assertTrue(any(35 <= p <= 50 for p in peaks) or any(75 <= p <= 90 for p in peaks), peaks)
        # non overlapping
        cs = sorted(cands, key=lambda x: x["start"])
        for a, b in zip(cs, cs[1:]):
            self.assertLessEqual(a["end"], b["start"])

    def test_preroll_excluded(self):
        pcm = sine_wav(Path(util.DATA) / "t2.wav", bursts=(10,))
        words, sents = fake_words()
        cands = detect.find_moments(pcm, words, sents, None, 60, dict(util.cfg()))
        self.assertTrue(all(x["peak"] >= 60 for x in cands))

    def test_chat_parse(self):
        d = Path(util.DATA) / "chatvid"; d.mkdir(exist_ok=True)
        lines = [json.dumps({"replayChatItemAction": {"actions": [{"x": "hahaha"}], "videoOffsetTimeMsec": str(ms)}})
                 for ms in (1000, 1500, 2100, 5000)]
        (d / "chat.live_chat.json").write_text("\n".join(lines), encoding="utf-8")
        c = youtube.chat_counts(d, None, 0, 10)
        self.assertEqual(c[1], 4); self.assertEqual(c[2], 2)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg lipseste")
class T5Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Path(util.DATA) / "synthetic.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=30",
                        "-f", "lavfi", "-i", "sine=f=440:d=30", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(cls.src)], check=True)

    def _check(self, out):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "json", str(out)], capture_output=True, text=True)
        s = json.loads(r.stdout)["streams"][0]
        self.assertEqual((s["width"], s["height"]), (1080, 1920))
        self.assertAlmostEqual(util.ffprobe_duration(out), 21, delta=1.0)

    def test_split_and_blur(self):
        words = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("Ce faci frate știi țară îți".split())]
        for lay in (["split"] if QUICK else ["split", "blur", "fill"]):
            out = Path(util.DATA) / f"r_{lay}.mp4"
            render.render(str(self.src), 2, 23, words, "HOOK DE TEST ȘȚĂ", out, lay, None)
            self._check(out)

    def test_layout_no_face_falls_back_to_blur(self):
        lay, box = layout.decide(str(self.src), 0, 20)
        self.assertEqual(lay, "blur")


class T6AppAndPosting(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def fake_upload(path, caption, visibility=None):
            self.calls.append(caption)
            return self.result
        self._orig = tiktok.upload
        tiktok.upload = fake_upload
        util.save_settings({"manual_min_gap_minutes": 0})

    def tearDown(self):
        tiktok.upload = self._orig

    def _clip(self, status="queued"):
        f = Path(util.DATA) / f"c{time.time_ns()}.mp4"; f.write_bytes(b"x")
        cid = db.add_clip(video_id="vidP", start=time.time() % 1000, end=time.time() % 1000 + 30, score=5, file=str(f),
                          title="t", caption="t #zbirgta6", status=status)
        db.x("UPDATE clips SET queued_at=? WHERE id=?", (time.time(), cid))
        return cid

    def test_post_ok(self):
        self.result = (True, "postat", "https://www.tiktok.com/@x/video/1")
        cid = self._clip()
        self.assertTrue(app.post_one(db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)))
        r = db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)
        self.assertEqual(r["status"], "posted"); self.assertTrue(r["url"]); self.assertIn("#zbirgta6", self.calls[0])

    def test_post_fail_retry_then_failed(self):
        self.result = (False, "timeout: retea", None)
        cid = self._clip()
        for _ in range(3):
            app.post_one(db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True))
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (cid,), one=True)["status"], "failed")

    def test_unconfirmed_never_reposted(self):
        self.result = (False, "nu am primit confirmarea", None)
        cid = self._clip()
        app.post_one(db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True))
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (cid,), one=True)["status"], "unconfirmed")
        self.assertNotEqual((db.next_clip_to_post(auto=True) or {"id": None})["id"], cid)

    def test_daily_cap_and_pause(self):
        self.result = (True, "postat", None)
        util.save_settings({"daily_post_cap": 1, "auto_post": False})
        db.x("UPDATE clips SET status='skipped' WHERE status IN ('queued','pending')")
        db.x("UPDATE clips SET posted_at=0 WHERE status IN ('posted','unconfirmed')")   # earlier tests = not today
        a, b = self._clip(), self._clip()
        th = threading.Thread(target=app.poster, daemon=True); th.start()
        t_end = time.time() + 20
        while time.time() < t_end and len(self.calls) < 1:
            time.sleep(0.2)
        time.sleep(2)
        self.assertEqual(len(self.calls), 1, "cap 1/zi trebuia respectat")
        self.assertIn("limita", app.STATUS["poster"])
        app.STOP.set(); th.join(10); app.STOP.clear()
        util.save_settings({"daily_post_cap": 30})


class T6bLLM(unittest.TestCase):
    def test_judge_with_mock_openai_server(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from zbirclip import llm
        reply = {"scor": 8, "categorie": "amuzant", "start": 1, "end": 25, "hook": "ZBIR A ÎNNEBUNIT",
                 "titlu": "Zbir și trotineta 😂", "corecturi": [{"i": 0, "w": "știi"}]}

        class M(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert body["messages"][0]["content"].startswith("Ești editorul")
                out = json.dumps({"choices": [{"message": {"content": "```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```"}}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), M)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        os.environ.update(LLM_BASE_URL=f"http://127.0.0.1:{srv.server_port}/v1", LLM_API_KEY="x", LLM_MODEL="m")
        try:
            self.assertTrue(llm.available())
            words = [{"w": "stii", "s": 0.5, "e": 0.8}, {"w": "frate", "s": 1, "e": 1.3}]
            j = llm.judge({"why": ["ras"], "score": 2, "start": 0, "end": 30}, words)
            self.assertEqual(j["scor"], 8)
            pipeline.apply_corrections(words, j["corecturi"])
            self.assertEqual(words[0]["w"], "știi")
        finally:
            srv.shutdown()
            for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
                os.environ.pop(k, None)

    def test_llm_down_returns_none(self):
        from zbirclip import llm
        os.environ.update(LLM_BASE_URL="http://127.0.0.1:9/v1", LLM_API_KEY="x")
        try:
            self.assertIsNone(llm.judge({"why": [], "score": 1, "start": 0, "end": 30}, []))
        finally:
            os.environ.pop("LLM_BASE_URL"); os.environ.pop("LLM_API_KEY")


class T7Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 18000 + os.getpid() % 1000
        cls.srv = web.serve(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json", "X-Zbir-Token": web.TOKEN})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_dashboard(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as r:
            self.assertIn("Zbir", r.read().decode("utf-8"))
        code, st = self.req("/api/state"); self.assertEqual(code, 200); self.assertIn("watching", st)
        cid = db.add_clip(video_id="vidW", start=1, end=30, score=4, file=str(Path(util.DATA) / "w.mp4"), title="t", caption="t")
        code, lst = self.req("/api/clips?status=pending"); self.assertTrue(any(x["id"] == cid for x in lst))
        code, _ = self.req("/api/clip", {"id": cid, "action": "post", "caption": "fara hashtag"})
        r = db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)
        self.assertEqual(r["status"], "queued"); self.assertIn("#zbirgta6", r["caption"])
        code, _ = self.req("/api/clip", {"id": cid, "action": "delete"})
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (cid,), one=True)["status"], "deleted")
        code, _ = self.req("/api/clip", {"id": cid, "action": "post"}); self.assertEqual(code, 400)   # deleted can't be posted
        c2 = db.add_clip(video_id="vidW", start=40, end=70, score=3, file=str(Path(util.DATA) / "w2.mp4"), title="t", caption="t")
        self.req("/api/clip", {"id": c2, "action": "skip"})
        code, lst = self.req("/api/clips?status=all")
        ids = [x["id"] for x in lst]; self.assertIn(c2, ids); self.assertNotIn(cid, ids)   # gallery = all but deleted
        code, r = self.req("/api/settings", {"hashtags": "#gta6 #zbir", "daily_post_cap": 40})
        self.assertEqual(code, 200); self.assertIn("#zbirgta6", r["hashtags"])
        code, _ = self.req("/api/settings", {"post_interval_min_minutes": 0}); self.assertEqual(code, 400)
        code, _ = self.req("/api/control", {"action": "stop"}); self.assertFalse(app.WATCHING.is_set())
        self.req("/api/control", {"action": "start"}); self.assertTrue(app.WATCHING.is_set())
        code, _ = self.req("/api/clip", {"id": 999999, "action": "post"}); self.assertEqual(code, 404)


def first_tag(cap):
    tags = re.findall(r"(?<![\w#])#\w+", cap)
    return tags[0] if tags else None


class T8HashtagsFirst(unittest.TestCase):
    def tearDown(self):
        util.save_settings({"hashtags": ["#zbirgta6"], "extra_hashtags": [], "hashtags_max": 5,
                            "caption_template": "{title} {hashtags}"})

    def test_zbirgta6_always_first(self):
        # user puts it last / forgets it / template puts hashtags first: it's still the FIRST hashtag
        util.save_settings({"hashtags": ["#gta6", "#fyp", "#ZBIRGTA6"], "extra_hashtags": ["#viral", "#romania"],
                            "hashtags_max": 5})
        self.assertEqual(util.cfg()["hashtags"][0], "#zbirgta6")
        for tpl in ("{title} {hashtags}", "{hashtags} {title}", "{title}", "#fyp {title} {hashtags} #extra"):
            util.save_settings({"caption_template": tpl})
            for i in range(20):
                cap = pipeline.build_caption("Zbir a luat-o razna 😂 #gta6", random.Random(i))
                self.assertEqual(first_tag(cap), "#zbirgta6", cap)
                self.assertEqual(cap.lower().count("#zbirgta6"), 1, cap)
        util.save_settings({"caption_template": "{title} {hashtags}"})
        cap = pipeline.build_caption("x", random.Random(3))
        self.assertLessEqual(len(re.findall(r"#\w+", cap)), 5)
        self.assertEqual(first_tag(web.ensure_tags("#fyp #viral textul meu")), "#zbirgta6")
        self.assertEqual(first_tag(web.ensure_tags("fara nimic")), "#zbirgta6")
        self.assertEqual(texts.finalize_caption("a #fyp b #zbirgta6"), "a b #zbirgta6 #fyp")

    def test_upload_gets_zbirgta6_first(self):
        got = {}
        orig = tiktok._upload
        tiktok._upload = lambda v, cap, vis=None: (got.setdefault("cap", cap), (False, "test"))[1]
        try:
            tiktok.upload(Path("x.mp4"), "titlu #fyp #gta6")
        finally:
            tiktok._upload = orig
        self.assertEqual(first_tag(got["cap"]), "#zbirgta6")

    def test_retext_new_variant_each_press(self):
        util.save_settings({"hashtags": ["#zbirgta6", "#zbir"], "extra_hashtags": ["#fyp", "#viral", "#romania", "#funny"],
                            "hashtags_max": 4, "use_llm": False})
        f = Path(util.DATA) / "rt.mp4"; f.write_bytes(b"x")
        cid = db.add_clip(video_id="vidT", start=5000, end=5030, score=3, file=str(f), title="Zbir vs chat, runda 1 🥊",
                          caption="x", cat="chat", text="bla")
        seen = set()
        prev = "Zbir vs chat, runda 1 🥊"
        for i in range(6):
            r = pipeline.retext(cid, random.Random(i))
            self.assertNotEqual(r["title"], prev); prev = r["title"]
            self.assertEqual(first_tag(r["caption"]), "#zbirgta6", r["caption"])
            self.assertIn("#zbir ", r["caption"] + " ")
            self.assertLessEqual(len(re.findall(r"#\w+", r["caption"])), 4)
            seen.add(r["caption"])
        self.assertGreaterEqual(len(seen), 4)
        db.x("UPDATE clips SET status='posted' WHERE id=?", (cid,))
        with self.assertRaises(pipeline.ClipError):
            pipeline.retext(cid)
        util.save_settings({"use_llm": True})


class T9Viral(unittest.TestCase):
    def test_score_range_and_factors(self):
        lo, f1 = viral.compute({"loud": 0, "laugh": 0, "chat_z": 0, "nkw": 0}, 58)
        hi, f2 = viral.compute({"loud": 4, "laugh": 3, "chat_z": 4, "nkw": 4}, 28, True, "ras", 4)
        self.assertTrue(5 <= lo < 40 < 80 <= hi <= 95, (lo, hi))
        self.assertEqual(set(f2), {"hook", "emotie", "cuvinte", "chat", "durata"})
        nochat, f3 = viral.compute({"loud": 4, "laugh": 3, "chat_z": None, "nkw": 4}, 28, True, "ras", 4)
        self.assertNotIn("chat", f3); self.assertGreater(nochat, 80)
        withai, f4 = viral.compute({"loud": 1, "laugh": 0, "chat_z": None, "nkw": 1}, 30, llm_score=10)
        self.assertIn("ai", f4)
        self.assertIn("Hook", viral.tooltip(f2))
        r = web.clip_row({"id": 1, "file": "a.mp4", "start": 0, "end": 30, "score": 3, "viral": None, "factors": None, "src": None})
        self.assertTrue(5 <= r["viral"] <= 95); self.assertFalse(r["can_recut"])


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg lipseste")
class T10Recut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Path(util.DATA) / "recut_src.mp4"; cls.src.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=60",
                        "-f", "lavfi", "-i", "sine=f=440:d=60", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(cls.src)], check=True)

    def _clip(self):
        out = Path(util.DATA) / "clips" / f"vidR_{time.time_ns()}.mp4"; out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"old")
        words = [{"w": f"cuvânt{i}", "s": i * 1.0, "e": i * 1.0 + 0.6} for i in range(60)]
        sents = [{"s": float(a), "e": float(a + 4.6), "t": "propoziție"} for a in range(0, 60, 5)]
        meta = {"start": 1012, "end": 1042, "layout": "blur", "cam_box": None, "cat": "chat", "words": [],
                "feat": {"loud": 2, "laugh": 0, "chat_z": None, "nkw": 1}, "llm": None,
                "src_abs0": 1000.0, "src_s": 12.0, "src_e": 42.0, "src_dur": 60.0, "src_words": words, "src_sents": sents}
        util.save_json(out.with_suffix(".json"), meta)
        return db.add_clip(video_id="vidR", start=1012, end=1042, score=2, file=str(out), title="t", caption="t #zbirgta6",
                           hook="TEST ȘȚ", layout="blur", cat="chat", src=str(self.src)), out

    def test_manual_trim_and_snap(self):
        cid, old = self._clip()
        r = pipeline.recut(cid, dstart=-2, dend=3, snap=False)
        row = db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)
        self.assertAlmostEqual(row["start"], 1010.0, places=1); self.assertAlmostEqual(row["end"], 1045.0, places=1)
        self.assertAlmostEqual(util.ffprobe_duration(row["file"]), 35.0, delta=0.6)
        self.assertFalse(old.exists()); self.assertTrue(Path(row["file"]).exists()); self.assertEqual(row["version"], 2)
        # snapping: start 10+1.3=11.3 -> sentence start 10 (-0.1); end 45+1.2=46.2 -> sentence end 44.6 (+0.35)
        pipeline.recut(cid, dstart=1.3, dend=1.2, snap=True)
        row = db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)
        self.assertAlmostEqual(row["start"] - 1000, 9.9, places=1); self.assertAlmostEqual(row["end"] - 1000, 44.95, places=1)
        m = util.load_json(Path(row["file"]).with_suffix(".json"))
        self.assertTrue(m["words"] and m["words"][0]["s"] >= 0)
        with self.assertRaises(pipeline.ClipError):
            pipeline.recut(cid, dstart=30, dend=-30, snap=False)       # too short

    def test_missing_source_and_web(self):
        cid, _ = self._clip()
        db.x("UPDATE clips SET src=NULL WHERE id=?", (cid,))
        with self.assertRaises(pipeline.ClipError):
            pipeline.recut(cid)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg lipseste")
class T11Effects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Path(util.DATA) / "fx_src.mp4"; cls.src.parent.mkdir(parents=True, exist_ok=True)
        # quiet tone with two loud bursts (= "screams") at 6 s and 14 s
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=20", "-f", "lavfi",
                        "-i", "aevalsrc='0.03*sin(2*PI*300*t)+0.9*sin(2*PI*900*t)*(between(t,6,6.8)+between(t,14,14.8))':s=48000:d=20",
                        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(cls.src)], check=True)

    def words(self):
        ws = "hai că râde haha băi ce prost doamne nu se poate frate gata".split()
        return [{"w": w, "s": 1 + i * 1.4, "e": 1 + i * 1.4 + 0.5} for i, w in enumerate(ws)]

    def test_plan(self):
        p = effects.plan(self.src, 0, 20, self.words(), dict(effects.DEFAULTS))
        self.assertTrue(any(abs(t - 6) < 0.6 for t in p["punch"]), p["punch"])
        self.assertTrue(any(abs(t - 14) < 0.6 for t in p["punch"]), p["punch"])
        self.assertTrue(p["emojis"] and all((effects.ASSETS / "emoji" / f"{e['code']}.png").exists() for e in p["emojis"]))
        self.assertTrue(p["rage"]); self.assertLessEqual(len(p["sfx"]), 2); self.assertTrue(p["boost"])
        off = effects.plan(self.src, 0, 20, self.words(), {k: (False if isinstance(v, bool) else v) for k, v in effects.DEFAULTS.items()})
        self.assertFalse(off["punch"] or off["emojis"] or off["sfx"] or off["boost"] or off["rage"])
        for f in ("boom.wav", "whoosh.wav", "pop.wav"):
            self.assertTrue((effects.ASSETS / "sfx" / f).exists())

    def test_render_with_all_effects_and_loudness(self):
        util.save_settings({"effects": dict(effects.DEFAULTS, progress_bar=True)})
        out = Path(util.DATA) / "fx_out.mp4"
        render.render(self.src, 0, 20, self.words(), "TEST EFECTE ȘȚ", out, "blur")
        self.assertAlmostEqual(util.ffprobe_duration(out), 20, delta=0.5)
        i, peak = render.measure_lufs(out)
        self.assertAlmostEqual(i, -14, delta=1.5); self.assertLessEqual(peak, -0.5)
        ass = out.with_suffix(".ass").read_text(encoding="utf-8")
        self.assertIn("\\fscx130", ass)                     # word bounce
        self.assertIn("&H003B3BFF&", ass)                   # keyword colour (rage = red)
        util.save_settings({"effects": {k: (False if isinstance(v, bool) else v) for k, v in effects.DEFAULTS.items()}})
        render.render(self.src, 0, 10, self.words(), "", out, "blur")    # all effects off still renders
        self.assertAlmostEqual(util.ffprobe_duration(out), 10, delta=0.5)
        util.save_settings({"effects": dict(effects.DEFAULTS)})


class T12Jobs(unittest.TestCase):
    def test_validation_and_local_file_job(self):
        self.assertEqual(jobs.parse_time("1:02:03"), 3723); self.assertEqual(jobs.parse_time("90"), 90)
        self.assertIsNone(jobs.parse_time(""))
        with self.assertRaises(ValueError):
            jobs.parse_time("abc")
        with self.assertRaises(ValueError):
            jobs.submit("nu e link")
        with self.assertRaises(ValueError):
            jobs.submit("https://youtu.be/x", "1:00", "1:10")
        if not HAS_FFMPEG:
            return
        src = Path(util.DATA) / "job_src.mp4"; src.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=70", "-f", "lavfi",
                        "-i", "sine=f=300:d=70", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
        calls = []

        def fake_process(ch, over=None):            # no Whisper in tests: create 2 fake clips per part
            calls.append((ch["t0"], ch["t1"], over))
            for k in range(2):
                f = Path(util.DATA) / f"jobclip_{ch['id']}_{k}.mp4"; f.write_bytes(b"x")
                db.add_clip(video_id=ch["video_id"], start=ch["t0"] + k * 25, end=ch["t0"] + k * 25 + 20, score=1 + k,
                            file=str(f), title="t", caption="t #zbirgta6", viral=40 + 10 * k + len(calls))
            return 2
        orig = pipeline.process_chunk; pipeline.process_chunk = fake_process
        util.save_settings({"chunk_minutes": 0.5})
        try:
            j = jobs.submit(str(src), "0:05", "1:05", 3)
            t_end = time.time() + 60
            while time.time() < t_end and jobs.JOBS[j["id"]]["status"] not in ("gata", "eroare"):
                time.sleep(0.3)
            jj = jobs.JOBS[j["id"]]
            self.assertEqual(jj["status"], "gata", jj); self.assertEqual(jj["progress"], 100)
            self.assertEqual(len(calls), 2); self.assertAlmostEqual(calls[0][0], 5, delta=0.1)
            left = db.q("SELECT * FROM clips WHERE video_id=? AND status='pending'", ("job-" + j["id"],))
            self.assertEqual(len(left), 3)                         # only the best 3 are kept
        finally:
            pipeline.process_chunk = orig
            util.save_settings({"chunk_minutes": 5})


class T13Progress(unittest.TestCase):
    def setUp(self):
        progress.reset()

    def test_channels_are_per_thread(self):
        def job():
            progress.bind("job"); progress.stage("transcriere", 10, "job")
        progress.bind("watcher"); progress.stage("randare", 0, "clip 1/3", clip_n=1, clip_m=3)
        t = threading.Thread(target=job); t.start(); t.join()
        progress.pct(55)
        ch = progress.snapshot()["channels"]
        self.assertEqual(ch["watcher"]["stage"], "randare"); self.assertEqual(ch["watcher"]["pct"], 55)
        self.assertEqual(ch["job"]["pct"], 10)
        progress.bind(None)
        progress.stage("x", 1)              # unbound thread: ignored, never crashes
        self.assertNotIn(None, progress.snapshot()["channels"])

    def test_speed_and_eta(self):
        self.assertIsNone(progress.eta_catch_up(600))
        progress.add_rate(300, 100)          # 3x realtime
        self.assertAlmostEqual(progress.speed(), 3.0)
        self.assertAlmostEqual(progress.eta_catch_up(600, live=True), 300)      # live grows 1x -> net 2x
        self.assertAlmostEqual(progress.eta_catch_up(600, live=False), 200)
        progress.reset(); progress.add_rate(100, 100)
        self.assertIsNone(progress.eta_catch_up(600, live=True))                # 1x: never catches the live edge

    def test_video_progress_math(self):
        vid = "vidPROG"
        db.upsert_video(vid, "Live test", "zbir", time.time() - 3600, "live")
        for a, st in ((0, "done"), (300, "done"), (600, "queued"), (900, "processing")):
            db.add_chunk(vid, a, a + 300, f"/x/{a}.mp4", "dvr", status=st)
        progress.add_rate(300, 60)
        v = app.video_progress(vid, live=True)
        self.assertAlmostEqual(v["total"], 3600, delta=3)
        self.assertEqual(v["processed"], 600); self.assertEqual(v["downloaded"], 1200)
        self.assertAlmostEqual(v["pct"], 16.7, delta=0.2)
        self.assertEqual(v["chunks_queued"], 1)
        self.assertAlmostEqual(v["eta"], 3000 / 4, delta=5)

    def test_progress_api_and_auto_log(self):
        p = app.progress_snapshot()
        for k in ("videos", "stages", "queue", "session", "auto_post", "daily_cap", "min_viral"):
            self.assertIn(k, p)
        util.save_settings({"auto_post": False}); app.note_auto(False)
        util.save_settings({"auto_post": True}); app.progress_snapshot()
        self.assertTrue(any("Auto-postare PORNIT" in e["msg"] for e in progress.snapshot()["events"]))
        util.save_settings({"auto_post": False}); app.progress_snapshot()

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg")
    def test_ffmpeg_progress_callback(self):
        seen = []
        out = Path(util.DATA) / "prog.mp4"
        util.run_ffmpeg_progress(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=4", "-c:v", "libx264",
                                  "-preset", "ultrafast", str(out)], 4.0, seen.append)
        self.assertTrue(out.exists()); self.assertTrue(seen); self.assertGreater(max(seen), 50)
        with self.assertRaises(RuntimeError):
            util.run_ffmpeg_progress(["ffmpeg", "-v", "error", "-i", "/nu/exista.mp4", str(out)], 1.0)


class T14PostOrder(unittest.TestCase):
    def setUp(self):
        db.x("UPDATE clips SET status='skipped' WHERE status IN ('pending','queued')")

    def _c(self, viral, score=1.0, text=None, status="pending", style=None):
        return db.add_clip(video_id="vidO", start=random.random() * 1e5, end=1e5 + 30, score=score, file="f", title="", caption="c",
                           viral=viral, status=status, style=style,
                           text=text or " ".join(random.sample(["a%d" % i for i in range(200)], 12)))

    def test_viral_first_threshold_manual_first(self):
        lo = self._c(35, score=99); mid = self._c(55, score=1); hi = self._c(80, score=0.5)
        plan, below = db.post_plan("viral_first", auto=True, min_viral=40)
        self.assertEqual([r["id"] for r in plan], [hi, mid])                 # viral %, not raw score
        self.assertIn(lo, [r["id"] for r in below])                          # under the 40% threshold
        self.assertEqual(db.next_clip_to_post(auto=True, min_viral=40)["id"], hi)
        man = self._c(10, status="queued")
        db.x("UPDATE clips SET queued_at=? WHERE id=?", (time.time(), man))
        self.assertEqual(db.next_clip_to_post(auto=True, min_viral=40)["id"], man)   # your approval always wins
        self.assertIsNone(db.next_clip_to_post(auto=False, min_viral=40) and None)
        db.x("UPDATE clips SET status='skipped' WHERE id=?", (man,))
        self.assertIsNone(db.next_clip_to_post(auto=False))

    def test_near_duplicates_not_posted_twice(self):
        t = "doamne nu se poate frate ce a facut asta e cel mai tare moment din tot live ul"
        a = self._c(90, text=t); b = self._c(85, text=t + " haha")
        plan, below = db.post_plan("viral_first", auto=True, min_viral=40)
        ids = [r["id"] for r in plan]
        self.assertIn(a, ids); self.assertNotIn(b, ids)
        self.assertTrue(any(r["id"] == b and r["plan"] == "duplicat" for r in below))
        self.assertTrue(db.similar(t, t.replace("frate", "frati")))
        self.assertFalse(db.similar(t, "hai sa mergem la masina ca vine politia acum repede"))
        self.assertEqual(db.similar_text_exists("vidO", t + " haha", None), b)

    def test_disabled_style_not_auto_posted(self):
        r = self._c(90, style="cringe")
        util.save_settings({"styles": {"cringe": False}})
        try:
            self.assertNotIn(r, [x["id"] for x in db.post_plan(auto=True, min_viral=0)[0]])
        finally:
            util.save_settings({"styles": {"cringe": True}})

    def test_sort_rows(self):
        rows = [{"viral": 50, "created": 1, "dur": 30, "style": "rage"}, {"viral": 90, "created": 0, "dur": 20, "style": "fail"},
                {"viral": 20, "created": 2, "dur": 50, "style": "rage"}]
        self.assertEqual([r["viral"] for r in web.sort_rows(rows)], [90, 50, 20])
        self.assertEqual([r["created"] for r in web.sort_rows(rows, "new")], [2, 1, 0])
        self.assertEqual([r["created"] for r in web.sort_rows(rows, "old")], [0, 1, 2])
        self.assertEqual([r["dur"] for r in web.sort_rows(rows, "dur")], [50, 30, 20])
        self.assertEqual(len(web.sort_rows(rows, "viral", 40)), 2)
        self.assertEqual(len(web.sort_rows(rows, "viral", 0, "rage")), 2)


class T15CamLock(unittest.TestCase):
    def test_box_sanity(self):
        face = [0.05, 0.20, 0.03, 0.05]
        self.assertTrue(layout.box_ok([0.0, 0.118, 0.238, 0.238], face))
        self.assertFalse(layout.box_ok([0.036, 0.0, 0.195, 0.175], [0.10, 0.12, 0.10, 0.10]))   # half face at the edge
        self.assertFalse(layout.box_ok([0.0, 0.1, 0.05, 0.3], face))                              # too narrow / tall
        g = [face, [0.18, 0.21, 0.03, 0.05]]
        self.assertFalse(layout.box_ok([0.0, 0.118, 0.15, 0.2], face, group=g))                   # guest cut off
        self.assertTrue(layout.box_ok([0.0, 0.118, 0.238, 0.238], face, group=g))

    def test_lock_needs_consensus_and_resists_jumps(self):
        lk = layout.CamLock(None)
        face = [0.05, 0.20, 0.03, 0.05]; good = [0.0, 0.118, 0.238, 0.238]; other = [0.30, 0.10, 0.25, 0.25]
        self.assertEqual(lk.choose(face, good, good), ("split", [0.0, 0.118, 0.238, 0.238]))
        lk.choose(face, good, good); lk.choose(face, good, good)
        self.assertEqual(lk.locked, good)
        jump = [0.036, 0.0, 0.195, 0.175]                    # the bad box from the real log
        self.assertEqual(lk.choose(face, jump, good)[1], good)
        far_face = [0.40, 0.15, 0.03, 0.05]
        lay, box = lk.choose(far_face, other, good)         # one odd clip: don't move, don't crop wrong
        self.assertEqual(lay, "blur"); self.assertEqual(lk.locked, good)
        lk.choose(far_face, other, good); lk.choose(far_face, other, good)
        self.assertIsNone(lk.locked)                         # real layout change after 3 clips -> re-vote

    def test_group_crop_frames_everyone(self):
        g = [[0.04, 0.23, 0.015, 0.034], [0.09, 0.24, 0.012, 0.025], [0.18, 0.23, 0.013, 0.029]]
        b = layout.group_crop(g, 1280, 720)
        for f in g:
            self.assertTrue(b[0] <= f[0] and f[0] + f[2] <= b[0] + b[2] and b[1] <= f[1] and f[1] + f[3] <= b[1] + b[3])
        self.assertTrue(layout.box_ok(b, g[0], 1280, 720, g))

    def test_wide_cam_filter_fits_width(self):
        v = render.video_filter("split", 1920, 1080, "/tmp/x.ass", [0.0, 0.10, 0.36, 0.15])
        self.assertIn("boxblur", v)                          # wide (2 people) -> fit, never centre-crop the sides
        v2 = render.video_filter("split", 1920, 1080, "/tmp/x.ass", [0.0, 0.118, 0.238, 0.238])
        self.assertNotIn("[cbg]", v2)
        self.assertIn("fontsdir='" + str(util.FONTS).replace(":", "\\:"), v2)          # the folder, not the preset table


class T16Backoff(unittest.TestCase):
    def setUp(self):
        app.clear_backoff(reset_level=True)
        self._orig = tiktok.upload

    def tearDown(self):
        tiktok.upload = self._orig; app.clear_backoff(reset_level=True)

    def test_warning_detection(self):
        self.assertEqual(tiktok.find_warning("Upload\nYou're posting too fast. Try again later.\nOK"), "You're posting too fast. Try again later.")
        self.assertIsNotNone(tiktok.find_warning("Postezi prea des. Încearcă din nou mai târziu."))
        self.assertIsNone(tiktok.find_warning("Your video has been uploaded\nManage your posts"))

    def test_backoff_escalates_and_blocks_posting(self):
        tiktok.upload = lambda *a, **k: (False, "limitare TikTok: You're posting too fast", None)
        f = Path(util.DATA) / "bo.mp4"; f.write_bytes(b"x")
        cid = db.add_clip(video_id="vidB", start=5, end=35, score=1, file=str(f), title="", caption="x #zbirgta6", status="queued")
        self.assertFalse(app.post_one(db.q("SELECT * FROM clips WHERE id=?", (cid,), one=True)))
        b = app.backoff(); self.assertEqual(b["level"], 0); self.assertGreater(b["until"], time.time() + 3500)
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (cid,), one=True)["status"], "queued")   # kept, not failed
        app.set_backoff("again"); self.assertEqual(app.backoff()["level"], 1)
        self.assertGreater(app.backoff()["until"], time.time() + 7100)
        self.assertIsNotNone(app.progress_snapshot()["backoff"])
        app.clear_backoff(); self.assertIsNone(app.progress_snapshot()["backoff"])
        db.x("UPDATE clips SET status='skipped' WHERE id=?", (cid,))

    def test_caption_never_repeated(self):
        calls = []
        tiktok.upload = lambda p, cap, *a, **k: (calls.append(cap), (True, "postat", None))[1]
        f = Path(util.DATA) / "rep.mp4"; f.write_bytes(b"x")
        Path(f).with_suffix(".json").write_text(json.dumps({"words": [], "hook": "h", "title": "Titlu"}), encoding="utf-8")
        cap = "Zbir a luat-o razna #zbirgta6 #fyp"
        a = db.add_clip(video_id="vidR", start=1, end=30, score=1, file=str(f), title="Titlu", caption=cap, status="queued")
        app.post_one(db.q("SELECT * FROM clips WHERE id=?", (a,), one=True))
        b = db.add_clip(video_id="vidR", start=100, end=130, score=1, file=str(f), title="Titlu", caption=cap, status="queued")
        app.post_one(db.q("SELECT * FROM clips WHERE id=?", (b,), one=True))
        self.assertEqual(len(calls), 2); self.assertNotEqual(calls[0], calls[1])
        self.assertTrue(calls[1].split("#")[1].startswith("zbirgta6"))


class T17Persistence(unittest.TestCase):
    def test_crash_during_posting_is_verified_not_reposted(self):
        f = Path(util.DATA) / "v.mp4"; f.write_bytes(b"x")
        cap = "Zbir s-a enervat pe chat rau de tot #zbirgta6"
        a = db.add_clip(video_id="vidV", start=1, end=30, score=1, file=str(f), title="", caption=cap, status="posting")
        b = db.add_clip(video_id="vidV", start=60, end=90, score=1, file=str(f), title="", caption="Alt clip fara nicio legatura aici #zbirgta6", status="posting")
        c2 = db.add_clip(video_id="vidV", start=120, end=150, score=1, file=str(f), title="", caption="Inca unul complet diferit acum #zbirgta6", status="posting")
        db._conn.close(); db._conn = None                      # simulated close / crash + restart
        self.assertEqual({db.q("SELECT status FROM clips WHERE id=?", (i,), one=True)["status"] for i in (a, b, c2)}, {"verify"})
        posts = [("https://www.tiktok.com/@x/video/1", "Zbir s-a enervat pe chat rau de tot #zbirgta6 #fyp")]
        app.verify_interrupted(posts)
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (a,), one=True)["status"], "posted")
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (b,), one=True)["status"], "queued")
        db.x("UPDATE clips SET status='verify' WHERE id=?", (c2,))
        orig = tiktok.recent_posts; tiktok.recent_posts = lambda n=15: None
        try:
            app.verify_interrupted()
        finally:
            tiktok.recent_posts = orig
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (c2,), one=True)["status"], "unconfirmed")
        db.x("UPDATE clips SET status='skipped' WHERE video_id='vidV'")

    def test_processing_chunk_resumes(self):
        cid = db.add_chunk("vidK", 0, 300, "/x.mp4", "dvr", status="processing")
        db._conn.close(); db._conn = None
        self.assertEqual(db.q("SELECT status FROM chunks WHERE id=?", (cid,), one=True)["status"], "queued")
        db.finish_chunk(cid, "done")

    def test_corrupt_db_restored_from_backup(self):
        n = db.q("SELECT COUNT(*) n FROM clips", one=True)["n"]
        db.backup()
        db._conn.close(); db._conn = None
        p = Path(util.DATA) / "state.db"
        for ext in ("-wal", "-shm"):
            Path(str(p) + ext).unlink(missing_ok=True)
        p.write_bytes(b"this is not a database" * 100)
        self.assertEqual(db.q("SELECT COUNT(*) n FROM clips", one=True)["n"], n)
        self.assertTrue(list(Path(util.DATA).glob("state.db.corrupt-*")))

    def test_json_atomic_and_bak(self):
        p = Path(util.DATA) / "t.json"
        util.save_json(p, {"a": 1}); util.save_json(p, {"a": 2})
        p.write_text('{"a": 3', encoding="utf-8")              # half-written
        self.assertEqual(util.load_json(p, {}), {"a": 2})

    def test_settings_migration(self):
        util.save_json(util.SETTINGS, dict(util.load_json(util.SETTINGS, {}), daily_post_cap=30, post_interval_min_minutes=30,
                                           post_interval_max_minutes=45, post_order="best_first", hashtags_max=4, _migrated=0))
        util._cfg = None
        self.assertTrue(util.migrate_settings())
        c = util.cfg()
        self.assertEqual(c["daily_post_cap"], 45); self.assertEqual(c["post_interval_min_minutes"], 20)
        self.assertEqual(c["hashtags_max"], 4)                 # your own choice stays
        self.assertFalse(util.migrate_settings())


class T18Security(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 19000 + os.getpid() % 900
        cls.srv = web.serve(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def raw(self, path, body=None, headers=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                   headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_binds_localhost_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_csrf_token_required(self):
        code, _ = self.raw("/api/control", {"action": "stop"}); self.assertEqual(code, 403)
        code, _ = self.raw("/api/control", {"action": "stop"}, {"X-Zbir-Token": "gresit"}); self.assertEqual(code, 403)
        code, _ = self.raw("/api/control", {"action": "stop"}, {"X-Zbir-Token": web.TOKEN, "Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertTrue(app.WATCHING.is_set())
        code, page = self.raw("/")
        self.assertIn(web.TOKEN.encode(), page)                # the real page gets the token

    def test_host_header_and_traversal(self):
        code, _ = self.raw("/api/state", headers={"Host": "evil.example:80"}); self.assertEqual(code, 403)
        code, _ = self.raw("/static/../../zbirclip/util.py"); self.assertEqual(code, 404)
        code, _ = self.raw("/static/..%2F..%2Fconfig.yaml"); self.assertEqual(code, 404)
        outside = db.add_clip(video_id="vidS", start=1, end=2, score=0, file="/etc/passwd", title="", caption="", status="skipped")
        code, _ = self.raw(f"/media/{outside}/video"); self.assertEqual(code, 403)
        code, _ = self.raw("/media/abc/video"); self.assertEqual(code, 400)

    def test_url_validation(self):
        for bad in ("file:///etc/passwd", "--exec rm", "http://127.0.0.1:8777/x", "https://evil.com/watch?v=1", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                util.check_url(bad)
        self.assertTrue(util.check_url("https://www.youtube.com/watch?v=sigWeXgcJjI", youtube_only=True))
        self.assertTrue(util.check_url("https://kick.com/zbir"))
        with self.assertRaises(ValueError):
            util.check_url("https://kick.com/zbir", youtube_only=True)
        with self.assertRaises(ValueError):
            jobs.submit("https://evil.com/x.mp4")

    def test_ass_escaping(self):
        a = render.build_ass([{"w": "{\\pos(1,1)}hack", "s": 0.1, "e": 0.5}], 3, "TITLU {\\fs999} ȘȚ", "blur")
        self.assertNotIn("{\\fs999}", a); self.assertNotIn("{\\pos(1,1)}hack", a); self.assertIn("ȘȚ", a)
        if HAS_FFMPEG:                                     # paths with spaces and apostrophes (D:\Zbir's clips\...)
            d = Path(util.DATA) / "dir cu spatii si ap'ostrof"; d.mkdir(exist_ok=True)
            ass = d / "t.ass"; ass.write_text(a, encoding="utf-8")
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=1080x1920:d=1", "-filter_complex",
                                render.subs_tail("[0:v]", ass, pre=""), "-map", "[vo]", "-frames:v", "1", str(d / "o.jpg")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])


class T19Updater(unittest.TestCase):
    def _zip(self, files, top="zbir-cliping-main"):
        import io, zipfile
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for k, v in files.items():
                z.writestr(f"{top}/{k}", v)
        return b.getvalue()

    def _root(self):
        r = Path(util.DATA) / f"upd{time.time_ns()}"
        (r / "zbirclip").mkdir(parents=True)
        (r / "version.txt").write_text("1.0.0"); (r / "zbirclip" / "__main__.py").write_text("old")
        (r / "requirements.txt").write_text("pyyaml\n"); (r / ".env").write_text("LLM_API_KEY=secret")
        (r / "data").mkdir(); (r / "data" / "state.db").write_text("db")
        (r / "config.local.yaml").write_text("x: 1")
        return r

    def test_versions(self):
        self.assertGreater(updater.vtuple("1.10.0"), updater.vtuple("1.9.9"))
        self.assertEqual(updater.vtuple("v2"), (2, 0, 0))

    def test_apply_keeps_user_files(self):
        r = self._root()
        files = updater._extract(self._zip({"version.txt": "1.2.0", "zbirclip/__main__.py": "new", "zbirclip/new.py": "x",
                                            "requirements.txt": "pyyaml\n", ".env": "HACKED", "data/state.db": "HACKED",
                                            "config.local.yaml": "HACKED", "weird_top/file": "ignored"}))
        old, new = updater.apply(files, r, log_fn=lambda m: None, pip=False, smoke=False)
        self.assertEqual((old, new), ("1.0.0", "1.2.0"))
        self.assertEqual((r / "zbirclip" / "__main__.py").read_text(), "new")
        self.assertEqual((r / ".env").read_text(), "LLM_API_KEY=secret")
        self.assertEqual((r / "data" / "state.db").read_text(), "db")
        self.assertEqual((r / "config.local.yaml").read_text(), "x: 1")
        self.assertFalse((r / "weird_top").exists())
        self.assertTrue(list((r / "data" / "backups").glob("code_1.0.0_*.zip")))

    def test_rollback_on_failure(self):
        r = self._root()
        files = updater._extract(self._zip({"version.txt": "1.3.0", "zbirclip/__main__.py": "broken", "zbirclip/added.py": "x"}))
        orig = updater._venv_python
        updater._venv_python = lambda root: sys.executable
        try:
            with self.assertRaises(RuntimeError):
                updater.apply(files, r, log_fn=lambda m: None, pip=False, smoke=True)   # 'import zbirclip.app' fails there
        finally:
            updater._venv_python = orig
        self.assertEqual((r / "zbirclip" / "__main__.py").read_text(), "old")
        self.assertEqual((r / "version.txt").read_text(), "1.0.0")
        self.assertFalse((r / "zbirclip" / "added.py").exists())

    def test_rejects_bad_archives(self):
        with self.assertRaises(RuntimeError):
            updater._extract(self._two_tops())
        with self.assertRaises(RuntimeError):
            updater._extract(self._zip({"../evil": "x"}))
        with self.assertRaises(RuntimeError):
            updater.apply({Path("README.md"): b"x"}, self._root(), log_fn=lambda m: None, pip=False, smoke=False)
        self.assertEqual(updater.repo(), "LucianCeusanRo/zbir-cliping")

    def _two_tops(self):
        import io, zipfile
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("a/version.txt", "1"); z.writestr("b/x", "2")
        return b.getvalue()


class T20DataAndStyles(unittest.TestCase):
    def test_data_move_validation_and_remap(self):
        with self.assertRaises(ValueError):
            util.request_data_move("relativ/cale")
        with self.assertRaises(ValueError):
            util.request_data_move(str(util.DATA))
        with self.assertRaises(ValueError):
            util.request_data_move(str(Path(util.DATA) / "sub"))
        src = Path(util.DATA) / "mvsrc"; dst = Path(util.DATA).parent / f"mvdst{time.time_ns()}"
        (src / "clips").mkdir(parents=True)
        (src / "clips" / "a.mp4").write_bytes(b"v")
        import sqlite3
        c = sqlite3.connect(src / "state.db")
        c.execute("CREATE TABLE clips (id INTEGER, file TEXT, thumb TEXT, src TEXT)"); c.execute("CREATE TABLE chunks (id INTEGER, path TEXT)")
        c.execute("INSERT INTO clips VALUES (1, ?, NULL, NULL)", (str(src / "clips" / "a.mp4"),)); c.commit(); c.close()
        util.DATA_MOVE.write_text(json.dumps({"from": str(src), "to": str(dst)}), encoding="utf-8")
        try:
            util._do_pending_move()
            self.assertTrue((dst / "clips" / "a.mp4").exists()); self.assertFalse((src / "clips").exists())
            c = sqlite3.connect(dst / "state.db")
            self.assertEqual(c.execute("SELECT file FROM clips").fetchone()[0], str(dst / "clips" / "a.mp4")); c.close()
            self.assertEqual(util.DATA_POINTER.read_text().strip(), str(dst))
        finally:
            util.DATA_POINTER.unlink(missing_ok=True)

    def test_janitor_budget_and_skipped(self):
        f = Path(util.DATA) / "clips" / "old_skipped.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"x" * 1000)
        cid = db.add_clip(video_id="vidJ", start=1, end=30, score=1, file=str(f), title="", caption="", status="skipped",
                          created=time.time() - 10 * 86400)
        util.save_settings({"skipped_retention_days": 7})
        pipeline.janitor()
        self.assertFalse(f.exists())
        self.assertEqual(db.q("SELECT status FROM clips WHERE id=?", (cid,), one=True)["status"], "skipped")   # row kept

    def test_categorize(self):
        self.assertEqual(styles.categorize("ce pula mea ma enervez", ["injuratura"], {"loud": 3})[0], "rage")
        self.assertEqual(styles.categorize("am murit iar am ratat", [], {})[0], "fail")
        self.assertEqual(styles.categorize("castigatorul giveaway ia un ps5", ["giveaway"], {})[0], "giveaway")
        self.assertEqual(styles.categorize("mersi pentru donatie chat ce ziceti", [], {"chat_z": 2})[0], "chat")
        self.assertEqual(styles.categorize("hahaha esti un fraier", ["ras"], {})[0], "roast")
        self.assertEqual(styles.categorize("hai", [], {})[0], "moment")
        self.assertEqual(len(styles.stats()), len(styles.CATEGORIES))

    def test_hook_trim(self):
        words = [{"w": "x", "s": 5.0 + i * 0.5, "e": 5.3 + i * 0.5} for i in range(30)]
        score = np.zeros(60); score[12] = 5; score[24] = 4
        s0, e0 = detect.hook_trim(2.0, 30.0, 12, words, score, 20, 60, 60)
        self.assertAlmostEqual(s0, 4.7, delta=0.01)                 # no dead air at the start
        self.assertGreaterEqual(e0, 26.0)                            # payoff at 24 s kept + 2 s



class T21RealRowChunk(unittest.TestCase):
    """Regresie: worker-ul primeste sqlite3.Row (nu dict) din db.next_chunk()."""
    def test_process_chunk_accepts_sqlite_row(self):
        from zbirclip import pipeline, progress as pr
        db.upsert_video("rowvid", "t", "z", time.time(), "vod")
        db.add_chunk("rowvid", 0, 60, "/nu/exista.mp4", "vod")
        ch = None
        for _ in range(50):
            r = db.next_chunk()
            if r is None: break
            if r["video_id"] == "rowvid": ch = r; break
        self.assertIsNotNone(ch)
        self.assertFalse(isinstance(ch, dict))
        class Stop(Exception): pass
        ow, os_ = pipeline._window, pr.stage
        pipeline._window = lambda c: ("/nu/exista.mp4", 0.0, 0.0, None)
        def boom(*a, **k): raise Stop()
        pr.stage = boom
        try:
            with self.assertRaises(Stop):
                pipeline.process_chunk(ch)
        finally:
            pipeline._window, pr.stage = ow, os_

if __name__ == "__main__":
    unittest.main()
