import sys, subprocess, shutil, tarfile, hashlib, os, uuid
from pathlib import Path

R = Path(__file__).parent.resolve()
IMG, CONT, BLD, CACHE, TMP = R/"images", R/"containers", R/"build", R/"cache", R/"tmp"
LYR = CACHE/"layers"
BS, BR, BW, E = '\033[34m\033[1m', '\033[31m\033[1m', '\033[97m', '\033[0m'

def run(cmd, **k):
    if 'c' in k: k['capture_output']=True; del k['c']
    if 't' in k: k['text']=True; del k['t']
    return subprocess.run(['sudo']+cmd if k.pop('s',False) else cmd, **k)

def p(c, m): print(f"{c}{m}{E}")
def ok(): print(f"{BW}true{E}")
def err(m): print(f"{BW}false {BR}{m}{E}"); sys.exit(1)

def banner():
    try: from pyfiglet import Figlet; print(f"{BS}{Figlet(font='slant').renderText('Boxer')}{E}")
    except: p(BS, "=== Boxer ===")

def mount(lowers, upper, work, merge):
    run(['mount', '-t', 'overlay', 'overlay', '-o', f"lowerdir={':'.join(map(str, lowers))},upperdir={upper},workdir={work}", str(merge)], s=True, check=True)
def umount(path): run(['umount', '-l', str(path)], s=True)

def get_base(img, path, quiet=False):
    CACHE.mkdir(exist_ok=True); tpl = CACHE/img
    if not tpl.exists():
        if not quiet: p(BS, f"Caching '{img}'...")
        tpl.mkdir()
        try:
            with tarfile.open(path, 'r:xz') as t: r = next((x.split('/')[0] for x in t.getnames() if '/' in x), None)
            cmd = ['tar', '-xf', str(path), '-C', str(tpl), '--numeric-owner'] + (['--strip-components=1'] if r else [])
            run(cmd, check=True); (tpl/"etc").mkdir(exist_ok=True)
            if not (tpl/"etc/os-release").exists(): (tpl/"etc/os-release").write_text('NAME=Linux\nID=linux\n')
        except: shutil.rmtree(tpl); raise
    return tpl

def setup(q=False):
    try:
        if not q: banner()
        pkgs = ["systemd-container", "uidmap", "wget"]
        if any(run(['dpkg', '-s', x], s=True, c=True).returncode for x in pkgs):
            if not q: p(BS, "Installing deps...")
            run(['apt', 'update'], s=True, c=True); run(['apt', 'install', '-y'] + pkgs, s=True, c=True)
        run(['pip', 'install', '-q', 'pyfiglet'], c=True)
        for d in [IMG, CONT, BLD, CACHE, LYR, TMP]: d.mkdir(parents=True, exist_ok=True)
        if not (IMG/"alpine.tar.xz").exists():
            if run(['wget', '-q', '-O', str(IMG/"alpine.tar.xz"), "https://github.com/ssbagpcm/rootfs/releases/download/rootfs/alpine.tar.xz"]).returncode: return err("Download failed")
        if not (BLD/"Box").exists(): (BLD/"Box").write_text('FROM alpine\nRUN mkdir /test && echo "Hello" > /test/hello.txt\nRUN apk update && apk add fastfetch\nRUN fastfetch')
        if not q: ok()
    except Exception as e: return err(str(e))

def ls():
    CONT.mkdir(exist_ok=True); IMG.mkdir(exist_ok=True); p(BW, "CONTAINERS")
    for d in filter(Path.is_dir, CONT.iterdir()): print(f"  {d.name:<20} {int(run(['du','-sk',str(d)],c=True,t=True).stdout.split()[0])/1024:.1f} MB")
    p(BW, "\nIMAGES")
    for f in IMG.glob('*.tar.xz'): print(f"  {f.name[:-7]}")

def ctn_ls():
    CONT.mkdir(exist_ok=True)
    for d in filter(Path.is_dir, CONT.iterdir()): print(f"{d.name:<20} {int(run(['du','-sk',str(d)],c=True,t=True).stdout.split()[0])/1024:.1f} MB")

def ctn_create(name, img):
    CONT.mkdir(exist_ok=True); dest = CONT/name
    if dest.exists(): return err("Exists")
    tar = IMG/f"{img}.tar.xz"
    if not tar.exists(): tar = IMG/f"{img.replace(':','-')}.tar.xz"
    if not tar.exists(): return err("Image not found")
    try: run(['cp', '-a', '--reflink=auto', str(get_base(img, tar, True)), str(dest)], check=True); ok()
    except Exception as e: run(['rm', '-rf', str(dest)]); err(str(e))

def ctn_delete(name):
    if not (dest := CONT/name).exists(): return err("Not found")
    if input(f"{BR}Type '{name}' to delete: {E}") != name: return
    run(['machinectl', 'terminate', name], s=True, c=True); run(['rm', '-rf', str(dest)]); ok()

def ctn_attach(name):
    if not (dest := CONT/name).exists(): return err("Not found")
    sh = next((s for s in ["/bin/bash", "/bin/sh"] if (dest/s.lstrip('/')).exists()), "/bin/sh")
    ok(); run(['systemd-nspawn', '-D', str(dest), '-M', name, '--bind-ro=/tmp/.X11-unix', '-E', f"DISPLAY={os.environ.get('DISPLAY','')}", sh], s=True)

def _get_file(f): return next((BLD/n for n in ['Box','Containerfile','Dockerfile'] if (BLD/n).exists()), BLD/"Box") if f == '.' else (BLD/f if (BLD/f).exists() else Path(f))

def _build(file):
    fpath = _get_file(file); LYR.mkdir(exist_ok=True); TMP.mkdir(exist_ok=True)
    if not fpath.exists(): err(f"'{file}' not found")
    with open(fpath) as f: lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    if not lines or not lines[0].startswith('FROM'): err("Must start with FROM")
    base = lines[0].split()[1]; tar = IMG/f"{base}.tar.xz"
    if not tar.exists(): tar = IMG/f"{base.replace(':','-')}.tar.xz"
    if not tar.exists(): err(f"Img {base} not found")
    lowers = [get_base(base, tar)]; h = hashlib.sha256(base.encode()).hexdigest()
    for i, line in enumerate(lines[1:], 1):
        blob = line + (str((fpath.parent/line.split()[1]).stat().st_mtime) if line.startswith('COPY') else "")
        h_new = hashlib.sha256((h+blob).encode()).hexdigest(); layer = LYR/h_new
        if layer.exists() and (layer/"diff").exists(): p(BS, f"Step {i} : Cache {h_new[:8]}"); lowers.insert(0, layer/"diff")
        else:
            p(BS, f"Step {i} : {line}")
            if layer.exists(): shutil.rmtree(layer)
            layer.mkdir(parents=True); (layer/"diff").mkdir(); (layer/"work").mkdir()
            mnt = TMP/f"mnt_{h_new[:8]}"; mnt.mkdir(exist_ok=True)
            try:
                mount(lowers, layer/"diff", layer/"work", mnt)
                cmd, args = (line.split(maxsplit=1)+[""])[:2]
                sh_bin = next((s for s in ["/bin/bash", "/bin/sh"] if (mnt/s.lstrip('/')).exists()), "/bin/sh")
                if cmd == 'RUN':
                    res = run(['systemd-nspawn', '-q', '-E', 'DEBIAN_FRONTEND=noninteractive', '-E', 'TERM=xterm-256color', '-D', str(mnt), sh_bin, '-c', args], s=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, t=True)
                    print(res.stdout, end=''); (layer/"log").write_text(res.stdout)
                    if res.returncode: umount(mnt); shutil.rmtree(layer); err(f"Failed: {args}")
                elif cmd == 'COPY':
                    src, dst = args.split(); s = fpath.parent/src
                    if not s.exists(): umount(mnt); shutil.rmtree(layer); err(f"No src: {src}")
                    run(['cp', '-a' if s.is_dir() else '', str(s), str(mnt/dst.lstrip('/'))], check=True)
                umount(mnt); lowers.insert(0, layer/"diff")
            except: umount(mnt); shutil.rmtree(layer); raise
            finally: mnt.exists() and mnt.rmdir()
        h = h_new
    return lowers

def _merge(lowers, dest, compress=False):
    mnt = TMP/f"mrg_{uuid.uuid4().hex[:6]}"; mnt.mkdir(exist_ok=True)
    run(['mount', '-t', 'overlay', 'overlay', '-o', f"lowerdir={':'.join(map(str, lowers))}", str(mnt)], s=True, check=True)
    try:
        if compress:
            env = os.environ.copy(); env['XZ_OPT'] = '-1 -T0'
            run(['tar', '-cJf', str(dest), '-C', str(mnt), '.'], check=True, env=env)
        else: run(['cp', '-a', f"{mnt}/.", str(dest)], check=True)
    finally: umount(mnt); mnt.rmdir()

def ctn_build(name, file):
    lowers = _build(file); dest = CONT/name
    if dest.exists(): run(['machinectl', 'terminate', name], s=True, c=True); run(['rm', '-rf', str(dest)])
    dest.mkdir(); p(BS, f"Merging '{name}'..."); _merge(lowers, dest); ok()

def ctn_imagine(name):
    if not (src := CONT/name).exists(): return err("Container not found")
    img_name = uuid.uuid4().hex[:8]; p(BS, f"Creating '{img_name}'...")
    env = os.environ.copy(); env['XZ_OPT'] = '-1 -T0'
    run(['tar', '-cJf', str(IMG/f"{img_name}.tar.xz"), '-C', str(src), '.'], check=True, env=env); ok()

def img_ls():
    IMG.mkdir(exist_ok=True)
    for f in IMG.glob('*.tar.xz'): print(f"{f.name[:-7]:<20} {f.stat().st_size/(1024*1024):.1f} MB")

def img_build(name, file):
    lowers = _build(file); dest = IMG/f"{name}.tar.xz"
    if dest.exists(): dest.unlink()
    p(BS, f"Creating '{name}'..."); _merge(lowers, dest, True); ok()

def img_delete(name):
    if not (tar := IMG/f"{name}.tar.xz").exists(): return err("Not found")
    if input(f"{BR}Type '{name}' to delete: {E}") != name: return
    tar.unlink(); (c := CACHE/name).exists() and shutil.rmtree(c); ok()

def show_help():
    banner()
    for l in ["setup | list", "ctn list|create <n> <i>|delete|attach|build <n> <f>|imagine <n>", "img list|build <n> <f>|delete <n>"]: p(BW, l)

def main():
    setup(True); a = sys.argv[1:]; cmd = a[0] if a else 'help'
    C = {'list': ctn_ls, 'create': lambda: ctn_create(a[2], a[3]), 'delete': lambda: ctn_delete(a[2]), 'attach': lambda: ctn_attach(a[2]), 'build': lambda: ctn_build(a[2], a[3]), 'imagine': lambda: ctn_imagine(a[2])}
    I = {'list': img_ls, 'build': lambda: img_build(a[2], a[3]), 'delete': lambda: img_delete(a[2])}
    if cmd == 'setup': setup()
    elif cmd == 'list': ls()
    elif cmd in ('ctn', 'container') and len(a) > 1: C.get(a[1], show_help)()
    elif cmd in ('img', 'image') and len(a) > 1: I.get(a[1], show_help)()
    else: show_help()

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: pass
