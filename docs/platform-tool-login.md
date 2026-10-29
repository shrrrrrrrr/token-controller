# Project tools with platform login

Version 0.11 adds an **explicit opt-in** session entry for reviewed tools.
Normal project links keep their own authentication. Configuration alone is not
evidence that a deployed tool or its model workflow is available.

An active, approved member or administrator opens `/tools/<tool-id>/` using the
existing platform web session. Missing or expired sessions redirect to the
platform login with a validated local return path. Pending and disabled accounts
cannot enter. API Keys cannot sign in to this page. Logout in the tool revokes the
same platform session. Role permissions are unchanged: visiting as administrator
does not grant a member calling identity.

The gateway connects only to an operator-configured TLS IPv4 loopback endpoint.
The tool certificate is verified against the supplied public certificate and
server name, including when a restricted SSH forward carries the connection.
There is no redirect following, environment proxy or disabled certificate check.
The browser's Cookie, Authorization and identity headers are not forwarded.

The tool verifies Ed25519 assertions bound to audience, platform user ID,
username, role, CSRF value, method, exact target, body digest and a 15-second
expiry. A nonce is consumed once in a separate tool ownership database. Old
account databases are not opened by the new handler and there is no legacy-login
fallback. The gateway refuses an upstream that does not identify the new bridge.
The signing private key stays on the gateway; the tool receives only its public
key. OpenSSL is invoked with fixed arguments, bounded time and private temporary
files; no shell, Docker socket or caller-supplied command is involved.

This is a trusted, **same-origin tool** integration, not browser isolation from
the platform. Tool scripts have the origin's privileges. Only reviewed tool code
may be mounted here; arbitrary external websites must remain ordinary links.
The tool opens as a page with a return link, rather than an iframe containing a
second sign-in screen. CSRF and the existing platform origin checks still apply
to every write.

## Operator configuration

Keep all real addresses, certificates, private keys and configuration outside the
public repository. A private gateway configuration has exactly these fields:

```json
{
  "tool_id": "case-tool",
  "upstream": "https://127.0.0.1:24443",
  "server_name": "tool.example.test",
  "certificate": "/srv/model-platform/private/tool.crt",
  "private_key": "/srv/model-platform/private/tool-signing.pem"
}
```

The configuration and signing private key must be owned by the service user,
mode `0600`, regular files with one link and no symbolic links. Certificate files
may be service- or root-owned and cannot be writable by other users. Register
the same tool ID in the private project-tool catalog. Enable with
`--tool-proxy-config <path> --cloud-api --web-accounts`; the other cloud launch
arguments remain required. Default startup does not attach a tool.

Version 0.11.1 accepts repeated `--tool-proxy-config` arguments (up to eight
distinct, registered IDs), or a private `{"tools": [ ... ]}` configuration.
The original five-field single-tool configuration stays compatible. Add
`"kind": "court-transcript"` only for the reviewed transcription backend.
This profile permits 4 MiB signed upload parts and PATCH edits, streams media
and single-byte-range responses, and does not proxy full-file `/api/tasks`
uploads. Its backend must validate assertions and task ownership. The original
SimCourt profile and every other platform endpoint retain the 256 KiB limit.
The larger Nginx limit must be scoped to the exact transcription prefix;
configuration and local tests do not mean this profile is deployed or qualified.

For slow upload parts the transcription verifier authenticates the signed
envelope and consumes its nonce at header arrival, then compares the complete
bounded body digest before invoking any application route. An already expired
envelope is rejected before receiving its body. The streaming TLS sidecar does
not run another queue or load a model. The original SimCourt verifier keeps its
existing full-body validation behavior.

On the tool host, install the reviewed `integrations/platform_identity.py` and
`integrations/simcourt_platform_auth.py`. Replace the legacy handler factory with
the new `make_handler`; retain its TLS server setup. Create an owned, private
`platform-sso/` directory alongside a private `platform-sso.json`:

```json
{
  "tool_id": "case-tool",
  "platform_origin": "https://models.example.test",
  "public_key": "/srv/case-tool/state/platform-signing-public.pem",
  "legacy_admin_read": false
}
```

Keep the backend listener and SSH remote listener loopback-only. A dedicated
tool tunnel should not expand the model tunnel key's permissions. Review actual
SSH policy, key restrictions, bindings and effective configuration before
changing any live service.

## Ownership and rollout boundary

New ownership uses platform user IDs in a separate database. Legacy account IDs
are not matched by name; existing histories are retained and remain unassigned
until an explicit ownership decision. Members cannot read or stop another
member's job. Administrative access to unassigned history defaults to disabled.

This first login slice **holds `/api/jobs` and `/api/replay` generation at 503**.
It does not forward an engine Key, create member Keys or submit inference. The
previous adapter that mapped old independent-login accounts is not suitable for
this mode. Delegated unified-queue transport and a mixed SimCourt/harness real
acceptance test must be installed before generation reopens. Model context,
concurrency and legal accuracy are not qualified by login tests.

## Verification

`tests/test_tool_sso.py` uses real Ed25519 signatures and an isolated HTTP tool
server to cover request tampering, replay, expiry, session/approval changes,
credential-header stripping, ownership and the generation hold. The gateway
proxy tests use synthetic data and a mocked transport, not a production tunnel.
`node --test tests/test_tool_return.mjs` checks safe login return paths. Browser
appearance, real TLS forwarding, real server deployment and workflow throughput
need separate verification before release claims.
