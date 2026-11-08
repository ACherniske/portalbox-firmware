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
  from GitHub's API (`api.github.com`) by each file's git hash, over one HTTPS
  connection with the server certificate checked (`src/certs/github_roots.pem`).
  Each file is checked against its git hash before anything is installed. A
  download that drops (network error, server error) is tried once more.
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

Boxes in release mode install it within a day of their last check, at a boot or
after 10 idle minutes.

### Dev flag

While `/config/dev_mode` exists, the box has no watchdog, never updates, and does
not count crashes; after a WiFi failure it stops at the REPL instead of
restarting. Use it on a bench box:

```python
open('/config/dev_mode', 'w').close()     # on
import os; os.remove('/config/dev_mode')  # off
```

### Safe mode

Hold the box button (or the BOOT button) from power-on for about 5 seconds, until
`SAFE MODE ENABLED` prints over USB. The box skips WiFi, updates and the app,
and leaves the REPL open over USB with no watchdog. This is how to reach a box
that is not in dev mode with `mpremote`.

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

### Memory during an update

A box without PSRAM has about 100 KB of system memory for WiFi and HTTPS, and
an update's HTTPS connection uses most of it. If WiFi drops during an update,
the box cannot log in again until the update stops: the update fails (nothing
is installed), WiFi comes back by itself, and the box tries again an hour later.

Boxes built on a module with PSRAM have room to spare and log in again in the
middle of an update. Use a module with **quad** PSRAM (`R2` in the name, for
example ESP32-S3 N8R2) or none. The normal firmware build (`make
BOARD=ESP32_GENERIC_S3`) supports quad PSRAM and also runs without it.

Do not use modules with **octal** PSRAM (`R8`, for example N8R8 or N16R8):
octal PSRAM uses GPIO35-37, and the card reader's SPI bus is on GPIO36 and
GPIO37 (`config/pins.py`), so the box could never read a card.
