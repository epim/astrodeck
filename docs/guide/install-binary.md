<a id="building-the-raspberry-pi-binary-without-a-pi"></a>

<a id="running-it-as-a-service"></a>

# Running the AstroDeck binary

Use the files attached to a [GitHub release](https://github.com/epim/astrodeck/releases). The release workflow targets these platforms; check that the chosen release actually contains the file you need.

| Platform | Asset name |
|---|---|
| Windows x86-64 | `astrodeck-windows-x86_64.exe` |
| Linux x86-64 | `astrodeck-linux-x86_64` |
| Linux ARM64 | `astrodeck-linux-arm64` |
| macOS Apple silicon | `astrodeck-macos-arm64` |

## Get it

1. Read the chosen release's notes and download its matching binary and `.sha256` file.
2. Calculate the binary's SHA-256 and compare it with that sidecar. On Windows use `Get-FileHash ./astrodeck-windows-x86_64.exe -Algorithm SHA256`; on Linux use `sha256sum astrodeck-linux-x86_64`.
3. Keep the download only if the values match. A matching checksum checks the bytes against the sidecar; it does not establish trust in an unknown download source.

On Linux ARM64, `uname -m` should report `aarch64`. A 32-bit ARM system cannot use that asset.

<a id="first-run-is-slow"></a>

## Run it

1. On Windows, run `./astrodeck-windows-x86_64.exe` from PowerShell as a standard user. On Linux or macOS, mark the matching file executable with `chmod +x` and run it with `./`.
2. Open `http://localhost:8800` on the same computer. The terminal prints the configuration and capture directories.
3. Follow [getting started](getting-started.md) for the simulator walkthrough.

The binary contains Python and the built browser interface. It is an executable, not an installer. It extracts bundled files at startup; startup time depends on the machine. If the operating system blocks an unfamiliar download, verify its source before changing that decision.

Native guiding and native autofocus require `astrodeck_native`, absent from published releases pending #630. ASTAP and its star database are a separate installation. Do not infer their presence from a working browser interface.

<a id="the-security-warnings"></a>

## Where your data goes

| OS | Default parent directory |
|---|---|
| Windows | `%LOCALAPPDATA%/AstroDeck` |
| macOS | `~/Library/Application Support/AstroDeck` |
| Linux | `$XDG_DATA_HOME/astrodeck`, or `~/.local/share/astrodeck` when unset |

The parent contains `config` and `captures`. Set `ASTRODECK_CONFIG_DIR` or `ASTRODECK_CAPTURE_DIR` before launch to override either directory. Keep configuration private and back up both directories before an update.

Windows configuration must be on NTFS or ReFS and owned by the account running AstroDeck. Startup checks its permissions and rejects unsafe links or ownership. Use a standard account, including for a service. See [Windows rig](windows-rig.md).

## Other things it can do

```text
astrodeck-windows-x86_64.exe --port 9000
astrodeck-windows-x86_64.exe create-admin yourname
astrodeck-windows-x86_64.exe --help
```

`create-admin` prompts for a password, enables local sign-in, and exits. Use the matching executable name on other systems. A fresh server is local-only; phone and remote access follow [remote access and roles](remote-access-and-roles.md).

## Updating

Stop AstroDeck before replacing its binary. Keep the previous executable and a configuration backup until you have confirmed the replacement starts and opens your existing configuration. Replacing a binary does not itself configure the supervised source-release updater.

## Building it yourself

From a source checkout, `python packaging/build_binary.py` builds the UI, installs build dependencies and builds a PyInstaller executable for the current OS and architecture. It performs startup smoke checks. Use a separate build environment; it is not a cross-compiler. See [development](../development.md) for source work.

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
