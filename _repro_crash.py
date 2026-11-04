# -*- coding: utf-8 -*-
"""Headless-репро падения при выборе трека. Ловит traceback, печатает его."""
import os, sys, json, traceback, faulthandler

# faulthandler ловит ЖЁСТКИЕ краши (abort/segfault) -- обычный traceback их не видит
LOG = open(r"C:\python projects\SoundGrip\_repro_log.txt", "w", encoding="utf-8",
           buffering=1)


def say(*a):
    s = " ".join(str(x) for x in a)
    LOG.write(s + "\n")
    LOG.flush()
    print(s, flush=True)


faulthandler.enable(file=LOG)
sys.stdout = LOG
sys.stderr = LOG

os.environ.setdefault('KIVY_NO_ARGS', '1')

sys.path.insert(0, r"C:\python projects\SoundGrip")
os.chdir(r"C:\python projects\SoundGrip")

say("### importing main ...")
import main as m
import tracks as tracklib
say("### import OK")

CRASH = []


def _boom(where, fn):
    try:
        return fn()
    except BaseException:
        tb = traceback.format_exc()
        CRASH.append((where, tb))
        say("\n" + "=" * 78)
        say("CRASH IN %s" % where)
        say("=" * 78)
        say(tb)
        return None


tracks = tracklib.list_tracks()
say("### tracks: %d" % len(tracks))

for t in tracks:
    say("\n### GameWidget(track=%r)" % (t,))
    gw = _boom("GameWidget.__init__", lambda t=t: m.GameWidget(track=t, volume=0.0))
    if gw is None:
        continue
    say("   bpm=%s beat=%.5f tick=%.5f notes=%d state=%s"
        % (gw.bpm, gw.beat_duration, gw.tick_duration, len(gw.level_notes), gw.state))
    _boom("start_track()", gw.start_track)
    say("   after start: state=%s" % gw.state)

    def _frames(g=gw, k=200):
        for i in range(k):
            g.update(1.0 / 60.0)
        return True
    _boom("update() x200", _frames)
    say("   after upd: active=%d ptr=%d idx=%d" % (len(gw._active), gw.note_pointer, gw._idx))
    _boom("toggle_pause()", gw.toggle_pause)
    _boom("start_track() resume", gw.start_track)
    say("   after pause/resume: state=%s" % gw.state)

say("\n########## SUMMARY ##########")
if CRASH:
    for where, tb in CRASH:
        say("FAILED: %s" % where)
    sys.exit(1)
say("NO CRASH REPRODUCED")
