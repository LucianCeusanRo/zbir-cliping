"""Pick the 9:16 layout for a clip by looking at the actual frames.

1. YuNet face detection on frames sampled from the clip (model bundled in models/).
2. A face that stays put = streamer webcam. If it is inside the configured cam_box -> split with cam_box
   (exact crop of Zbir's cam overlay). If the cam moved (new stream layout) -> split with an auto box:
   the webcam rectangle found by facecam.find_cam (static-border detection), or a 16:9 crop around the faces.
3. No stable face (cam off / full-screen game / BRB) -> 'blur': whole 16:9 frame over a blurred fill.
"""
import threading
import numpy as np
from . import facecam
from .util import DATA, cfg, load_json, log, save_json

# --- stable cam box per live -------------------------------------------------------------------------------
# The auto box used to be recomputed for every clip, so one bad frame set could give a half-face crop.
# Now: every candidate must pass sanity checks; candidates vote; once VOTES_TO_LOCK of the last VOTE_WINDOW
# agree (IoU >= AGREE_IOU) the box is LOCKED for that live (saved in data/<video>/cam_lock.json, survives
# restarts). A locked box is only dropped after UNLOCK_MISSES clips in a row where Zbir's face is not in it
# (real layout change). Until locked: last good box -> configured cam_box -> blur (never a risky crop).
VOTE_WINDOW, VOTES_TO_LOCK, AGREE_IOU, UNLOCK_MISSES = 6, 3, 0.6, 3
_locks, _lk = {}, threading.Lock()


def box_ok(box, face, w=1920, h=1080, group=None):
    """Sanity: plausible webcam overlay that fully contains the face with margin, not a zoomed half face."""
    if not box or len(box) != 4:
        return False
    bx, by, bw, bh = [float(v) for v in box]
    if bw < 0.10 or bh < 0.10 or bw > 0.60 or bh > 0.75 or bx < -0.01 or by < -0.01 or bx + bw > 1.01 or by + bh > 1.01:
        return False
    aspect = (bw * w) / max(1e-6, bh * h)          # webcams are 4:3 .. 16:9 (a little crop tolerance)
    if not 1.0 <= aspect <= 2.1:
        return False
    if face is not None:
        fx, fy, fw, fh = face
        for gx, gy, gw, gh in (group or [face]):    # EVERY person (Zbir + guest) fully inside, with margin
            mx, my = 0.15 * gw, 0.15 * gh
            if gx - mx < bx - 1e-6 or gy - my < by - 1e-6 or gx + gw + mx > bx + bw + 1e-6 or gy + gh + my > by + bh + 1e-6:
                return False
        if fw / bw > 0.65:                          # face filling the box = zoomed-in half face
            return False
        gw_ = facecam.union(group)[2] if group and len(group) > 1 else fw
        if fw / bw < 0.04 and gw_ / bw < 0.25:      # people lost in a huge box (probably not the webcam)
            return False
    return True


def face_in(box, face):
    if not box or face is None:
        return False
    cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
    return box[0] <= cx <= box[0] + box[2] and box[1] <= cy <= box[1] + box[3]


class CamLock:
    def __init__(self, key):
        self.key = key
        self.path = (DATA / key / "cam_lock.json") if key else None
        d = load_json(self.path, {}) if self.path else {}
        self.locked = d.get("locked"); self.last_good = d.get("last_good")
        self.votes = d.get("votes", []); self.misses = d.get("misses", 0)

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            save_json(self.path, {"locked": self.locked, "last_good": self.last_good, "votes": self.votes, "misses": self.misses})

    def vote(self, box):
        self.votes = (self.votes + [list(map(float, box))])[-VOTE_WINDOW:]
        agree = [v for v in self.votes if facecam.iou(v, box) >= AGREE_IOU]
        if len(agree) >= VOTES_TO_LOCK:
            med = [round(float(np.median([a[k] for a in agree])), 4) for k in range(4)]
            if med != self.locked:
                log.info("camera BLOCATA pentru %s: %s (%d/%d cadre de acord)", self.key, med, len(agree), len(self.votes))
            self.locked, self.misses = med, 0
        self.last_good = list(map(float, box))

    def choose(self, face, candidate, default, group=None):
        """face = normalized [x,y,w,h] or None; candidate = auto box or None. Returns (layout, box)."""
        if self.locked:
            if face is None or (face_in(self.locked, face) and all(face_in(self.locked, g) for g in (group or []))):
                self.misses = 0
                return "split", self.locked                     # locked: never moves while Zbir is in it
            self.misses += 1
            if self.misses < UNLOCK_MISSES:                     # one odd clip doesn't move the camera
                if default and face_in(default, face) and box_ok(default, face, group=group):
                    return "split", [round(float(v), 4) for v in default]
                return "blur", None
            log.info("camera deblocata pentru %s: fata a iesit din box de %d ori la rand", self.key, self.misses)
            self.locked, self.votes, self.misses = None, [], 0
        prev = self.last_good
        if candidate and box_ok(candidate, face, group=group):
            self.vote(candidate)
            if self.locked:
                return "split", self.locked
            # not locked yet: use it only if it agrees with the previous good box or the configured one
            if any(b and facecam.iou(candidate, b) >= AGREE_IOU for b in (prev, default)):
                return "split", [round(v, 4) for v in candidate]
        for b in (prev, default):
            if b and face is not None and face_in(b, face) and box_ok(b, face, group=group):
                return "split", [round(float(v), 4) for v in b]
        return "blur", None


def _lock_for(key):
    with _lk:
        if key not in _locks:
            _locks[key] = CamLock(key)
        return _locks[key]





def group_crop(group, w, h, margin=1.2):
    """16:9 crop that frames ALL people (Zbir + guest) centred, never cutting one in half."""
    gx, gy, gw, gh = facecam.union(group)
    fw = float(np.median([g[2] for g in group])); fh = float(np.median([g[3] for g in group]))
    cw = gw + 2 * margin * fw * 1.6                 # shoulders + air on both sides
    ch = cw * (w / h) * 9 / 16                      # 16:9 in pixels
    if ch < gh + 2.6 * fh:                          # head + chest visible
        ch = gh + 2.6 * fh; cw = ch * (h / w) * 16 / 9
    cw, ch = min(cw, 0.6), min(ch, 0.75)
    cx, cy = gx + gw / 2, gy + gh * 0.55
    x0 = float(np.clip(cx - cw / 2, 0, 1 - cw)); y0 = float(np.clip(cy - ch * 0.42, 0, 1 - ch))
    return [round(x0, 4), round(y0, 4), round(cw, 4), round(ch, 4)]


def candidate_box(video, s0, e0, face, w, h, c, group=None):
    """Auto box for a clip: configured cam_box if every face is in it, else the detected webcam rectangle
    (if it holds everyone), else a 16:9 crop framing all people. May still be bad -> CamLock checks it."""
    group = group or [face]
    cb = c.get("cam_box") or [0, 0, 0, 0]
    if all(face_in(cb, g) for g in group) and box_ok(cb, face, w, h, group):
        return [float(v) for v in cb]
    try:
        r = facecam.find_cam(video, s0, e0)
        if r.get("mode") == "cam" and r["box"][2] >= 0.08 and box_ok(r["box"], face, w, h, group):
            return [float(v) for v in r["box"]]
    except Exception:
        pass
    return group_crop(group, w, h)


def decide(video, s0, e0, key=None):
    """key = video id: the cam box is locked per live once several clips agree."""
    c = cfg()
    want = c.get("layout", "split")
    if want != "split" or not c.get("auto_cam", True):
        return want, c.get("cam_box")
    lock = _lock_for(key or str(video))
    default = c.get("cam_box")
    try:
        frames = facecam.sample_frames(video, s0, e0, 8)
        if len(frames) < 4:
            return lock.locked and ("split", lock.locked) or ("blur", None)
        h, w = frames[0].shape[:2]
        dets = facecam.detect_faces(frames)
        face, hits = facecam.stable_face(dets, len(frames))
        others = [f for f, _ in facecam.stable_faces(dets, len(frames))]
    except Exception as e:
        log.warning("detectie fata esuata (%s) -> folosesc box-ul blocat / cam_box din config", str(e)[:100])
        return "split", lock.locked or default
    if face is None or hits < 4:                 # cam off / BRB / full-screen game
        return "blur", None
    nf = [face[0] / w, face[1] / h, face[2] / w, face[3] / h]
    if nf[2] > 0.25:                             # huge face = full-screen camera (IRL), not an overlay
        return "blur", None
    group = facecam.group_faces(nf, [[o[0] / w, o[1] / h, o[2] / w, o[3] / h] for o in others])
    if len(group) > 1:
        log.debug("camera: %d persoane in cadru", len(group))
    cand = candidate_box(video, s0, e0, nf, w, h, c, group)
    before = lock.locked
    lay, box = lock.choose(nf, cand, default, group)
    if box != before and box is not None and box != default:
        log.info("camera: box %s (%s)", [round(float(x), 4) for x in box], "blocat" if box == lock.locked else "provizoriu, astept consens")
    lock.save()
    return lay, box
