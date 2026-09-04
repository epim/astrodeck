# AstroDeck TLS reverse-proxy deployments

These are the supported network-facing home and relay stacks. Inbound traffic
reaches the Python services only through a dedicated internal Docker network;
nginx is the sole published listener. The home controller alone also has a
project-private egress bridge for outbound integrations. Run rootless Docker
where available and keep the host firewall closed to upstream ports 8800 and
8080.

## Home controller

Create or mount a certificate and private key as `fullchain.pem` and
`privkey.pem` in a directory readable by nginx UID/GID 101 *after the container
runtime's user-namespace mapping*. Use a narrowly scoped host ACL or dedicated
owner; never make the private key world-readable to work around a bind-mount
permission error. Use an ACME client or a locally trusted CA appropriate to the
permanent hostname.

Set `ASTRODECK_PUBLIC_HOST` to one DNS hostname (punycode for an international
name) or IPv4 address, set `ASTRODECK_CERT_DIR`, and set
`ASTRODECK_HTTPS_PORT` if the external HTTPS port is not the default 8443. The
HTTP redirect includes that explicit port. The entrypoint rejects an empty,
wildcard, multi-host, overlong, directive-like, or invalid port value before
template expansion. On a new named config volume, create the first named
administrator before starting the listener:

Compose passes the selected value into the template as
`ASTRODECK_PUBLIC_HTTPS_PORT` (and the relay uses
`RELAY_PUBLIC_HTTPS_PORT`); each must be an integer from 1 to 65535.

```sh
docker compose -f deploy/reverse-proxy/docker-compose.yml run --rm astrodeck \
  python -m astrodeck create-admin admin
docker compose -f deploy/reverse-proxy/docker-compose.yml up -d
```

The production service sets `ASTRODECK_REQUIRE_AUTH=true`, so it refuses to
listen unless a real authentication method remains configured. Do not use
`ASTRODECK_TOKEN` as a browser bootstrap. The supported SPA uses the named
account's secure session cookie. Keep port 8800 blocked by the firewall; only
the selected nginx HTTP/HTTPS ports should accept traffic.

The controller's `egress` bridge is attached to no other service and publishes
no port. It is required for remote WSS, updates, weather/maps, OAuth, alerts,
and explicitly addressed network devices. Docker bridge networking does not
carry LAN broadcast discovery reliably; configure a device address or use the
reviewed bare-metal service when discovery is required. Do not attach nginx or
unrelated containers to this bridge. On fixed installations, use the host's
container-forwarding firewall to deny the Docker daemon/host control plane and
allow only the required relay, update, weather, alert, and device destinations;
then exercise every enabled integration after applying that policy.

## Relay

Generate the device-token map directly into a mode-0600 file outside the
repository; do not print the token or put it in shell history. For example, on
Linux, with a new destination path:

```sh
umask 077
python -c 'import json,secrets,sys; f=open(sys.argv[1], "x", encoding="utf-8"); json.dump({secrets.token_urlsafe(32): sys.argv[2]}, f); f.close()' /secure/astrodeck/device_tokens.json home-1
```

Docker Compose implements file-backed secrets as bind mounts and silently
ignores their requested UID/GID/mode. That would make a host-owned 0600 file
unreadable by relay UID 10001. This stack instead uses the Compose-managed
`RELAY_DEVICE_TOKENS_JSON` secret source, mounted only at
`/run/secrets/device_tokens` as UID/GID 10001 with mode 0400. It is not added to
the relay service environment. Load it only around create/recreate operations,
without tracing the shell, and clear it even if Compose fails:

```sh
relay_tokens_file=/secure/astrodeck/device_tokens.json
test "$(stat -c %a -- "$relay_tokens_file")" = 600 || exit 1
RELAY_DEVICE_TOKENS_JSON="$(cat -- "$relay_tokens_file")" || exit 1
export RELAY_DEVICE_TOKENS_JSON
trap 'unset RELAY_DEVICE_TOKENS_JSON' EXIT HUP INT TERM
docker compose -f deploy/reverse-proxy/docker-compose.relay.yml config >/dev/null
docker compose -f deploy/reverse-proxy/docker-compose.relay.yml up -d
unset RELAY_DEVICE_TOKENS_JSON
trap - EXIT HUP INT TERM
```

The equivalent Windows PowerShell pattern is:

```powershell
$relayTokensFile = 'C:\secure\astrodeck\device_tokens.json'
$env:RELAY_DEVICE_TOKENS_JSON = Get-Content -Raw -LiteralPath $relayTokensFile
try {
    docker compose -f deploy/reverse-proxy/docker-compose.relay.yml config | Out-Null
    docker compose -f deploy/reverse-proxy/docker-compose.relay.yml up -d
} finally {
    Remove-Item Env:RELAY_DEVICE_TOKENS_JSON -ErrorAction SilentlyContinue
}
```

Restrict that Windows file's DACL to the Docker operator, SYSTEM, and
Administrators. Never run `docker compose config --environment`, shell tracing,
or an environment-dump command while the value is loaded. Re-load it whenever
Compose must create or recreate the relay container. Set `RELAY_PUBLIC_HOST`,
`RELAY_CERT_DIR`, and `RELAY_HTTPS_PORT` when the external port is not 8443.
The same validated port drives both the HTTP redirect and relay Origin checks.
Use one relay hostname/instance per home. The relay terminates TLS and can see
the home-signed session cookie, so it is trusted bearer-token infrastructure,
not an end-to-end encrypted blind forwarder.

## Host and rootless prerequisites

Rootless Docker may require subordinate UID/GID ranges, unprivileged published
ports, and source-IP preservation configuration appropriate to the host. Verify
that nginx receives the real source IP before relying on per-IP limits. Never
trust wildcard forwarded addresses. If rootless networking cannot preserve the
source IP, use a host nginx bound to 127.0.0.1/::1 upstreams and configure only
those exact proxy addresses.

Before first start, prove the mapped nginx identity can read both certificate
files. This check runs as the configured UID 101 and does not print key data:

```sh
docker compose -f deploy/reverse-proxy/docker-compose.yml run --rm --no-deps \
  --entrypoint /bin/sh proxy -c 'test -r /certs/fullchain.pem && test -r /certs/privkey.pem'
```

Repeat for `docker-compose.relay.yml` while its Compose secret value is loaded.
If the check fails, fix the host owner/ACL or rootless subordinate-ID mapping;
do not broaden the key to group/world read.

The Compose files use named writable volumes, read-only roots, no capabilities,
`no-new-privileges`, bounded tmpfs/PIDs/logs, and no host namespaces or Docker
socket. Do not add privileged device access to either production stack.

The nginx image is pinned to a reviewed multi-architecture index digest as well
as an exact stable version. Updating nginx means selecting a supported release,
reviewing its vulnerability and provenance reports, replacing both the tag and
digest together, and rerunning the full HTTPS/WSS gate on amd64 and arm64.

The certificate and key are individual read-only bind mounts. ACME clients
normally replace a file or symlink atomically, and an already-running container
may remain pinned to the old inode. Therefore certificate renewal requires a
proxy-container recreate, not merely `nginx -s reload`:

```sh
docker compose -f deploy/reverse-proxy/docker-compose.yml config
docker compose -f deploy/reverse-proxy/docker-compose.yml up -d --no-deps --force-recreate proxy
docker compose -f deploy/reverse-proxy/docker-compose.yml exec -T proxy nginx -T >/dev/null
```

The proxy entrypoint validates the newly mounted files with `nginx -t` before
listening; a bad renewal fails closed. Repeat with `docker-compose.relay.yml`
while its secret is loaded. Test HTTPS and WSS, mismatched SNI/Host, request
limits, source-IP preservation, log redaction, certificate renewal,
application restart recovery, and firewall denial of direct 8800/8080 access.
