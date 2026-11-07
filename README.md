# portalbox-firmware

MicroPython firmware for PortalBox access-control boxes (ESP32-S3). It runs on a
MicroPython fork with WPA2-Enterprise WiFi support; see [BUILD_GUIDE.md](BUILD_GUIDE.md)
to build and flash it. The code that goes on the box is in `src/`.

## Updates over WiFi (OTA)

A box can update its own code from this GitHub repository.

### How it works

- **When it checks:** at boot, and while the box is idle (no card session for 10
  minutes), every 60 seconds. After a successful check it waits a day; after a
  failed one, an hour. Putting a card in stops a download.
- **What it downloads:** only the files in `src/` that differ from the box,
  over HTTPS with the server certificate checked (`src/certs/github_roots.pem`).
  Each file is checked against its git hash before anything is installed.
- **Trial:** the box restarts into the new version. After 60 seconds of the app
  running, the version is kept. If it fails to get there in 3 boots, the box
  goes back to the old version and never installs that one again (unless every
  failed boot was only a WiFi failure).
- **Power cuts:** every step of an install is written down first, so a power
  cut at any point is undone at the next boot.
- **Never replaced:** `boot.py`, `main.py`, `recovery.py` (they run the
  recovery), and the box's own files: `config/`, `ca-cert.pem`, `/ota`, `/log`.

### Turn it on

At the box's REPL:

```python
from net import ota
ota.create_config(user='ACherniske', repository='portalbox-firmware',
                  token='<token>', enabled=True, mode='release')
```

- `mode='release'` follows the latest GitHub release. `mode='branch'` with
  `branch='<name>'` follows the newest commit on a branch (for testing).
- `token` is needed while the repository is private: a fine-grained GitHub
  token with read-only access to this repository's contents. It is stored only
  on the box, in `/config/ota_config.json`. Never commit it.
- To turn updates off, run `create_config` again with `enabled=False`.

### Release a new version

```bash
gh release create vX.Y.Z --target main --generate-notes
```

Boxes in release mode install it at their next boot, or within a day.

### Dev flag

While `/config/dev_mode` exists, the box has no watchdog, never updates, and does
not count crashes; after a WiFi failure it stops at the REPL instead of
restarting. Use it on a bench box:

```python
open('/config/dev_mode', 'w').close()     # on
import os; os.remove('/config/dev_mode')  # off
```

### Safe mode

Hold the box button (or the BOOT button) for 3 seconds while powering on. The
box skips WiFi, updates and the app, and leaves the REPL open over USB with no
watchdog. This is how to reach a box that is not in dev mode with `mpremote`.

### What the box did: `/log/events.log`

The box writes one line per event: install, commit, rollback (`bad=True` when
the version is refused for good), `interrupted install undone`, each update
check's result, crashes, WiFi failures, and `wifi connect, ram FREE/LARGEST`
(free system memory before the WiFi login). The file keeps 16 KB; the older
part moves to `/log/events.log.1`. To read it (dev or safe mode):

```bash
mpremote connect COM16 exec "print(open('/log/events.log').read())"
```

### A box flashed by hand

After copying `src/` to a box by cable, clear what it remembers about updates
(version, refused versions, trial, staged files). The live files stay as they are:

```python
import recovery; recovery.clear_state()
```

### Changes that need a cable

An update that changes `boot.py`, `main.py` or `recovery.py` is refused, and the
log says `anchor changed, needs cable`. These files undo bad updates, so a
broken copy could leave a box that nothing can fix over WiFi. Copy them by cable
in safe mode. Files in `config/` are per box and are set by the flash tool.

### Known issue: downloads often fail (November 2026)

GitHub's file server (`raw.githubusercontent.com`) now uses a larger Let's Encrypt
certificate chain, and the box often runs short of memory checking it. The log
then shows `failed: ValueError` with "The certificate is not correctly signed by
the trusted CA", or `MBEDTLS_ERR_X509_ALLOC_FAILED`. The box tries again every
hour; the first download after a boot usually works, but an update with many
files may not get through. A fix (freeing memory for TLS) is being worked on.
