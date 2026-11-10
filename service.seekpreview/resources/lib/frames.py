# Frame making for Seek Preview. No Kodi imports here, so this file also runs in tests/ on a PC.
# One ffmpeg run per frame: seek to the nearest keyframe, decode that one picture, scale it, save a small JPEG.
import os, sys, time, shutil, platform, threading, subprocess

WIN = sys.platform.startswith('win')
NO_WINDOW = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW: no console flashing up on Windows


def platform_keys():
    """Folder names under bin/ that can run on this machine, best first."""
    m = platform.machine().lower()
    if WIN:
        return ['windows-x86_64'] if m in ('amd64', 'x86_64') else []
    if sys.platform.startswith('linux'):
        if m in ('aarch64', 'arm64'):
            return ['linux-aarch64', 'linux-armv7']  # a 64-bit kernel usually runs 32-bit ARM programs too
        if m.startswith('arm'):
            return ['linux-armv7']
        if m in ('x86_64', 'amd64'):
            return ['linux-x86_64']
    return []


def runs(path):
    """True when this ffmpeg actually starts here (right CPU, allowed to execute)."""
    try:
        if not WIN and not os.access(path, os.X_OK):
            os.chmod(path, 0o755)  # a zip install drops the execute bit
        r = subprocess.run([path, '-hide_banner', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, creationflags=NO_WINDOW)
        return r.returncode == 0 and b'ffmpeg' in r.stdout[:200].lower()
    except Exception:
        return False


def find_ffmpeg(addon_dir, extra=()):
    """The ffmpeg to use: the one bundled in the add-on, else one already on the system. Returns (path, where)."""
    exe = 'ffmpeg.exe' if WIN else 'ffmpeg'
    for key in platform_keys():
        p = os.path.join(addon_dir, 'bin', key, exe)
        if os.path.isfile(p) and runs(p):
            return p, 'bundled ' + key
    for p in list(extra) + [shutil.which('ffmpeg') or '']:
        if p and os.path.isfile(p) and runs(p):
            return p, 'system'
    return '', ''


def prune(addon_dir, ffmpeg):
    """After install: drop the bundled builds for other kinds of device (about 200 MB), keep the one in use."""
    root = os.path.join(addon_dir, 'bin')
    keep = os.path.basename(os.path.dirname(ffmpeg))
    if os.path.dirname(os.path.dirname(os.path.abspath(ffmpeg))) != os.path.abspath(root):
        return 0  # not running a bundled build: leave everything, a later start may need one
    n = 0
    for d in os.listdir(root):
        p = os.path.join(root, d)
        if os.path.isdir(p) and d != keep:
            shutil.rmtree(p, True); n += 1
    return n


def memfree():
    """Bytes of memory the system can still give out (Linux); huge where it can't be read."""
    try:
        with open('/proc/meminfo') as f:
            return next(int(l.split()[1]) * 1024 for l in f if l.startswith('MemAvailable:'))
    except Exception:
        return 1 << 40


def command(ffmpeg, url, headers, t, width, height, out):
    cmd = []
    if not WIN:
        # low priority, and the first thing the kernel kills if memory runs out (never the player)
        cmd = ['sh', '-c', 'echo 1000 > /proc/self/oom_score_adj 2>/dev/null; exec "$@"', 'ff', 'nice', '-n', '15']
    cmd += [ffmpeg, '-nostdin', '-loglevel', 'error']
    if headers:
        cmd += ['-headers', headers]
    # -threads before -i limits the decoder: frame-threaded 4K HEVC holds a set of frames per core (hundreds of MB)
    cmd += ['-threads', '1', '-skip_frame', 'nokey', '-ss', str(int(t)), '-noaccurate_seek', '-i', url, '-map', '0:v:0', '-frames:v', '1', '-an', '-sn', '-dn',
            '-threads', '1', '-vf', 'scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d' % (width, height, width, height), '-q:v', '5', '-f', 'mjpeg', '-y', out]
    return cmd


class Frames(threading.Thread):
    """Makes the frame that was asked for first, then the ones around where the film is."""

    def __init__(self, ffmpeg, url, headers, folder, step=10, width=384, remote=False, stop=None, low_mem=450 << 20, wait=20):
        threading.Thread.__init__(self)
        self.daemon = True
        self.ffmpeg, self.url, self.headers, self.dir = ffmpeg, url, headers, folder
        self.step, self.width, self.height = max(2, int(step)), int(width), int(width) * 9 // 16 // 2 * 2
        self.remote, self.low_mem, self.wait = remote, low_mem, wait
        self.want, self.around, self.dur, self.stop, self.bad = None, 0, 0, False, 0
        self.all_stop = stop or threading.Event()
        self.ev = threading.Event()
        self.born = time.time()
        os.makedirs(folder, exist_ok=True)

    def path(self, b):
        return os.path.join(self.dir, '%06d.jpg' % b)

    def bucket(self, t):
        return int(t) // self.step * self.step

    def nearest(self, t, reach=900):
        """The frame for t, or the closest one made so far."""
        b = self.bucket(t)
        for d in range(0, reach, self.step):
            for x in (b - d, b + d):
                if x >= 0 and os.path.exists(self.path(x)):
                    return self.path(x)
        return ''

    def ask(self, t):
        b = self.bucket(t)
        if not os.path.exists(self.path(b)):
            self.want = b
            self.ev.set()

    def halt(self):
        self.stop = True
        self.ev.set()

    def make(self, b):
        p = self.path(b)
        if os.path.exists(p) or self.bad > 5:
            return
        if memfree() < self.low_mem:  # never let a preview cost the player its memory
            self.ev.wait(3); self.ev.clear(); return
        tmp = p + '.tmp'
        try:
            pr = subprocess.Popen(command(self.ffmpeg, self.url, self.headers, b, self.width, self.height, tmp),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            end = time.time() + 20
            while pr.poll() is None:
                if self.stop or self.all_stop.is_set() or time.time() > end:
                    pr.kill(); pr.wait(); break
                time.sleep(0.05)
            if pr.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, p); self.bad = 0
            else:
                self.bad += 1
        except Exception:
            self.bad += 1
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception:
            pass

    def run(self):
        while not self.stop and not self.all_stop.is_set():
            if self.want is not None:
                b, self.want = self.want, None
                self.make(b)
                continue
            # the player allocates most while a film starts (4K decoder, display mode change): prefetch waits
            if time.time() - self.born < self.wait:
                self.ev.wait(1); self.ev.clear(); continue
            # outward from where the film is: the whole file when local, 10 minutes each way when streamed
            span = 600 if self.remote else max(self.dur, 600)
            todo = None
            for d in range(0, int(span) + self.step, self.step):
                for b in (self.around + d, self.around - d):
                    b = self.bucket(b)
                    if 0 <= b and (not self.dur or b < self.dur) and not os.path.exists(self.path(b)):
                        todo = b; break
                if todo is not None:
                    break
            if todo is None or self.bad > 5:
                self.ev.wait(2); self.ev.clear(); continue
            self.make(todo)


def tidy(root, keep, most=3):
    """Keep the frame folders of the last few films only."""
    try:
        ds = sorted((os.path.join(root, d) for d in os.listdir(root)), key=os.path.getmtime, reverse=True)
        for d in ds[most:]:
            if os.path.basename(d) != keep:
                shutil.rmtree(d, True)
    except Exception:
        pass
