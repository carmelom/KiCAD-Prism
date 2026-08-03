# Discussion #39 reply (Remote Symbols HTTPS)

Paste into: https://github.com/krishna-swaroop/KiCAD-Prism/discussions/39#discussioncomment-17692631

---

@aidanbrzezinski You’re hitting a real gap that showed up for several reverse-proxy HTTPS deploys. Short version: behind TLS termination, Prism’s Remote Symbols metadata often advertised `http://…`, and KiCad rejects that.

**1. Diagnose which failure you have**

```bash
curl -fsS https://YOUR_HOST/.well-known/kicad-remote-provider | head
curl -fsS https://YOUR_HOST/.well-known/kicad-remote-provider | jq '{api_base_url, panel_url, auth}'
```

- Response starts with `<` / HTML → proxy is returning the SPA (`index.html`) instead of backend JSON.
- JSON with `"api_base_url": "http://…"` → origin/proto bug (your case if you’re past invalid JSON).
- JSON with all `https://YOUR_HOST/…` URLs → different issue (TLS trust / OIDC redirects).

**2. Temporary fix for HTML / invalid JSON**

Ensure these paths hit the backend (not SPA fallback), either in the bundled frontend nginx or your outer nginx:

- `/.well-known/kicad-remote-provider`
- `/remote-provider/`
- `/oauth/`
- `/api/`

Current main already proxies these in `frontend/nginx.conf`; rebuild/pull a recent image if you’re on an older build.

**3. Temporary workarounds until you upgrade**

**Option A:** set an explicit public origin and rebuild:

```env
PUBLIC_BASE_URL=https://YOUR_HOST
SESSION_COOKIE_SECURE=true
CORS_ORIGINS_STR=https://YOUR_HOST
```

(Available once the linked PR is merged; until then you can patch `_provider_origin` / `_base_url` as TriOda described.)

**Option B (proxy-only):** have the outer TLS nginx proxy `.well-known`, `/oauth`, `/remote-provider`, and `/api` **directly to backend:8000** with:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Forwarded-Host $host;
```

**4. Verify before opening KiCad**

```bash
curl -fsS https://YOUR_HOST/.well-known/kicad-remote-provider | jq '{api_base_url, panel_url, auth}'
curl -fsS https://YOUR_HOST/oauth/.well-known/oauth-authorization-server | jq '{issuer, authorization_endpoint, token_endpoint}'
```

Every advertised URL must be `https://YOUR_HOST/...`.

**Permanent fix:** Remote Symbols now uses the same public-origin resolution pattern as the comments helpers (`PUBLIC_BASE_URL` + forwarded headers), and frontend nginx no longer overwrites outer `X-Forwarded-Proto` with the internal HTTP `$scheme`. See the linked PR / `docs/HTTPS_AND_TLS.md`.
