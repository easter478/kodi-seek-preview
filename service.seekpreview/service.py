# Seek Preview: while you skip forward or back, a small frame of the spot you are jumping to shows over the video.
# Works in any skin: the box is this add-on's own window drawn on top of the video, no skin file is touched.
# It is opened with show(), so it is not modal and every remote key still goes to the player.
# Frames come from the ffmpeg bundled in bin/ (see resources/lib/frames.py), at fixed steps, kept per film.
import os, re, sys, json, time, shutil, hashlib, threading
import xbmc, xbmcgui, xbmcvfs, xbmcaddon

ADDON = xbmcaddon.Addon()
ID = ADDON.getAddonInfo('id')
HERE = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
sys.path.insert(0, os.path.join(HERE, 'resources', 'lib'))
import frames  # noqa: E402

DISK = xbmcvfs.translatePath('special://temp/seekpreview')
RAM = '/tmp/seekpreview' if os.path.isdir('/tmp') and not frames.WIN else DISK
WHITE = os.path.join(HERE, 'resources', 'media', 'white.png')
STOP = threading.Event()  # set when Kodi stops the service: every thread checks it, so Kodi never has to kill the script
SIZES = [(240, 135), (320, 180), (426, 240)]
PAD = 4  # the dark border round the frame
# Skins the box is fitted to, in 1280x720 units. bar: top edge of the skin's seek strip; x0..x1: where its bar runs;
# gap: space left above the strip; edge: closest the box goes to the screen sides; osd/raise: lift the box while this is true.
# Aeon Nox SiLVO 11 (16x9/Includes_OSD.xml, DefaultSeekbar): strip at top 1014 of 1080, bar full width, OSD buttons add 90 more.
SKINS = {'skin.aeon.nox.silvo': {'bar': 676, 'x0': 0, 'x1': 1280, 'gap': 2, 'edge': 8, 'raise': 60,
                                 'osd': 'Window.IsActive(videoosd) + !Skin.HasSetting(VideoOSDOnTop)'}}
FITS = {2: 'skin.aeon.nox.silvo'}  # the "Fit to skin" setting: 0 automatic, 1 custom, 2.. a named skin


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s] %s' % (ID, msg), level)


def settings():
    a = xbmcaddon.Addon()  # a fresh object, so changed settings are seen
    b = lambda k, d: (a.getSetting(k) or d).lower() == 'true'
    n = lambda k, d: int(a.getSetting(k) or d) if re.match(r'^-?\d+$', a.getSetting(k) or '') else int(d)
    fit = n('fit', '0')
    try: skin = SKINS.get(xbmc.getSkinDir() if fit == 0 else FITS.get(fit, ''))
    except Exception: skin = None
    return {'seek': max(0, min(2, n('kodiseek', '0'))), 'seekdelay': max(250, min(3000, n('seekdelay', '1000') // 250 * 250)),
            'seeksteps': max(0, min(3, n('seeksteps', '0'))), 'on': b('enabled', 'true'), 'size': max(0, min(2, n('size', '1'))), 'place': n('place', '0'), 'top': b('top', 'false'),
            'skin': skin, 'margin': max(0, min(300, n('margin', '40'))), 'nudge': max(-200, min(200, n('nudge', '0'))),
            'offset': max(0, min(700, n('offset', '150'))), 'step': [5, 10, 15, 30][max(0, min(3, n('step', '1')))], 'ram': b('ram', 'true'),
            'hidesubs': b('hidesubs', 'true'), 'time': b('showtime', 'true'), 'prefetch': b('prefetch', 'true')}


# Kodi's own skip settings (Settings > Player > Videos), which decide how skipping feels. The preview only shows during
# Kodi's skip delay, and when the delay runs out between two presses Kodi really jumps and rebuffers, which is the stutter.
# Suggested: 1000 ms, and one 10 s step each way: with a single step Kodi adds it up per press (10, 20, 30 ..), an even
# walk that matches the 10 s frames. The values found in Kodi are kept in KEEP and put back when the setting is "leave".
KEEP = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/' + ID), 'kodi-seek.json')
SUGGESTED = (1000, [10])
STEPS = [[10], [30], [10, 30, 60], [10, 30, 60, 180, 300, 600]]  # the "Custom: skip steps" choices; the last is Kodi's default


def rpc(method, params):
    try: return json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}))).get('result')
    except Exception: return None


def kodiseek(c):
    get = lambda k: (rpc('Settings.GetSettingValue', {'setting': k}) or {}).get('value')
    put = lambda k, v: v is not None and get(k) != v and rpc('Settings.SetSettingValue', {'setting': k, 'value': v})
    try:
        if c['seek'] == 0:  # leave Kodi's settings alone: put back what was there before this add-on changed them
            if os.path.isfile(KEEP):
                with open(KEEP) as f: old = json.load(f)
                put('videoplayer.seekdelay', old.get('delay')); put('videoplayer.seeksteps', old.get('steps'))
                os.remove(KEEP); log('Kodi skip settings put back')
            return
        if not os.path.isfile(KEEP):
            old = {'delay': get('videoplayer.seekdelay'), 'steps': get('videoplayer.seeksteps')}
            if old['delay'] is None or old['steps'] is None:
                return
            os.makedirs(os.path.dirname(KEEP), exist_ok=True)
            with open(KEEP, 'w') as f: json.dump(old, f)
        delay, steps = SUGGESTED if c['seek'] == 1 else (c['seekdelay'], STEPS[c['seeksteps']])
        put('videoplayer.seekdelay', delay); put('videoplayer.seeksteps', sorted([-s for s in steps] + steps))
    except Exception as e:
        log('Kodi skip settings not changed: %s' % e, xbmc.LOGWARNING)


def secs(s):
    try:
        n = 0
        for part in s.strip().split(':'):
            n = n * 60 + int(part)
        return n
    except Exception:
        return -1


# ffmpeg can't open smb:// or nfs://, Kodi can: a tiny HTTP server on 127.0.0.1 serves the file through xbmcvfs
# with Range support, so ffmpeg seeks in it and no share password ever leaves Kodi.
PROXY = {'port': 0, 'files': {}, 'srv': None}


def proxy():
    if PROXY['port']:
        return PROXY['port']
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = PROXY['files'].get(self.path.strip('/'))
            if not path:
                self.send_error(404); return
            f = xbmcvfs.File(path)
            try:
                size = f.size()
                m = re.match(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
                a = int(m.group(1)) if m else 0
                b = int(m.group(2)) if m and m.group(2) else size - 1
                self.send_response(206 if m else 200)
                self.send_header('Accept-Ranges', 'bytes'); self.send_header('Content-Length', str(b - a + 1))
                if m:
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (a, b, size))
                self.end_headers()
                f.seek(a, 0); left = b - a + 1
                while left > 0 and not STOP.is_set():
                    d = f.readBytes(min(262144, left))
                    if not d:
                        break
                    self.wfile.write(d); left -= len(d)
            except Exception:
                pass
            finally:
                f.close()
    srv = ThreadingHTTPServer(('127.0.0.1', 0), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True).start()
    PROXY['port'], PROXY['srv'] = srv.server_address[1], srv
    return PROXY['port']


def source(f):
    """What is playing -> (ffmpeg input, http headers, streamed?). None when ffmpeg can't read it (discs, PVR, DRM add-ons)."""
    url, _, hdr = f.partition('|')
    if re.match(r'https?://', url):
        h = ''.join('%s: %s\r\n' % tuple(x.split('=', 1)) for x in hdr.split('&') if '=' in x)
        return url, h, True
    if os.path.isfile(url):
        return url, '', False
    if re.match(r'(smb|nfs|ftp|sftp|dav|davs|upnp|special|zip|rar)://', url) and xbmcvfs.exists(url):
        tok = hashlib.md5(url.encode('utf8', 'replace')).hexdigest()
        PROXY['files'][tok] = url
        return 'http://127.0.0.1:%d/%s' % (proxy(), tok), '', False
    return None, '', False


def spot(frac, c, raised=False):
    """Top-left corner of the frame, in 1280x720 units. With a fitted skin the box sits right on top of the skin's
    seek strip, centred over the seek position; otherwise it goes where the Look settings say."""
    w, h = SIZES[c['size']]
    th, s = 26 if c['time'] else 0, c['skin']
    if s:
        x0, x1, edge = s['x0'], s['x1'], s['edge']
        y = s['bar'] - s['gap'] - PAD - th - h - (s['raise'] if raised else 0)
    else:
        edge = c['margin']
        x0, x1 = edge + w / 2.0, 1280 - edge - w / 2.0
        y = c['offset'] if c['top'] else 720 - c['offset'] - h - th
    if c['place'] == 1: x = (1280 - w) // 2
    elif c['place'] == 2: x = edge
    elif c['place'] == 3: x = 1280 - edge - w
    else: x = min(1280 - edge - w, max(edge, int(x0 + max(0.0, min(1.0, frac)) * (x1 - x0) - w / 2.0)))  # follows the seek position
    x = max(PAD, min(1280 - w - PAD, x))
    y = max(PAD, min(720 - h - th - PAD, y - c['nudge']))
    return x, y


class Overlay(object):
    """The preview box: a background, the frame and the time, in Kodi's 1280x720 window coordinates."""

    def __init__(self):
        self.win = None
        self.shown = ''

    def show(self, img, text, frac, c, raised=False):
        w, h = SIZES[c['size']]
        pad, th = PAD, 26 if c['time'] else 0
        x, y = spot(frac, c, raised)
        if not self.win:
            self.win = xbmcgui.WindowDialog()
            self.bg = xbmcgui.ControlImage(x - pad, y - pad, w + 2 * pad, h + th + 2 * pad, WHITE, colorDiffuse='E60B0B10')
            self.img = xbmcgui.ControlImage(x, y, w, h, img, aspectRatio=0)
            self.lab = xbmcgui.ControlLabel(x, y + h, w, max(th, 1), text if c['time'] else '', font='font13', textColor='FFFFFFFF', alignment=0x2 | 0x4)
            self.win.addControls([self.bg, self.img, self.lab])
            self.win.show()
            self.shown = img
            return
        self.bg.setPosition(x - pad, y - pad); self.img.setPosition(x, y); self.lab.setPosition(x, y + h)
        if img != self.shown:
            self.img.setImage(img, False); self.shown = img
        self.lab.setLabel(text if c['time'] else '')

    def hide(self):
        if self.win:
            try: self.win.close()
            except Exception: pass
            self.win = None; self.shown = ''


class Watch(xbmc.Monitor):
    changed = True

    def onSettingsChanged(self):
        self.changed = True


def main():
    mon, player, box = Watch(), xbmc.Player(), Overlay()
    ff, where = frames.find_ffmpeg(HERE, [xbmcvfs.translatePath('special://home/addons/tools.ffmpeg-tools/bin/ffmpeg')])
    log('ffmpeg: %s' % ('%s (%s)' % (ff, where) if ff else 'none found, no previews on this device'), xbmc.LOGINFO if ff else xbmc.LOGWARNING)
    try:
        n = frames.prune(HERE, ff) if ff else 0
        if n: log('removed the ffmpeg builds for %d other device types' % n)
    except Exception:
        pass
    c, gen, cur, last, root, subs = settings(), None, None, 0, None, False
    kodiseek(c)

    def drop():
        nonlocal gen, cur
        if gen: gen.halt()
        gen, cur = None, None

    def clear():
        nonlocal subs, last
        box.hide(); last = 0
        if subs:
            subs = False
            try: player.showSubtitles(True)
            except Exception: pass

    while not mon.abortRequested():
        now = time.time()
        if mon.changed:
            mon.changed = False; c = settings(); kodiseek(c)
        want = RAM if c['ram'] else DISK
        if root and want != root:
            drop(); clear(); shutil.rmtree(root, True)
        root = want
        if not (ff and c['on'] and player.isPlayingVideo()):
            if gen:
                drop(); clear()
                if root == RAM and root != DISK: shutil.rmtree(RAM, True)
            mon.waitForAbort(1); continue
        try: f = player.getPlayingFile()
        except Exception: f = ''
        if f != cur:
            if gen: gen.halt()
            gen, cur = None, f
            clear(); c = settings()  # read again at each film: the skin may have been changed since
            url, hdr, remote = source(f)
            if url:
                key = hashlib.md5(('%d:%d:%s' % (SIZES[2][0], c['step'], f.partition('|')[0])).encode('utf8', 'replace')).hexdigest()[:16]
                gen = frames.Frames(ff, url, hdr, os.path.join(root, key), c['step'], SIZES[2][0], remote, STOP, wait=20 if c['prefetch'] else 1e12)
                frames.tidy(root, key); gen.start()
        if not gen:
            mon.waitForAbort(0.5); continue
        try: gen.dur = int(player.getTotalTime()); gen.around = int(player.getTime())
        except Exception: pass
        if xbmc.getCondVisibility('Player.Seeking'):
            label = xbmc.getInfoLabel('Player.SeekTime')
            t = secs(label)
            if t >= 0:
                gen.ask(t)
                img = gen.nearest(t)  # the asked-for frame, or the closest one made so far while it is being made
                if img:
                    if c['hidesubs'] and not subs and xbmc.getCondVisibility('VideoPlayer.SubtitlesEnabled'):
                        try: player.showSubtitles(False); subs = True
                        except Exception: pass
                    box.show(img, label, t / float(gen.dur) if gen.dur else 0.5, c, bool(c['skin'] and xbmc.getCondVisibility(c['skin']['osd'])))
                last = now
        elif last and now - last > 0.4:
            clear()
        mon.waitForAbort(0.1)
    STOP.set()
    if gen: gen.halt(); gen.join(2)
    clear()
    if PROXY['srv']:
        try: PROXY['srv'].shutdown(); PROXY['srv'].server_close()
        except Exception: pass


if __name__ == '__main__':
    main()
