#!/usr/bin/env python3
"""Prepare a separate Electron bundle with the cookie encryption fuse enabled."""
import pathlib
import subprocess
import sys

source, destination = map(pathlib.Path, sys.argv[1:3])
if destination.exists():
    raise SystemExit('destination already exists')
subprocess.run(['/usr/bin/ditto', str(source), str(destination)], check=True)
framework = destination / 'Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework'
marker = b'dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX'
data = framework.read_bytes()
pos = data.find(marker)
if pos < 0 or data.find(marker, pos + 1) >= 0:
    raise SystemExit('unexpected fuse sentinel count')
wire = pos + len(marker)
if data[wire] != 1 or data[wire + 1] < 2 or data[wire + 3] != ord('0'):
    raise SystemExit('unexpected fuse version or cookie encryption state')
with framework.open('r+b') as f:
    f.seek(wire + 3)
    f.write(b'1')
subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(destination)], check=True)
subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)], check=True)
data = framework.read_bytes()
if data[wire + 3] != ord('1'):
    raise SystemExit('fuse verification failed')
print('isolated Electron bundle: cookieEncryption enabled and signature verified')
