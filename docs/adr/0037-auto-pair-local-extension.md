# ADR-0037: Auto-Pair The Local Browser Extension

Status: accepted

Date: 2026-08-01

## Context

The Chrome extension and Python pipeline run in separate security domains. A
browser extension cannot start a local Python process, while a loopback HTTP
service must not accept arbitrary requests from websites, other extensions, or
local clients. The previous explicit bearer token preserved that boundary but
made every reviewer copy a URL and secret into the popup before first use.

Hard-coding a shared token or disabling authentication would remove the prompt
by removing the protection. Native Messaging or a hosted backend could provide
stronger installation-time identity, but each adds deployment and packaging
work beyond this source-only Beta.

## Decision

The default local bridge generates a high-entropy process-local bearer token
and never prints it. Until it is claimed, the bridge accepts one tightly bounded
`POST /v1/pair` request from a syntactically valid `chrome-extension://` Origin
using pairing protocol `1`. The request remains unavailable to normal website
Origins and the service remains bound to loopback.

The first valid extension Origin claims the process. The bridge returns the
token only to that Origin, allows the same Origin to recover it, and rejects a
different extension Origin. Once claimed, extension CORS and bearer-authenticated
requests are pinned to the claimed Origin. Origin-less local clients remain
possible only when they already possess the explicit bearer token.

The popup probes only `http://127.0.0.1:8765` by default. It first verifies a
saved credential; a missing credential or an authenticated 401/403 response
triggers pairing. The popup strictly validates the response, stores the token
in `chrome.storage.local`, verifies health, and renders **Online**. Custom ports
and explicit tokens remain an Advanced fallback.

The browser still cannot launch Python. The reviewer runs
`make extension-bridge`, then may open the popup whenever the process is ready.

## Consequences

- normal first use no longer requires copying a URL or token;
- credentials rotate on every bridge restart and are absent from logs and
  release artifacts;
- a stale saved token recovers automatically after a bridge restart;
- the unclaimed process waits for its first valid extension Origin, then remains
  first-origin-wins for its lifetime;
- an already compromised local machine or malicious installed extension with
  loopback host permission can race the bootstrap claim, which is accepted for this local Beta but
  would require Native Messaging or an installed desktop host to eliminate.

## Non-Goals

- starting a local executable from the Chrome extension;
- hosting a shared remote backend;
- weakening loopback-only binding, bearer authentication, URL safety, provider
  validation, tenant continuity, or S7;
- persisting the generated token, paired Origin, LinkedIn DOM, cookies, or
  browser storage in backend artifacts.
