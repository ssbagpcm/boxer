import sys, subprocess, shutil, tarfile, hashlib, os
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

def mount(lowers, upper, work, merge):
    run(['mount', '-t', 'overlay', 'overlay', '-o', f"lowerdir={':'.join(map(str, lowers))},upperdir={upper},workdir={work}", str(merge)], s=True, check=True)
def umount(path): run(['umount', '-l', str(path)], s=True)

def get_base(img, path, quiet=False):
    CACHE.mkdir(exist_ok=True); tpl = CACHE/img
    if not tpl.exists():
        if not quiet: p(BS, f"Caching '{img}'...")
        tpl.mkdir()
        try:
            with tarfile.open(path, 'r:xz') as t:
                r = next((x.split('/')[0] for x in t.getnames() if '/' in x), None)
            cmd = ['tar', '-xf', str(path), '-C', str(tpl), '--numeric-owner']
            if r: cmd += ['--strip-components=1']
            run(cmd, check=True)
            (tpl/"etc").mkdir(exist_ok=True)
            if not (tpl/"etc/os-release").exists(): (tpl/"etc/os-release").write_text('NAME=Linux\nID=linux\n')
        except: shutil.rmtree(tpl); raise
    return tpl

def setup_pkgs(q=False):
    pkgs = ["systemd-container", "uidmap", "wget"]
    if any(run(['dpkg', '-s', p], s=True, c=True).returncode for p in pkgs):
        if not q: p(BS, "Installing dependencies...")
        run(['apt', 'update'], s=True, c=True)
        if run(['apt', 'install', '-y'] + pkgs, s=True, c=True).returncode != 0: return False
    return True

def setup(q=False):
    try:
        if not setup_pkgs(q): return err("Package setup failed")
        for d in [IMG, CONT, BLD, CACHE, LYR, TMP]: d.mkdir(parents=True, exist_ok=True)
        if not (IMG/"alpine.tar.xz").exists():
             if run(['wget', '-q', '-O', str(IMG/"alpine.tar.xz"), "https://github.com/ssbagpcm/rootfs/releases/download/rootfs/alpine.tar.xz"]).returncode != 0:
                 return err("Download failed")
        if not (BLD/"ez").exists(): (BLD/"ez").write_text('FROM alpine\nRUN mkdir /test_folder\nRUN echo "Hello world" > /test_folder/hello.txt\nRUN apk update\nRUN apk add fastfetch\nRUN fastfetch')
        if not q: ok()
    except Exception as e: return err(str(e))

def ls():
    CONT.mkdir(exist_ok=True)
    p(BW, "CONTAINERS")
    for d in filter(Path.is_dir, CONT.iterdir()):
        s = int(run(['du','-sk',str(d)],c=True,t=True).stdout.split()[0])/1024
        print(f"  {d.name:<20} {s:.1f} MB")
    p(BW, "\nIMAGES")
    print('\n'.join(f"  {f.name[:-7]}" for f in IMG.glob('*.tar.xz')) or "  (none)\n")

def create(name, img, quiet=False):
    CONT.mkdir(exist_ok=True); dest = CONT/name
    if dest.exists(): return False if quiet else err("Exists")
    tar = IMG/f"{img}.tar.xz"
    if not tar.exists(): tar = IMG/f"{img.replace(':', '-')}.tar.xz"
    if not tar.exists(): return False if quiet else err("Image not found")
    try:
        tpl = get_base(img, tar, True)
        run(['cp', '-a', '--reflink=auto', str(tpl), str(dest)], check=True)
        if not quiet: ok()
        return True
    except Exception as e:
        if dest.exists(): run(['rm', '-rf', str(dest)])
        return False if quiet else err(str(e))

def delete(name):
    if not (dest := CONT/name).exists(): return err("Not found")
    if input(f"{BR}Type '{name}' to delete: {E}") != name: return
    run(['machinectl', 'terminate', name], s=True, c=True)
    run(['rm', '-rf', str(dest)])
    ok()

def attach(name):
    if not (dest := CONT/name).exists(): return err("Not found")
    sh = next((s for s in ["/bin/bash", "/usr/bin/bash", "/bin/sh"] if (dest/s.lstrip('/')).exists()), "/bin/sh")
    ok()
    run(['systemd-nspawn', '-D', str(dest), '-M', name, '--bind-ro=/tmp/.X11-unix', '-E', f"DISPLAY={os.environ.get('DISPLAY','')}", sh], s=True)

def build(name, file):
    fpath = BLD/file; LYR.mkdir(exist_ok=True); TMP.mkdir(exist_ok=True)
    if not fpath.exists(): fpath = Path(file)
    if not fpath.exists(): return err(f"File '{file}' not found")
    with open(fpath) as f: lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    if not lines or not lines[0].startswith('FROM'): return err("Must start with FROM")
    
    base = lines[0].split()[1]
    tar = IMG/f"{base}.tar.xz"
    if not tar.exists(): tar = IMG/f"{base.replace(':', '-')}.tar.xz"
    if not tar.exists(): return err(f"Img {base} not found")
    
    lowers = [get_base(base, tar)]
    h = hashlib.sha256(base.encode()).hexdigest()
    
    for i, line in enumerate(lines[1:], 1):
        blob = line + (str((fpath.parent/line.split()[1]).stat().st_mtime) if line.startswith('COPY') else "")
        h_new = hashlib.sha256((h+blob).encode()).hexdigest()
        layer = LYR/h_new; log = layer/"log"
        if layer.exists() and (layer/"diff").exists():
            p(BS, f"Step {i} : Cache {h_new[:8]}")
            lowers.insert(0, layer/"diff")
        else:
            p(BS, f"Step {i} : {line}")
            if layer.exists(): shutil.rmtree(layer)
            layer.mkdir(parents=True, exist_ok=True); (layer/"diff").mkdir(); (layer/"work").mkdir()
            mnt = TMP/f"mnt_{h_new[:8]}"; mnt.mkdir(exist_ok=True)
            try:
                mount(lowers, layer/"diff", layer/"work", mnt)
                cmd, args = (line.split(maxsplit=1)+[""])[:2]
                sh_bin = next((s for s in ["/bin/bash", "/usr/bin/bash", "/bin/sh"] if (mnt/s.lstrip('/')).exists()), "/bin/sh")
                if cmd == 'RUN':
                    res = run(['systemd-nspawn', '-q', '-E', 'DEBIAN_FRONTEND=noninteractive', '-E', 'TERM=xterm-256color', '-D', str(mnt), sh_bin, '-c', args], s=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, t=True)
                    print(res.stdout, end=''); log.write_text(res.stdout)
                    if res.returncode: umount(mnt); shutil.rmtree(layer); err(f"Failed: {args}")
                elif cmd == 'COPY':
                    src, dst = args.split()
                    s = fpath.parent/src
                    if not s.exists(): umount(mnt); shutil.rmtree(layer); err(f"No src: {src}")
                    run(['cp', '-a' if s.is_dir() else '', str(s), str(mnt/dst.lstrip('/'))], check=True)
                umount(mnt); lowers.insert(0, layer/"diff")
            except: umount(mnt); shutil.rmtree(layer); raise
            finally: 
                if mnt.exists(): mnt.rmdir()
        h = h_new
    
    dest = CONT/name
    if dest.exists(): run(['machinectl', 'terminate', name], s=True, c=True); run(['rm', '-rf', str(dest)])
    dest.mkdir()
    p(BS, f"Merging into {name}...")
    mnt = TMP/f"final_{name}"; mnt.mkdir(exist_ok=True)
    run(['mount', '-t', 'overlay', 'overlay', '-o', f"lowerdir={':'.join(map(str, lowers))}", str(mnt)], s=True, check=True)
    try: run(['cp', '-a', f"{mnt}/.", str(dest)], check=True)
    finally: umount(mnt); mnt.rmdir()
    ok()

def main():
    setup(True); a = sys.argv[1:]; cmd = a[0] if a else 'help'
    if cmd == 'list': ls()
    elif cmd == 'create' and len(a)>2: create(a[1], a[2])
    elif cmd == 'delete' and len(a)>1: delete(a[1])
    elif cmd == 'attach' and len(a)>1: attach(a[1])
    elif cmd == 'build' and len(a)>2: build(a[1], a[2])
    elif cmd == 'setup': setup()
    else: p(BS, "Commands: list, create <n> <i>, delete <n>, attach <n>, build <n> <f>, setup")

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: pass