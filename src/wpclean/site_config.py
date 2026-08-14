from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SiteConnectionProfile:
    host: str
    username: str
    password: str | None
    protocol: str
    port: int
    remote_path: str
    passive: bool = True
    workers: int = 6
    block_mb: int = 1
    site_url: str | None = None

    @property
    def use_tls(self) -> bool:
        return self.protocol.lower() in {"ftps", "ftp+tls", "ftp-tls"}

    @property
    def web_base_url(self) -> str:
        if self.site_url:
            return self.site_url.rstrip("/")
        return f"https://{self.host}".rstrip("/")


def load_site_profile(path: Path) -> SiteConnectionProfile:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))

    required = ["host", "username", "protocol", "remotePath"]
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError(f"Missing required config field(s): {', '.join(missing)}")

    protocol = str(raw["protocol"]).strip().lower()
    if protocol not in {"ftp", "ftps", "ftp+tls", "ftp-tls"}:
        raise ValueError(
            f"Unsupported protocol '{protocol}'. Current profile loader supports ftp/ftps."
        )

    default_port = 21
    site_url = str(raw["siteUrl"]).strip() if raw.get("siteUrl") else None
    if site_url and not site_url.lower().startswith(("http://", "https://")):
        raise ValueError("siteUrl must start with http:// or https://")

    return SiteConnectionProfile(
        host=str(raw["host"]).strip(),
        username=str(raw["username"]).strip(),
        password=(str(raw["password"]) if raw.get("password") else None),
        protocol=protocol,
        port=int(raw.get("port", default_port)),
        remote_path=str(raw["remotePath"]).strip(),
        passive=bool(raw.get("passive", True)),
        workers=max(1, min(16, int(raw.get("workers", 6)))),
        block_mb=max(1, min(8, int(raw.get("blockMb", 1)))),
        site_url=site_url,
    )
