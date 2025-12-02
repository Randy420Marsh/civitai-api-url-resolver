import json
import os
import re
import configparser
from urllib.parse import urlparse, parse_qs, urljoin
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


_MODEL_ID_RE = re.compile(r"^/models/(\d+)(?:/.*)?$")
_DOWNLOAD_RE = re.compile(r"^/api/download/models/(\d+)$")

# Resolve config file co-located with this module: custom_nodes/.../civitai_url_resolver.py
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_MODULE_DIR, ".config")


def _load_civitai_token_from_config() -> str | None:
    """
    Loads civitai_token from .config:
      [API]
      civitai_token = ...
    Returns None if missing/empty.
    
    Token must be at least 32 characters and contain no spaces.
    """
    if not os.path.exists(_CONFIG_PATH):
        return None

    cfg = configparser.ConfigParser()
    try:
        cfg.read(_CONFIG_PATH, encoding="utf-8")
        token = cfg.get("API", "civitai_token", fallback="").strip()
        if token and (len(token) < 32 or ' ' in token):
            return None  # Likely invalid token
        return token or None
    except Exception:
        # Do not raise; keep node functional without exposing stack traces in UI.
        return None


def _http_get_json(url: str, token: str | None, timeout_s: float = 10.0) -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": "ComfyUI-Civitai-Resolver",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _normalize_download_url(origin: str, download_url: str) -> str:
    if download_url.startswith("/"):
        return urljoin(origin, download_url)
    return download_url


def resolve_civitai_to_direct(url: str) -> str:
    """
    Resolves civitai share/model URLs to a direct download URL.
    Token is loaded from local .config and used ONLY in headers (never appended to URL).
    """
    url = (url or "").strip()
    if not url:
        return ""

    u = urlparse(url)
    host = (u.netloc or "").lower()

    # If already a direct download URL, return as-is.
    if host == "civitai.com" and _DOWNLOAD_RE.match(u.path):
        return url

    # Only special-handle civitai.com URLs.
    if host != "civitai.com":
        return url

    token = _load_civitai_token_from_config()

    # Prefer modelVersionId if present
    qs = parse_qs(u.query)
    mvid = (qs.get("modelVersionId") or [None])[0]
    if mvid and str(mvid).isdigit():
        api = f"https://civitai.com/api/v1/model-versions/{mvid}"
        try:
            data = _http_get_json(api, token)
            dl = data.get("downloadUrl")
            if dl:
                return _normalize_download_url("https://civitai.com", dl)
        except (HTTPError, URLError, json.JSONDecodeError):
            # fallback: still no secrets in URL
            return f"https://civitai.com/api/download/models/{mvid}"

    # Fallback: model page URL without modelVersionId
    m = _MODEL_ID_RE.match(u.path)
    if m:
        model_id = m.group(1)
        api = f"https://civitai.com/api/v1/models/{model_id}"
        try:
            data = _http_get_json(api, token)
            versions = data.get("modelVersions") or []
            if versions:
                dl = versions[0].get("downloadUrl")
                if dl:
                    return _normalize_download_url("https://civitai.com", dl)
        except (HTTPError, URLError, json.JSONDecodeError):
            pass

    return url


class CivitaiShareToDirectURL:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "civitai_share_url": ("STRING", {"multiline": False, "default": ""}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("direct_url",)
    FUNCTION = "run"
    CATEGORY = "utils/url"

    def run(self, civitai_share_url: str):
        direct = resolve_civitai_to_direct(civitai_share_url)
        return (direct,)
