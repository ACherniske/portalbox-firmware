# portalbox-firmware

MicroPython firmware for PortalBox access-control boxes (ESP32-S3). It runs on a
MicroPython fork with WPA2-Enterprise WiFi support; see [BUILD_GUIDE.md](BUILD_GUIDE.md)
to build and flash it. The code that goes on the box is in `src/`.

## Maker Portal server

The box asks the Maker Portal server before it powers the tool. Each card tap
is a server call. If the server can't be reached or says no, the tool stays off.

### Point the box at the server

Copy `config/example_api_config.json` to `/config/api_config.json` on the box
and put in the server's address:

```json
{
    "url": "https://makerportal.example.edu"
}
```

The address lives only on the box, never in this repository. Without the file,
the box refuses every card. `"url": ""` uses built-in test cards instead of a
server; use it only on a bench.

The box checks the server's certificate against the roots in
`/certs/github_roots.pem`: ISRG Root X1 (Let's Encrypt) and USERTrust ECC. A
server whose certificate chains to any other root fails every TLS handshake,
and the box refuses every card. Check the server's chain before you deploy.

### Set up the box on the portal

1. **Find the box's MAC address.** It's 12 lowercase hex digits, no colons
   (for example `a1b2c3d4e5f6`):
   ```bash
   mpremote connect COM16 exec "from core import api; print(api.mac())"
   ```
2. **Add the box as equipment** on the portal with that MAC address, choose its
   equipment type, and mark it **in service**.
3. **Authorize users** for that equipment type. The portal stores a card as the
   decimal number of its UID: UID `23BE9603` is card `599692803`.
4. **Proxy cards:** turn on **allow proxy** for the equipment type. Without it, a
   proxy card tapped during a user's grace period is refused, the user's session
   ends, and the box turns red.
5. **Training:** a trainer is a user whose role may create equipment
   authorizations. A trainer's card, then a trainee's card in the grace period,
   starts a training session.

### Startup report

After WiFi connects, the box tells the server it has started and prints the
answer, for example `[APP] Server startup report: ok`. The same line goes to
`/log/events.log` as `server startup: ok`.

| Answer | Meaning |
|---|---|
| `ok` | The server knows the box and it's in service. |
| `not registered` | No equipment on the portal has this box's MAC address. |
| `out of service` | The equipment is marked out of service. |
| `error` | No WiFi, no `/config/api_config.json`, or the server didn't answer. |

The box keeps running whatever the answer, but it can't power the tool until
the server accepts its cards.

### Known server limit

A trainer's card followed by a card whose user is already authorized starts a
**training** session, not a hand-off to that user. The server answers
"training" whenever the first user may train, without checking whether the
second user is already authorized. Fixing this needs a server change: refuse
training when the trainee is already authorized.

## Load a box by cable

The box runs precompiled modules (`.mpy`). Compiling `.py` files at boot takes
memory that the first HTTPS connection then cannot get. `scripts/deploy.py`
builds the modules and copies them over USB:

```bash
pip install mpremote pyserial
export MPY_CROSS="wsl -e /home/<you>/micropython/mpy-cross/build/mpy-cross"
py -3 scripts/deploy.py COM16            # --tests also copies tests/
```

- `MPY_CROSS` is the mpy-cross from BUILD_GUIDE.md Step 4, built from the same
  MicroPython as the firmware (it must write `.mpy` v6.3). On Windows, run a
  Linux build through WSL as shown.
- It resets the box, stops it at the REPL before the app starts, compares every
  file by its git hash, and copies only what differs.
- It deletes each `X.py` that an `X.mpy` replaces (MicroPython loads the `.py`
  first) and the old `manifest.json`.
- It never overwrites the box's own `config/` files. `config/pins.py` and
  `config/settings.py` are copied only to a box that has none. `boot.py`,
  `main.py` and `recovery.py` stay `.py`.
- Reset the box afterwards to boot it.

## Updates over WiFi (OTA)

A box can update its own code from this GitHub repository.

### How it works

- **When it checks:** at boot, and while the box is idle (no card session for 10
  minutes), every 60 seconds. After a successful check it waits a day; after a
  failed one, an hour. Putting a card in stops a download.
- **What it downloads:** the latest GitHub release (never a draft, a
  prerelease, or a branch) and its `portalbox.bundle` file: the precompiled
  modules plus a list of every file's size and git hash. The box reads the list
  first and writes only the files that differ from its own to flash, each
  checked against its hash before anything is installed. It deletes each old
  `X.py` that an `X.mpy` replaces. The server certificate is checked
  (`src/certs/github_roots.pem`), and the token goes only to `api.github.com`,
  never to GitHub's download host. A download that drops (network error,
  server error) starts over once. A bundle built for another MicroPython
  (`.mpy` version) is refused.
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
                  token='<token>', enabled=True)
```

- The box follows the latest release of that repository.
- `token` is needed while the repository is private: a fine-grained GitHub
  token with read-only access to this repository's contents. It is stored only
  on the box, in `/config/ota_config.json`. Never commit it.
- To turn updates off, run `create_config` again with `enabled=False`.

### Release a new version

```bash
git switch main && git pull
py -3 scripts/release.py vX.Y.Z            # --target <branch> for another branch
```

It refuses a working tree that is not clean or not the pushed tip of the
target, builds the bundle (needs `MPY_CROSS`, see "Load a box by cable"), and
creates the release with `gh`, the bundle attached. The new release is the
latest one: boxes with OTA on install it within a day of their last check, at
a boot or after 10 idle minutes. A release made by hand without the bundle is
refused by the boxes (`no bundle`).

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

After loading a box by cable, clear what it remembers about updates
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
