import os, re, json

ROOT = r"C:\python projects\SoundGrip"
os.chdir(ROOT)

src = open("main.py", encoding="utf-8").read().splitlines()
pat = re.compile(r"^\s*def (load_track|start_track|reset|_spawn_one|_spawn_due|update|audio_now|_audio_load|stop_playback)\b")
for i, l in enumerate(src, 1):
    if pat.match(l):
        print("%5d: %s" % (i, l))

print("\n===== beatmap JSON meta =====")
for f in os.listdir("beatmaps"):
    p = os.path.join("beatmaps", f)
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(f, "PARSE FAIL", e); continue
    if isinstance(d, dict):
        keys = {k: (type(v).__name__, (v if not isinstance(v, (list, dict)) else "len=%d" % len(v)))
                for k, v in d.items()}
        notes = d.get("notes", [])
        n0 = notes[0] if notes else None
        print("%-55s keys=%s" % (f, keys))
        print("      first note: %r" % (n0,))
    else:
        print(f, "type", type(d))
