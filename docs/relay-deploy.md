<a id="key-environment-variables"></a>

<a id="why-flyio--a-vps-and-why-not-cloud-run"></a>

# Deploying the AstroDeck relay

The relay is optional, owner-operated transport. AstroDeck opens an outbound WSS connection; a remote browser reaches the home application through the relay. Keep a working direct local connection while setting it up.

TLS terminates at the relay and home session cookies pass through it. Treat the relay host as a trusted authentication proxy. The home still checks each tunneled request's permissions and blocks selected configuration operations remotely.

<a id="3-point-a-scope-home-at-the-relay"></a>

<a id="what-the-relay-is-one-paragraph"></a>

## 1. Deploy the relay

1. Prepare a hostname and TLS termination for HTTPS/WSS.
2. Use the [relay deployment configuration](../deploy/reverse-proxy/docker-compose.relay.yml) with the [reverse-proxy instructions](../deploy/reverse-proxy/README.md), or the relay's [deployment reference](../relay/README.md).
3. The checked-in relay Compose build context is currently inconsistent with `relay/Dockerfile` ([#645](https://github.com/epim/astrodeck/issues/645)). Before building, use a local override that sets `build.context` to `../../relay` and `build.dockerfile` to `Dockerfile`, relative to the first Compose file in `deploy/reverse-proxy`. Keep the upstream port private to the TLS proxy. The image runs as UID/GID 10001.
4. Set `RELAY_ORIGIN` to the canonical public origin, including HTTPS and a non-default port when used. Configure `RELAY_FORWARDED_ALLOW_IPS` only for the actual immediate proxy addresses, not a wildcard.
5. Check `/healthz`. It reports liveness and the supplied build identity; an unlabelled build can report `unknown` rather than a source commit.

The tunnel registry is in memory. Use one relay instance for this deployment unless you deliberately implement shared routing. One relay origin supports one distinct home ID, avoiding cross-home cookie sharing.

<a id="2-generate-and-set-the-device-token"></a>

<a id="option-a--docker-any-host"></a>

<a id="option-b--flyio-bundled-flytoml"></a>

## 2. Provision the device token

1. Generate a high-entropy token in your secret-management workflow. The relay rejects tokens shorter than 32 printable ASCII characters.
2. Store a JSON mapping from that token to the chosen home ID privately. With the supplied proxy stack, follow its Compose-managed `RELAY_DEVICE_TOKENS_JSON` secret handoff so UID 10001 can read the mounted secret. For another launcher, set `RELAY_DEVICE_TOKENS_FILE` to a runtime-readable private file.
3. Mount the file at runtime; never bake it into the image or commit it. Use the same token and home ID on the controller.

Do not confuse the device token with a user password. It registers the controller's tunnel; browser users still sign in to AstroDeck. The reserved OIDC/viewer signing-key components are not public login or share endpoints in this build.

<a id="security-model-the-deployment-depends-on-this"></a>

## 3. Configure the controller

1. Create and test a local administrator as described in [auth setup](auth-setup.md).
2. Stop the controller and back up its private configuration directory.
3. In `astrodeck.json`, set the `remote` block's `relay_url` to `wss://relay.example.com/scope`, `home_id` to the chosen ID, `device_token` to the private token, and `enabled` to true. Preserve unrelated fields. Use your own hostname, not the example.
4. Restart and check the remote connection status. If pairing fails, compare the home ID, token configuration, TLS hostname and relay logs without printing the token.

The relay connection is opt-in. Setting `remote.enabled` false disables it while leaving local access available. It is not necessary to port-forward the controller's application port.

<a id="4-reach-it-from-a-remote-browser"></a>

## 4. Sign in remotely

1. Open `https://relay.example.com/h/home-1/`, replacing the hostname and home ID with your deployment's values.
2. Sign in with a real home account through local or configured Google authentication.
3. Confirm the expected role and test status access before issuing equipment commands.
4. Keep configuration changes that are refused over the tunnel on the direct controller connection.

The open local provider is denied remotely. The direct `ASTRODECK_TOKEN` credential is stripped at the tunnel boundary. Viewer-share-link creation and relay-terminated OIDC are not public endpoints in this build; use a named viewer account for watching.

## Updating

For Fly.io deployments, the repository's `scripts/deploy_relay.ps1` supplies version and commit build arguments and verifies the deployed health identity. For another platform, supply equivalent build identity and check it after deployment. Back up the token map privately and keep the previous image until the home tunnel and authenticated browser both work.

For end-user capabilities and privacy limits, see [remote access and roles](guide/remote-access-and-roles.md).
