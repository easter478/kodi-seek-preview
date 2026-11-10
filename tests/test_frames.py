# python tests/test_frames.py : checks the frame maker with a real ffmpeg and the Kodi side with stand-in Kodi modules.
# Needs an ffmpeg on this PC (bundled in bin/ or on PATH). Nothing here talks to a Kodi box.
import os, sys, time, types, shutil, tempfile, subprocess

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'service.seekpreview')
sys.path.insert(0, os.path.join(ROOT, 'resources', 'lib'))
import frames

ok = 0
def check(name, cond, extra=''):
    global ok
    print(('  ok   ' if cond else '  FAIL ') + name + (' ' + str(extra) if extra and not cond else ''))
    if not cond: sys.exit(1)
    ok += 1

tmp = tempfile.mkdtemp(prefix='seekpreview-test-')
try:
    ff, where = frames.find_ffmpeg(ROOT)
    check('an ffmpeg is found and runs', bool(ff), where)
    print('       using', where)
    clip = os.path.join(tmp, 'clip.mkv')
    subprocess.run([ff, '-nostdin', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1280x536:rate=24:duration=60', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '24', '-y', clip], check=True)

    g = frames.Frames(ff, clip, '', os.path.join(tmp, 'f1'), step=10, width=426, wait=1e12)  # no prefetch: only what is asked
    g.dur = 60; g.start()
    check('nothing made before it is asked for', g.nearest(30) == '')
    g.ask(34)
    end = time.time() + 20
    while not os.path.exists(g.path(30)) and time.time() < end: time.sleep(0.05)
    check('asking for 0:34 makes the 0:30 frame', os.path.exists(g.path(30)))
    from PIL import Image
    check('the frame is 426x238, cropped to 16:9 from a 2.39:1 film', Image.open(g.path(30)).size == (426, 238), Image.open(g.path(30)).size)
    check('nearest() falls back to the closest frame made', g.nearest(52) == g.path(30))
    check('no half-written files are left', not [f for f in os.listdir(g.dir) if f.endswith('.tmp')])
    g.halt(); g.join(3)
    check('the thread stops when told', not g.is_alive())

    g = frames.Frames(ff, clip, '', os.path.join(tmp, 'f2'), step=10, width=426, wait=0)
    g.dur = 60; g.around = 30; g.start()
    end = time.time() + 40
    while len([f for f in os.listdir(g.dir) if f.endswith('.jpg')]) < 6 and time.time() < end: time.sleep(0.1)
    check('prefetch makes every frame of a local film (6 of 6)', sorted(os.listdir(g.dir)) == ['%06d.jpg' % b for b in range(0, 60, 10)], os.listdir(g.dir))
    g.halt(); g.join(3)

    g = frames.Frames(ff, os.path.join(tmp, 'missing.mkv'), '', os.path.join(tmp, 'f3'), step=10, wait=0)
    g.dur = 600; g.start(); time.sleep(4)
    check('a file ffmpeg cannot read is given up on after a few tries', g.bad > 5 and not os.listdir(g.dir), g.bad)
    g.halt(); g.join(3)

    for d in ('a', 'b', 'c', 'd', 'e'):
        os.makedirs(os.path.join(tmp, 'films', d)); time.sleep(0.02)
    frames.tidy(os.path.join(tmp, 'films'), 'a')
    check('tidy keeps the 3 newest folders and the current one', sorted(os.listdir(os.path.join(tmp, 'films'))) == ['a', 'c', 'd', 'e'], os.listdir(os.path.join(tmp, 'films')))
    fake = os.path.join(tmp, 'addon')
    for k in ('linux-aarch64', 'linux-armv7', 'windows-x86_64'):
        os.makedirs(os.path.join(fake, 'bin', k)); open(os.path.join(fake, 'bin', k, 'ffmpeg'), 'w').close()
    open(os.path.join(fake, 'bin', 'ffmpeg-GPLv3.txt'), 'w').close()
    check('a system ffmpeg in use: no bundled build is removed', frames.prune(fake, os.path.join(tmp, 'elsewhere', 'ffmpeg')) == 0 and len(os.listdir(os.path.join(fake, 'bin'))) == 4)
    check('after install the builds for other devices are removed, the one in use and the licence stay',
          frames.prune(fake, os.path.join(fake, 'bin', 'linux-aarch64', 'ffmpeg')) == 2 and sorted(os.listdir(os.path.join(fake, 'bin'))) == ['ffmpeg-GPLv3.txt', 'linux-aarch64'])
    c = frames.command('ffmpeg', 'http://x/y', 'A: b\r\n', 90, 426, 238, 'o.jpg')
    check('decoder limited to one thread, keyframes only, before the input', c.index('-threads') < c.index('-i') and c.index('-skip_frame') < c.index('-i') and c[c.index('-headers') + 1] == 'A: b\r\n')

    # ---- the Kodi side, with stand-in modules: the box opens while seeking and closes after ----
    S = {'playing': True, 'seeking': False, 'seektime': '00:34', 'file': clip, 'abort': False, 'subs': True, 'subcalls': [], 'wins': []}
    class Ctl(object):
        def __init__(self, *a, **k): self.a, self.k, self.pos, self.image, self.label = a, k, a[:2], (a[4] if len(a) > 4 else ''), ''
        def setPosition(self, x, y): self.pos = (x, y)
        def setImage(self, p, cache=True): self.image = p
        def setLabel(self, t): self.label = t
    class Win(object):
        def __init__(self): self.open = False; self.controls = []; S['wins'].append(self)
        def addControls(self, c): self.controls = c
        def show(self): self.open = True
        def close(self): self.open = False
    class Player(object):
        def isPlayingVideo(self): return S['playing']
        def getPlayingFile(self): return S['file']
        def getTotalTime(self): return 60.0
        def getTime(self): return 5.0
        def showSubtitles(self, on): S['subcalls'].append(on)
    class Monitor(object):
        def abortRequested(self): return S['abort']
        def waitForAbort(self, t): time.sleep(min(t, 0.05)); return S['abort']
    class Addon(object):
        def getAddonInfo(self, k): return {'id': 'service.seekpreview', 'path': ROOT}[k]
        def getSetting(self, k): return {'prefetch': 'false', 'ram': 'false'}.get(k, '')
    xbmc = types.ModuleType('xbmc'); xbmc.LOGINFO = 1; xbmc.LOGWARNING = 2; xbmc.log = lambda *a: None
    xbmc.Player = Player; xbmc.Monitor = Monitor
    xbmc.getCondVisibility = lambda k: S['seeking'] if k == 'Player.Seeking' else S['subs']
    xbmc.getInfoLabel = lambda k: S['seektime']
    xbmc.getSkinDir = lambda: S.get('skin', 'skin.estuary')
    import json
    K = {'videoplayer.seekdelay': 750, 'videoplayer.seeksteps': [-600, -300, -180, -60, -30, -10, 10, 30, 60, 180, 300, 600]}  # Kodi's own settings
    def jsonrpc(q):
        q = json.loads(q); p = q['params']
        if q['method'] == 'Settings.SetSettingValue': K[p['setting']] = p['value']; return json.dumps({'result': True})
        return json.dumps({'result': {'value': K[p['setting']]}})
    xbmc.executeJSONRPC = jsonrpc
    gui = types.ModuleType('xbmcgui'); gui.WindowDialog = Win; gui.ControlImage = Ctl; gui.ControlLabel = Ctl
    vfs = types.ModuleType('xbmcvfs'); vfs.translatePath = lambda p: os.path.join(tmp, 'kodi-temp') if p.startswith('special://temp') else p if not p.startswith('special://') else os.path.join(tmp, 'none')
    vfs.exists = os.path.exists
    add = types.ModuleType('xbmcaddon'); add.Addon = Addon
    sys.modules.update(xbmc=xbmc, xbmcgui=gui, xbmcvfs=vfs, xbmcaddon=add)
    import importlib.util, threading
    spec = importlib.util.spec_from_file_location('service', os.path.join(ROOT, 'service.py'))
    svc = importlib.util.module_from_spec(spec); spec.loader.exec_module(svc)
    svc.frames.prune = lambda *a: 0  # the service runs from the source folder here: never delete its other builds
    # ---- Kodi's own skip settings ----
    c0 = svc.settings(); was = dict(K)
    svc.kodiseek(c0)
    check('Kodi skip settings: left alone by default', K == was and not os.path.exists(svc.KEEP))
    svc.kodiseek(dict(c0, seek=1))
    check('Kodi skip settings: Suggested sets 1000 ms and 10 s each press, and keeps the old values', K == {'videoplayer.seekdelay': 1000, 'videoplayer.seeksteps': [-10, 10]} and os.path.isfile(svc.KEEP), K)
    svc.kodiseek(dict(c0, seek=2, seekdelay=500, seeksteps=2))
    check('Kodi skip settings: Custom sets the chosen delay and steps', K == {'videoplayer.seekdelay': 500, 'videoplayer.seeksteps': [-60, -30, -10, 10, 30, 60]}, K)
    svc.kodiseek(c0)
    check('Kodi skip settings: back to "leave" puts the first values back', K == was and not os.path.exists(svc.KEEP), K)
    # ---- where the box goes ----
    check('other skins: the Custom values are used (150 from the bottom, 40 from the sides)', c0['skin'] is None and svc.spot(0.0, c0) == (40, 720 - 150 - 180 - 26) and svc.spot(1.0, c0)[0] == 1280 - 40 - 320, svc.spot(0.0, c0))
    S['skin'] = 'skin.aeon.nox.silvo'; ca = svc.settings(); S['skin'] = 'skin.estuary'
    x, y = svc.spot(0.5, ca)
    check('Aeon Nox SiLVO: the box ends 2 above the seek strip (676 of 720), centred over the seek position', y + 180 + 26 + svc.PAD == 676 - 2 and x + 160 == 640, (x, y))
    check('Aeon Nox SiLVO: at the ends of the film the box stays on screen', svc.spot(0.0, ca)[0] == 8 and svc.spot(1.0, ca)[0] == 1280 - 8 - 320)
    check('Aeon Nox SiLVO: the box lifts by 60 while the OSD buttons are open', svc.spot(0.5, ca, True)[1] == y - 60)
    check('the fine adjustment moves the box up', svc.spot(0.5, dict(ca, nudge=10))[1] == y - 10 and svc.spot(0.5, dict(c0, nudge=-10))[1] == svc.spot(0.5, c0)[1] + 10)
    th = threading.Thread(target=svc.main, daemon=True); th.start()
    time.sleep(1.0)
    check('no window while nothing is being sought', not S['wins'])
    S['seeking'] = True
    end = time.time() + 20
    while not (S['wins'] and S['wins'][-1].open) and time.time() < end: time.sleep(0.05)
    w = S['wins'][-1] if S['wins'] else None
    check('seeking opens the box with the 0:30 frame', bool(w and w.open and w.controls[1].image.endswith('000030.jpg')), w and w.controls[1].image)
    check('the box follows the seek position and stays on screen', 40 <= w.controls[1].pos[0] <= 1280 - 40 - 320 and 0 < w.controls[1].pos[1] < 720 - 180, w.controls[1].pos)
    check('subtitles are hidden while it shows', S['subcalls'] == [False])
    S['seektime'] = '00:52'; time.sleep(0.5)
    check('the time label follows', w.controls[2].label == '00:52', w.controls[2].label)
    S['seeking'] = False; time.sleep(1.2)
    check('the box closes after the seek and subtitles come back', not w.open and S['subcalls'] == [False, True], S['subcalls'])
    S['abort'] = True; th.join(5)
    check('the service stops cleanly when Kodi asks', not th.is_alive())
    print('\n%d checks passed' % ok)
finally:
    shutil.rmtree(tmp, True)
