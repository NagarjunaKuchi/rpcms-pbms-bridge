import ast, os, subprocess, sys, zipfile, glob
core = {d for d in os.listdir("/home/claude/odoo17/odoo-17.0.post20261021/odoo/addons")}
roots = ["/home/claude/openg2p-program", "/home/claude/openg2p/openg2p-registry", "/home/claude/oca/queue", "/home/claude/oca/openg2p-rest-framework", "/home/claude/oca/odoo-formio", "/home/claude/oca/openg2p-storage", "/home/claude/odoo17/extra"]
def scan():
    mods = {}
    for p in roots:
        for d in os.listdir(p):
            f = f"{p}/{d}/__manifest__.py"
            if os.path.exists(f): mods[d] = ast.literal_eval(open(f).read())
    return mods
failed = set()
for _ in range(15):
    mods = scan(); need, todo, unres = set(), ["g2p_payment_g2p_connect"], set()
    while todo:
        m = todo.pop()
        if m in need: continue
        need.add(m)
        for d in mods[m].get("depends", []):
            if d in mods: todo.append(d)
            elif d not in core: unres.add(d)
    unres -= failed
    if not unres: break
    for d in sorted(unres):
        r = subprocess.run([sys.executable, "-m", "pip", "download", f"odoo-addon-{d}>=17.0,<17.1", "--no-deps", "--only-binary", ":all:", "-d", "/home/claude/odoo17/wheels", "-q"], capture_output=True, text=True)
        if r.returncode:
            print("NOT ON PYPI:", d, r.stderr.strip().splitlines()[-1][:120]); failed.add(d); continue
        for w in glob.glob(f"/home/claude/odoo17/wheels/odoo_addon_{d}-*.whl"):
            z = zipfile.ZipFile(w)
            for n in z.namelist():
                if n.startswith("odoo/addons/"): z.extract(n, "/tmp/wx")
        os.system("cp -r /tmp/wx/odoo/addons/* /home/claude/odoo17/extra/ 2>/dev/null; rm -rf /tmp/wx")
        print("got", d)
print("still unresolved:", sorted(failed))
