from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import settings
from app.core.session import create_session_token, set_session_cookie
from app.core.security import get_current_user
from app.services import provider_auth_service
from app.services.public_url_service import resolve_public_base_url

router = APIRouter()


def _base_url(request: Request) -> str:
    return resolve_public_base_url(request)


def _require_provider_auth() -> None:
    if not provider_auth_service.provider_auth_enabled():
        raise HTTPException(status_code=404, detail="Provider auth disabled")


def _oauth_metadata_payload(request: Request):
    _require_provider_auth()
    return provider_auth_service.build_oauth_metadata(_base_url(request))


@router.get("/oauth/.well-known/oauth-authorization-server", include_in_schema=False)
async def oauth_authorization_server_metadata(request: Request):
    return _oauth_metadata_payload(request)


@router.get("/oauth/.well-known/openid-configuration", include_in_schema=False)
async def openid_configuration(request: Request):
    return _oauth_metadata_payload(request)


@router.get("/oauth/authorize", include_in_schema=False)
async def authorize(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query(...),
    state: str = Query(...),
    scope: str = Query(default=""),
    nonce: str = Query(default=""),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(...),
):
    provider_auth_service.validate_authorization_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=response_type,
        state=state,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )

    try:
        user = await get_current_user(request)
    except HTTPException:
        login_url = provider_auth_service.build_oidc_login_url(_base_url(request), str(request.url))
        return RedirectResponse(login_url, status_code=302)

    code = provider_auth_service.issue_authorization_code(
        user=user,  # type: ignore[arg-type]
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        nonce=nonce,
        code_challenge=code_challenge,
    )

    redirect_target = f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(redirect_target, status_code=302)


@router.get("/oauth/oidc/callback", include_in_schema=False)
async def oidc_callback(request: Request, code: str = Query(...), state: str = Query(...)):
    _require_provider_auth()
    user, return_to = provider_auth_service.resolve_oidc_callback(code, state, _base_url(request))
    token = create_session_token(
        email=user.email,
        name=user.name,
        picture=user.picture,
        role=user.role,
    )
    response = RedirectResponse(return_to, status_code=302)
    set_session_cookie(response, token)
    return response


@router.post("/oauth/token", include_in_schema=False)
async def token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    code: str = Form(default=""),
    redirect_uri: str = Form(default=""),
    code_verifier: str = Form(default=""),
    refresh_token: str = Form(default=""),
):
    _require_provider_auth()
    if grant_type == "authorization_code":
        payload = provider_auth_service.exchange_authorization_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
        return JSONResponse(payload)

    if grant_type == "refresh_token":
        payload = provider_auth_service.refresh_access_token(
            refresh_token=refresh_token,
            client_id=client_id,
        )
        return JSONResponse(payload)

    raise HTTPException(status_code=400, detail="Unsupported grant_type")


@router.post("/oauth/revoke", include_in_schema=False)
async def revoke(token: str = Form(...), client_id: str = Form(...)):
    _require_provider_auth()
    if client_id != provider_auth_service.provider_client_id():
        raise HTTPException(status_code=400, detail="Unknown client_id")
    provider_auth_service.revoke_token(token)
    return JSONResponse({"revoked": True})


@router.post("/oauth/session/bootstrap", include_in_schema=False)
async def session_bootstrap(request: Request):
    _require_provider_auth()
    body = await request.json()
    access_token = str(body.get("access_token") or "")
    next_url = str(body.get("next_url") or "")
    base_url = _base_url(request)

    if not next_url.startswith(f"{base_url}/"):
        raise HTTPException(status_code=400, detail="next_url must stay on the provider origin")

    nonce_url = provider_auth_service.build_bootstrap_nonce_url(base_url, access_token, next_url)
    return JSONResponse({"nonce_url": nonce_url})


@router.get("/oauth/bootstrap", include_in_schema=False)
async def bootstrap_redirect(token: str = Query(...)):
    _require_provider_auth()
    payload = provider_auth_service.consume_bootstrap_token(token)
    session_token = create_session_token(
        email=str(payload["email"]),
        name=str(payload["name"]),
        picture=str(payload.get("picture") or ""),
        role=str(payload["role"]),
    )
    response = RedirectResponse(str(payload["next_url"]), status_code=302)
    set_session_cookie(response, session_token)
    return response
