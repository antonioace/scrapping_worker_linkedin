"""Load private LinkedIn session cookies without exposing their values."""

import json
import math
import re
import time
from pathlib import Path


class CookieFileError(RuntimeError):
    pass


def load_cookies(path: Path) -> list[dict]:
    """Accept browser cookie exports or the cookies array of a storage state."""
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            content = file.read(1_048_577)
        if len(content) > 1_048_576:
            raise CookieFileError("El JSON de cookies supera el límite de 1 MiB.")
        data = json.loads(content)
    except (OSError, UnicodeError):
        raise CookieFileError("No se pudo leer el archivo de cookies. Revisa su ruta y permisos.") from None
    except (ValueError, RecursionError):
        raise CookieFileError("El archivo de cookies no es un JSON válido. Revisa la plantilla.") from None

    if isinstance(data, dict):
        data = data.get("cookies")
    if not isinstance(data, list) or not 1 <= len(data) <= 200:
        raise CookieFileError("El JSON debe contener una lista de 1 a 200 cookies, o un objeto con esa lista en 'cookies'.")

    cookies = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            raise CookieFileError("Cada cookie debe ser un objeto JSON.")
        name, value = item.get("name"), item.get("value")
        if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise CookieFileError("Hay una cookie con nombre inválido.")
        if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise CookieFileError("Hay una cookie con valor inválido.")
        if name == "li_at" and not value.strip():
            raise CookieFileError("Completa el valor de li_at en tu JSON privado con una sesión válida.")

        domain = item.get("domain", ".linkedin.com")
        if not isinstance(domain, str):
            raise CookieFileError("El dominio de una cookie no es válido.")
        domain = domain.lower()
        host = domain.removeprefix(".")
        if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host) or not (
            host == "linkedin.com" or host.endswith(".linkedin.com")
        ):
            raise CookieFileError("Solo se aceptan cookies de linkedin.com. Exporta únicamente las de LinkedIn.")
        cookie_path = item.get("path", "/")
        if not isinstance(cookie_path, str) or not cookie_path.startswith("/") or any(
            ord(char) < 32 or ord(char) == 127 for char in cookie_path
        ):
            raise CookieFileError("La ruta de una cookie no es válida.")
        if name == "li_at" and (host not in ("linkedin.com", "www.linkedin.com") or cookie_path != "/"):
            raise CookieFileError("li_at debe corresponder a linkedin.com o www.linkedin.com, con ruta '/'.")

        cookie = {"name": name, "value": value, "domain": domain, "path": cookie_path}
        for flag, default in (("secure", True), ("httpOnly", name == "li_at")):
            flag_value = item.get(flag, default)
            if not isinstance(flag_value, bool):
                raise CookieFileError("secure y httpOnly deben ser booleanos JSON.")
            cookie[flag] = flag_value

        same_site = item.get("sameSite")
        if same_site is not None:
            options = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}
            if not isinstance(same_site, str) or same_site.lower() not in (*options, "unspecified"):
                raise CookieFileError("sameSite debe ser Strict, Lax, None o unspecified.")
            if same_site.lower() in options:
                cookie["sameSite"] = options[same_site.lower()]

        expires = item.get("expires", item.get("expirationDate", -1))
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise CookieFileError("La caducidad de una cookie debe ser una fecha Unix en segundos.")
        if expires != -1:
            if expires <= time.time():
                if name == "li_at":
                    raise CookieFileError("La cookie li_at está caducada. Renueva tu sesión y actualiza el JSON.")
                continue
            cookie["expires"] = expires
        key = (name, domain, cookie_path)
        if key in seen:
            raise CookieFileError("El JSON contiene cookies duplicadas para el mismo dominio y ruta.")
        seen.add(key)
        cookies.append(cookie)

    if not any(cookie["name"] == "li_at" for cookie in cookies):
        raise CookieFileError("El archivo debe incluir la cookie de sesión li_at.")
    return cookies
