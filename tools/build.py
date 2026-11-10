# python tools/build.py : makes the installable zips in dist/
#   service.seekpreview-<version>.zip        everything, with the ffmpeg builds found in service.seekpreview/bin/
#   service.seekpreview-<version>-lite.zip   no ffmpeg inside (uses one already on the device)
# Files under bin/ keep their execute bit inside the zip, so Kodi on Linux can run them straight after install.
import os, re, sys, zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SRC = os.path.join(ROOT, 'service.seekpreview')
version = re.search(r'<addon[^>]*\bversion="([^"]+)"', open(os.path.join(SRC, 'addon.xml'), encoding='utf8').read()).group(1)
os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)


def build(name, with_bin):
    out = os.path.join(ROOT, 'dist', name)
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d, dirs, files in os.walk(SRC):
            dirs[:] = sorted(x for x in dirs if x != '__pycache__')
            rel = os.path.relpath(d, SRC).replace(os.sep, '/')
            if not with_bin and (rel == 'bin' or rel.startswith('bin/')):
                continue
            for f in sorted(files):
                if f.endswith(('.pyc', '.tmp')) or f == '.gitkeep':
                    continue
                arc = 'service.seekpreview/' + ('' if rel == '.' else rel + '/') + f
                zi = zipfile.ZipInfo.from_file(os.path.join(d, f), arc)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.create_system = 3  # unix, so the mode below is kept
                zi.external_attr = (0o100755 if rel.startswith('bin/') else 0o100644) << 16
                with open(os.path.join(d, f), 'rb') as fh:
                    z.writestr(zi, fh.read(), compresslevel=9)
                n += 1
    print('%s  %d files  %.1f MB' % (out, n, os.path.getsize(out) / 1048576.0))


bins = [os.path.relpath(os.path.join(d, f), SRC) for d, _, fs in os.walk(os.path.join(SRC, 'bin')) for f in fs if f.startswith('ffmpeg')]
print('ffmpeg builds inside:', ', '.join(sorted(bins)) or 'none')
build('service.seekpreview-%s-lite.zip' % version, False)
if bins:
    build('service.seekpreview-%s.zip' % version, True)
else:
    print('no ffmpeg in bin/, so only the lite zip was made')
