import re, io
src = io.open('_spheres_smoke.py', encoding='utf-8').read()
attrs = sorted(set(re.findall(r'\bw\.(_[A-Za-z_][A-Za-z0-9_]*)', src)))
print(len(attrs))
print(attrs)
