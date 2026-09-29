"""Route signed proxy requests to explicitly supported Google destinations."""

import re
from typing import Tuple


GOOGLE_HOSTS = frozenset({
    "www.googleapis.com", "gmail.googleapis.com", "calendar.googleapis.com",
    "tasks.googleapis.com", "sheets.googleapis.com", "docs.googleapis.com",
    "slides.googleapis.com", "forms.googleapis.com", "script.googleapis.com",
    "chat.googleapis.com", "classroom.googleapis.com", "people.googleapis.com",
    "keep.googleapis.com", "cloudidentity.googleapis.com", "admin.googleapis.com",
    "adsense.googleapis.com", "analyticsadmin.googleapis.com", "analyticsdata.googleapis.com",
    "meet.googleapis.com", "driveactivity.googleapis.com", "drivelabels.googleapis.com",
    "searchconsole.googleapis.com", "youtube.googleapis.com", "photoslibrary.googleapis.com",
    "photospicker.googleapis.com", "maps.googleapis.com", "places.googleapis.com",
    "docs.google.com",
})

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class RoutingError(ValueError):
    pass


def resolve_google_host_and_path(proxy_path: str, original_host: str) -> Tuple[str, str]:
    host = original_host.lower()
    # Picker media URLs use variable Google-owned hosts. Policy rules can narrow
    # this allowance by matching the exact upstream host.
    media_host = (
        len(host) <= 253
        and host.endswith(".googleusercontent.com")
        and all(_DNS_LABEL.fullmatch(label) for label in host.split("."))
    )
    if host not in GOOGLE_HOSTS and not media_host:
        raise RoutingError("missing or unsupported X-GOG-Google-Host")
    if (
        not proxy_path.strip("/")
        or proxy_path.startswith("//")
        or any(ord(char) < 32 or ord(char) == 127 or char in "?#" for char in proxy_path)
    ):
        raise RoutingError("invalid Google request path")
    return host, "/" + proxy_path.lstrip("/")
