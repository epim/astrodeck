# Running the AstroDeck binary

Download one file, run it, open a browser. No Python, no Node, no install.

## Get it

From the [latest release](https://github.com/epim/astrodeck/releases/latest):

| You have | Download |
|---|---|
| Windows | `astrodeck-windows-x86_64.exe` |
| Linux (Intel/AMD) | `astrodeck-linux-x86_64` |
| Linux on ARM — Raspberry Pi 4/5, 64-bit OS | `astrodeck-linux-arm64` |
| Mac (Apple silicon) | `astrodeck-macos-arm64` |

On a Pi, check you are on a 64-bit OS first — `uname -m` must say `aarch64`. A
32-bit Raspberry Pi OS reports `armv7l` and none of these will run on it.

Each has a `.sha256` beside it. Checking it takes a second and tells you the
download is intact:

```bash
shasum -a 256 -c astrodeck-linux-x86_64.sha256
```

## Run it

**Windows** — double-click it, or from a terminal:

```
astrodeck-windows-x86_64.exe
```

**Linux / Mac** — mark it executable once, then run it:

```bash
chmod +x astrodeck-linux-x86_64
./astrodeck-linux-x86_64
```

It prints where to point your browser and where it is keeping things:

```
AstroDeck 0.2.17
  open        http://localhost:8800
  settings    /home/you/.local/share/astrodeck/config
  captures    /home/you/.local/share/astrodeck/captures
  stop        Ctrl-C
```

Open that address on the same machine, or `http://<this-machine>:8800` from a
phone or tablet on the same network.

## Where your data goes

Not next to the binary — the binary might be in a Downloads folder, on a USB
stick, or somewhere read-only. It uses the normal per-user location for your OS:

| OS | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\AstroDeck` |
| macOS | `~/Library/Application Support/AstroDeck` |
| Linux | `~/.local/share/astrodeck` |

On Windows, the configuration directory must be on NTFS or ReFS. At every
startup AstroDeck verifies the filesystem and replaces inherited permissions
with a protected DACL granting full control only to the account running
AstroDeck, `SYSTEM`, and `BUILTIN\Administrators`. It also rejects junctions,
symlinks, unexpected owners, and ACL API failures before opening a listening
socket. FAT32 and exFAT are therefore suitable for exported captures, but not
for `ASTRODECK_CONFIG_DIR`.

Run a supervised Windows instance as its own standard user, not as
Administrator or LocalSystem. Create and first start the configuration while
signed in as that same identity; a directory owned by an unrelated account is
rejected rather than silently taken over.

Override either with an environment variable — useful for putting captures on an
external drive:

```bash
ASTRODECK_CAPTURE_DIR=/media/ssd/astro ./astrodeck-linux-x86_64
```

Updating is replacing the file. Your settings and captures are not inside it.

## First run is slow

The binary unpacks itself to a temporary directory the first time, so give it up
to a minute before deciding it has hung. Later starts are quick.

## The security warnings

The binaries are not code-signed yet, so:

- **Windows** shows a SmartScreen prompt. "More info" → "Run anyway".
- **macOS** refuses on the first attempt. Right-click → Open, then confirm; or
  `xattr -d com.apple.quarantine astrodeck-macos-arm64`.

Both are the OS saying "nobody has paid for a certificate for this", not that
anything is wrong with the file. Verify the SHA-256 if you want certainty.

## Other things it can do

```bash
astrodeck --port 9000              # a different port
astrodeck --host 127.0.0.1         # this machine only, not the network
astrodeck create-admin yourname    # create the first sign-in account
astrodeck --help
```

A fresh install has no sign-in method, so it binds loopback only. The CLI refuses
an unauthenticated non-loopback bind. Before serving a LAN device, run
`create-admin` and enable local auth, or set a long `ASTRODECK_TOKEN`. See
[`docs/SECURITY.md`](../SECURITY.md).

## Running it as a service

On Linux, so it starts with the machine:

```ini
# /etc/systemd/system/astrodeck.service
[Unit]
Description=AstroDeck
After=network-online.target

[Service]
EnvironmentFile=/etc/astrodeck.env
ExecStart=/opt/astrodeck/astrodeck run --host 0.0.0.0 --port 8800
User=astro
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo install -m 600 /dev/null /etc/astrodeck.env
sudoedit /etc/astrodeck.env  # add ASTRODECK_TOKEN=<long random secret>
sudo systemctl enable --now astrodeck
```

A serial-connected mount, focuser or filter wheel needs that user in the
`dialout` group: `sudo usermod -aG dialout astro`.

## Building it yourself

```bash
python packaging/build_binary.py
```

Builds the UI, installs the server, produces `dist/astrodeck`, then starts it and
checks that it serves — because a binary that builds and does not run is the
normal failure here.

**PyInstaller cannot cross-compile.** You get a binary for the OS *and*
architecture you built on, and there is no flag that changes that. In particular
an Apple-silicon Mac produces a macOS arm64 binary, not a Linux arm64 one: same
instruction set, different OS, libc and bootloader. Testing one says nothing
about the other.

### Building the Raspberry Pi binary without a Pi

On an Apple-silicon Mac, Docker runs arm64 Linux *natively* in its VM rather than
under emulation, so an arm64 Linux container builds this at full speed:

```bash
docker run --rm -v "$PWD":/src -w /src --platform linux/arm64 python:3.12-slim \
  sh -c "apt-get update -qq && apt-get install -y -qq curl binutils \
         && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
         && apt-get install -y -qq nodejs \
         && python packaging/build_binary.py"
```

`dist/astrodeck` is then a Linux arm64 binary. Copy it to the Pi and run it.

The same command works on an Intel machine but goes through QEMU, which is slow
enough (tens of minutes) that building on the Pi itself is usually the better
trade.
