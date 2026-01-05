#!/usr/bin/env python3
import sys, subprocess, shutil, tarfile, hashlib, os, uuid, json
from pathlib import Path

B = Path("/usr/local/bin/boxer")
IS_BIN = sys.argv[0].endswith("boxer")
R = Path("/var/lib/boxer") if IS_BIN else Path(__file__).resolve().parent
ENTRY = "boxer" if IS_BIN else f"python3 {Path(__file__).name}"
IMG, CONT, BLD, CACHE, TMP = R/"images", R/"containers", R/"build", R/"cache", R/"tmp"
CIMG, LYR = CACHE/"images", CACHE/"layers"
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
    CIMG.mkdir(parents=True, exist_ok=True); tpl = CIMG/img
    if not tpl.exists():
        if not quiet: p(BS, f"Caching '{img}'...")
        tpl.mkdir(parents=True)
        try:
            with tarfile.open(path, 'r:xz') as t: r = next((x.split('/')[0] for x in t.getnames() if '/' in x), None)
            cmd = ['tar', '-xf', str(path), '-C', str(tpl), '--numeric-owner'] + (['--strip-components=1'] if r else [])
            run(cmd, check=True); (tpl/"etc").mkdir(exist_ok=True)
            if not (tpl/"etc/os-release").exists(): (tpl/"etc/os-release").write_text('NAME=Linux\nID=linux\n')
        except: shutil.rmtree(tpl); raise
    return tpl

def ensure_dirs():
    if IS_BIN: run(['mkdir', '-p', str(IMG), str(CONT), str(BLD), str(CACHE), str(LYR), str(TMP), str(CIMG)], s=True)
    else:
        for d in [IMG, CONT, BLD, CACHE, LYR, TMP, CIMG]: d.mkdir(parents=True, exist_ok=True)
    if not (IMG/"alpine.tar.xz").exists():
        p(BS, "Downloading alpine..."); run(['wget', '-q', '-O', str(IMG/"alpine.tar.xz"), "https://github.com/ssbagpcm/rootfs/releases/download/rootfs/alpine.tar.xz"], s=IS_BIN)

def setup(q=False):
    a = sys.argv
    if '--install' in a:
        if run(['ln', '-sf', str(Path(__file__).resolve()), str(B)], s=True).returncode: return err("Install failed")
        run(['chmod', '+x', str(Path(__file__).resolve())], s=True); return ok()
    if '--uninstall' in a:
        if B.exists(): run(['rm', str(B)], s=True)
        if IS_BIN or Path("/var/lib/boxer").exists(): run(['rm', '-rf', "/var/lib/boxer"], s=True)
        if not IS_BIN:
            for d in [IMG, CONT, BLD, CACHE, TMP]: shutil.rmtree(d, ignore_errors=True)
        return ok()
    if '--disable-warn-binaries' in a:
        CACHE.mkdir(exist_ok=True); (CACHE/".no_warn").touch(); return ok()
    
    ensure_dirs()
    if not q:
        banner()
        if (CACHE/".setup_done").exists():
            ok()
            if B.exists() and not (CACHE/".no_warn").exists() and "boxer" not in sys.argv[0]:
                p(BS, f"Warn: '{BW}boxer{BS}' is installed. You can use '{BW}boxer <cmd>{BS}' directly.")
            return
    elif (CACHE/".setup_done").exists(): return
    
    try:
        pkgs = ["systemd-container", "uidmap", "wget"]
        if any(run(['dpkg', '-s', x], s=True, c=True).returncode for x in pkgs):
            run(['apt', 'update'], s=True, c=True); run(['apt', 'install', '-y'] + pkgs, s=True, c=True)
        run(['pip', 'install', '-q', 'pyfiglet'], c=True)
        (CACHE/".setup_done").touch()
        if not q: ok()
    except Exception as e: return err(str(e))

def ls():
    CONT.mkdir(exist_ok=True); IMG.mkdir(exist_ok=True); p(BW, "CONTAINERS")
    for d in filter(Path.is_dir, CONT.iterdir()): print(f"  {d.name:<20} {int(run(['du','-sk',str(d)],c=True,t=True).stdout.split()[0])/1024:.1f} MB")
    p(BW, "\nIMAGES")
    for f in IMG.glob('*.tar.xz'): print(f"  {f.name[:-7]}")

def ctn_ls():
    CONT.mkdir(exist_ok=True)
    for d in filter(Path.is_dir, CONT.iterdir()):
        cfg = _get_ctn_config(d.name)
        size = int(run(['du','-sk',str(d)],c=True,t=True).stdout.split()[0])/1024
        print(f"{d.name:<20} {size:.1f} MB" + (f" ({cfg['base_image']})" if cfg else ""))

def _get_ctn_config(name):
    p = CONT/name/"config.json"
    return json.loads(p.read_text()) if p.exists() else None

def _save_ctn_config(name, cfg):
    (CONT/name/"config.json").write_text(json.dumps(cfg))

def ctn_create(name, img):
    CONT.mkdir(exist_ok=True); dest = CONT/name
    if dest.exists(): return err("Exists")
    tar = IMG/f"{img}.tar.xz"
    if not tar.exists(): tar = IMG/f"{img.replace(':','-')}.tar.xz"
    if not tar.exists(): return err("Image not found")
    try:
        dest.mkdir(); (dest/"layers").mkdir(); (dest/"current").mkdir(); (dest/"current"/"diff").mkdir(); (dest/"current"/"work").mkdir(); (dest/"merged").mkdir()
        _save_ctn_config(name, {"base_image": img, "layers": []})
        ok()
    except Exception as e: run(['rm', '-rf', str(dest)]); err(str(e))

def ctn_delete(name):
    if not (dest := CONT/name).exists(): return err("Not found")
    if input(f"{BR}Type '{name}' to delete: {E}") != name: return
    umount(dest/"merged"); run(['rm', '-rf', str(dest)]); ok()

def ctn_attach(name):
    if not (dest := CONT/name).exists(): return err("Not found")
    cfg = _get_ctn_config(name)
    if not cfg: return err("Invalid config")
    tar = IMG/f"{cfg['base_image']}.tar.xz"
    if not tar.exists(): tar = IMG/f"{cfg['base_image'].replace(':','-')}.tar.xz"
    base = get_base(cfg['base_image'], tar, True)
    
    lowers = [dest/"layers"/l/"diff" for l in reversed(cfg['layers'])] + [base]
    mount(lowers, dest/"current"/"diff", dest/"current"/"work", dest/"merged")
    
    try:
        sh = next((s for s in ["/bin/bash", "/bin/sh"] if (dest/"merged"/s.lstrip('/')).exists()), "/bin/sh")
        ok(); run(['systemd-nspawn', '-q', '-D', str(dest/"merged"), '-M', name, '--bind-ro=/tmp/.X11-unix', '-E', f"DISPLAY={os.environ.get('DISPLAY','')}", sh], s=True)
    finally:
        umount(dest/"merged")

def ctn_checkpoint(name):
    if not (dest := CONT/name).exists(): return err("Not found")
    cfg = _get_ctn_config(name)
    uid = uuid.uuid4().hex[:4]
    (dest/"layers"/uid).mkdir()
    shutil.move(str(dest/"current"/"diff"), str(dest/"layers"/uid/"diff"))
    shutil.rmtree(dest/"current"/"work")
    (dest/"current"/"diff").mkdir(); (dest/"current"/"work").mkdir()
    cfg['layers'].append(uid); _save_ctn_config(name, cfg)
    p(BS, f"Checkpoint: {uid}"); ok()

def ctn_diff_list(name):
    if not (cfg := _get_ctn_config(name)): return err("Not found")
    p(BW, f"Versions for {name}:")
    print(f"  [base] {cfg['base_image']}")
    for l in cfg['layers']: print(f"  [{l}]")
    print(f"  [active]")

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
    if not (dest := CONT/name).exists(): return err("Container not found")
    cfg = _get_ctn_config(name)
    tar = IMG/f"{cfg['base_image']}.tar.xz"
    if not tar.exists(): tar = IMG/f"{cfg['base_image'].replace(':','-')}.tar.xz"
    base = get_base(cfg['base_image'], tar, True)
    
    p(BW, f"Export versions for {name}:")
    print(f"  [0] Full (base + {len(cfg['layers'])} layers + active)")
    for i, l in enumerate(cfg['layers'], 1): print(f"  [{i}] up to version {l}")
    
    try:
        c = int(input(f"{BS}Choice [0]: {E}") or 0)
        lowers = ([dest/"current"/"diff"] + [dest/"layers"/l/"diff" for l in reversed(cfg['layers'])] + [base]) if c == 0 else ([dest/"layers"/l/"diff" for l in reversed(cfg['layers'][:c])] + [base])
        img_name = input(f"{BS}New image name: {E}")
        if not img_name: return err("Name required")
        p(BS, f"Compressing '{img_name}'..."); _merge(lowers, IMG/f"{img_name}.tar.xz", True); ok()
    except Exception as e: err(str(e))

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
    p(BW, f"Usage: {ENTRY} <command> [args]\n")
    
    p(BS, "GLOBAL COMMANDS")
    print(f"  {'setup':<20} Install dependencies and initialize environment.")
    print(f"  {'list':<20} Overview of all containers and images.")
    print(f"  {'imagine <n>':<20} Interactive checkpoint selection to create a .tar.xz image.\n")
    
    p(BS, "CONTAINER MANAGEMENT (ctn)")
    print(f"  {'ctn list':<20} List containers with size and image info.")
    print(f"  {'ctn create <n> <i>':<20} Create a new container <n> from image <i>.")
    print(f"  {'ctn delete <n>':<20} Delete container <n> and its persistent data.")
    print(f"  {'ctn attach <n>':<20} Open an interactive shell inside container <n>.")
    print(f"  {'ctn checkpoint <n>':<20} Save current changes as a new persistent layer.")
    print(f"  {'ctn diff <n> list':<20} Display the version history for container <n>.\n")
    
    p(BS, "IMAGE MANAGEMENT (img)")
    print(f"  {'img list':<20} List all compressed .tar.xz images.")
    print(f"  {'img build <n> <f>':<20} Build image <n> from a Boxerfile <f>.")
    print(f"  {'img delete <n>':<20} Delete a compressed image from storage.")

def main():
    a = sys.argv[1:]; cmd = a[0] if a else 'help'
    ensure_dirs()
    try:
        if cmd == 'setup': setup()
        elif cmd == 'list': ls()
        elif cmd == 'imagine': ctn_imagine(a[1])
        elif cmd in ('ctn', 'container'):
            sc = a[1]
            if sc == 'list': ctn_ls()
            elif sc == 'create': ctn_create(a[2], a[3])
            elif sc == 'delete': ctn_delete(a[2])
            elif sc == 'attach': ctn_attach(a[2])
            elif sc == 'checkpoint': ctn_checkpoint(a[2])
            elif sc == 'diff': ctn_diff_list(a[2])
            else: show_help()
        elif cmd in ('img', 'image'):
            sc = a[1]
            if sc == 'list': img_ls()
            elif sc == 'build': img_build(a[2], a[3])
            elif sc == 'delete': img_delete(a[2])
            else: show_help()
        else: show_help()
    except IndexError: show_help()
    except Exception as e: err(str(e))

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: pass
