import sys, os, subprocess

ROOT = r"C:\python projects\SoundGrip"
os.chdir(ROOT)

CANDS = [
    r"C:\Users\dota-\AppData\Local\Programs\Python\Python312\python.exe",
    r"C:\Users\dota-\AppData\Local\Programs\Python\Python313\python.exe",
    r"C:\Users\dota-\AppData\Local\Programs\Python\Python311\python.exe",
    os.path.join(ROOT, "env", "Scripts", "python.exe"),
    sys.executable,
]

print("=== interpreter probe ===")
good = []
for c in CANDS:
    if not os.path.exists(c):
        print("MISSING", c)
        continue
    try:
        p = subprocess.run([c, "-c", "import kivy,sys;print(sys.version.split()[0],'KIVY',kivy.__version__)"],
                           capture_output=True, text=True, timeout=60)
        print("OK   ", c, "->", p.stdout.strip() or p.stderr.strip()[-200:])
        if p.returncode == 0:
            good.append(c)
    except Exception as e:
        print("FAIL ", c, e)

print("=== usable interpreters ===")
for g in good:
    print(g)
