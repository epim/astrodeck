# Pulling sub-exposures off astrotown

For an agent or a person who needs the raw light frames from the rig for
processing. Two routes: the rig's own LAN (fastest, gives you the whole
folder) and the Fly relay (works from anywhere the rig can reach the
internet, one HTTPS session, size-limited per request).

The rig runs AstroDeck 0.3.32 on Windows as user `James`. The capture
library is `C:\Users\James\AstroDeck\captures\`. Everything under it is a
frame unless its top-level folder is one of `logs`, `sessions`, `reports`,
`exports`, `_masters`, `_solve`, `_survey`, `_survey_pack`, `_weather_tiles`,
`.trash` or the thumbnail cache; those are bookkeeping, not lights. Frames
are `.fits` files named `Light_<target>_<filter>_<date>_<time>_<n>.fits`
inside folders laid out by the naming template in the rig's config, so do
not guess folder names: list them.

The FITS headers carry the observatory's coordinates. Do not paste header
dumps, `SITELAT`/`SITELONG` values or the rig's address into anything
public (issues, chats, docs). See the project rule in `CLAUDE.md`.

## Route 1: on the rig's LAN (scp or sftp)

Works only when your machine is on the same network as the rig, which is
the `192.168.216.x` network. If your address is anything else (the
household AmpliFi network is `192.168.140.x`), you are not on it, and no
VPN fixes that: join the rig's network. The ssh alias in `~/.ssh/config` is:

```
Host nina astrotown
    HostName astrotown.lan
    User James
```

If `astrotown.lan` does not resolve, pass the address instead:

```
ssh -o HostName=192.168.216.220 astrotown
```

The shell you land in is PowerShell. To see what is there:

```
ssh astrotown 'Get-ChildItem C:\Users\James\AstroDeck\captures -Directory | Select-Object Name'
ssh astrotown 'Get-ChildItem -Recurse -File -Filter *.fits C:\Users\James\AstroDeck\captures\<folder> | Measure-Object -Property Length -Sum'
```

To pull a folder (Windows paths on the remote side use forward slashes with scp):

```
scp -r astrotown:C:/Users/James/AstroDeck/captures/<folder> ./subs/
```

`sftp astrotown` also works for browsing. Do not delete anything on the
rig from this route; the app has its own trash with restore.

## Route 2: through the relay (HTTPS, from anywhere)

Base URL: `https://astrodeck-relay.fly.dev/h/home-1` (the `h/home-1` part is
the rig's hub id at the relay). Everything below is relative to it.

1. Check the rig is connected: `GET /healthz` returns
   `{"ok":true,"version":"0.3.32"}` when the tunnel is up, an error from the
   relay when the rig is offline.
2. Log in once and keep the cookie jar. The account is the operator's; the
   person who owns the rig gives you the username and password out of band.
   Never look for them in files.

   ```
   curl -s -c jar.txt -H 'Content-Type: application/json' \
     -d '{"username":"<user>","password":"<password>"}' \
     https://astrodeck-relay.fly.dev/h/home-1/auth/local
   ```
   That mints the `ad_session` cookie. Downloading FITS needs the
   `view.media` capability, which operator and admin roles have and viewer
   does not (because of the coordinates in the headers).
3. List what exists:

   ```
   curl -s -b jar.txt 'https://astrodeck-relay.fly.dev/h/home-1/api/gallery/nights'
   curl -s -b jar.txt 'https://astrodeck-relay.fly.dev/h/home-1/api/gallery/frames?q=7331&night_from=2026-09-17&night_to=2026-09-17&limit=200&offset=0'
   ```
   `night_from`/`night_to` are noon-to-noon night keys, inclusive: the night
   of 17/18 September is `2026-09-17`. `q` matches target and filter names.
   Each row carries `path` (relative to the capture library), `night`,
   `target`, `filter`, `frame_type`, `exposure_s` and `bytes`; `total` and
   `bytes` at the top describe the whole filtered set. Page with `offset`
   until you have `total` rows.
4. Download one frame at a time using the row's `path`:

   ```
   curl -s -b jar.txt -o "<local name>.fits" \
     --data-urlencode "path=<row path>" -G \
     https://astrodeck-relay.fly.dev/h/home-1/api/gallery/file
   ```
   There is also a streamed zip of a whole filter result,
   `GET /api/gallery/download.zip?q=...&night_from=...&night_to=...`
   (repeat `path=` to pick files instead). Prefer it on the LAN
   (`http://192.168.216.220:8800` as the base, same cookie flow). Through
   the relay, expect the request to be cut off if any single response takes
   longer than about 30 s, which a multi-gigabyte zip on a home uplink
   will; per-file downloads of 30 to 60 MB frames are the safe shape there.
   Test with one frame before scripting hundreds.

## What not to do

- Do not use `POST /api/gallery/trash` or anything under `/api/capture`,
  `/api/sequence`, `/api/mount` or `/api/camera` from a processing session.
  Read-only means the gallery GET routes only.
- Do not run pulls during an imaging run over the relay; the tunnel is the
  same one the operator's phone uses.
