from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
from tempfile import TemporaryDirectory
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import zipfile

from .rebuild_execute import _upload_tree
from .site_config import SiteConnectionProfile
from .transport import FTPTransport


ProgressCallback = Callable[[dict], None]
PLUGIN_API_URL = "https://api.wordpress.org/plugins/info/1.2/"
TRUSTED_DOWNLOAD_HOSTS = {"downloads.wordpress.org"}
MAX_PLUGIN_ZIP_FILES = 60000
MAX_PLUGIN_ZIP_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_PLUGIN_ZIP_DOWNLOAD = 512 * 1024 * 1024
PLUGIN_HEADER_BYTES = 64 * 1024
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.I)
WPORG_PLUGIN_URI_RE = re.compile(r"https?://(?:www\.)?wordpress\.org/plugins/([a-z0-9._-]+)/?", re.I)


@dataclass(slots=True)
class PluginInventoryItem:
    slug: str
    source_path: str
    kind: str
    name: str = ""
    version: str = ""
    main_file: str = ""
    plugin_uri: str = ""
    text_domain: str = ""
    candidate_slugs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WordPressOrgPlugin:
    requested_slug: str
    slug: str
    name: str
    version: str
    download_link: str
    requires: str = ""
    requires_php: str = ""
    tested: str = ""


@dataclass(slots=True)
class PluginClassification:
    inventory: PluginInventoryItem
    status: str
    wporg: WordPressOrgPlugin | None = None
    detail: str = ""


@dataclass(slots=True)
class PluginInstallResult:
    source_slug: str
    wporg_slug: str
    name: str
    version: str
    remote_slug: str
    files_uploaded: int
    package_sha256: str
    download_link: str


@dataclass(slots=True)
class PluginStageReport:
    inventory_count: int = 0
    wordpress_org_count: int = 0
    manual_count: int = 0
    lookup_error_count: int = 0
    install_prompted: bool = False
    install_accepted: bool = False
    installed_count: int = 0
    inventory: list[dict[str, object]] = field(default_factory=list)
    classifications: list[dict[str, object]] = field(default_factory=list)
    installed: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _parse_plugin_headers(path: Path) -> dict[str, str]:
    try:
        data = path.read_bytes()[:PLUGIN_HEADER_BYTES]
    except OSError:
        return {}
    text = data.decode("utf-8", errors="replace")
    headers: dict[str, str] = {}
    for label, key in (
        ("Plugin Name", "name"),
        ("Version", "version"),
        ("Plugin URI", "plugin_uri"),
        ("Text Domain", "text_domain"),
    ):
        match = re.search(rf"^[ \t\/*#@]*{re.escape(label)}\s*:\s*(.+?)\s*$", text, re.I | re.M)
        if match:
            value = match.group(1).strip().strip("*/ \t")
            headers[key] = value
    return headers


def _plugin_main_file(component: Path) -> tuple[Path | None, dict[str, str]]:
    if component.is_file():
        headers = _parse_plugin_headers(component)
        return (component, headers) if headers.get("name") else (None, {})

    candidates: list[tuple[Path, dict[str, str]]] = []
    try:
        php_files = sorted(
            (path for path in component.iterdir() if path.is_file() and path.suffix.lower() == ".php"),
            key=lambda item: item.name.lower(),
        )
    except OSError:
        return None, {}

    for path in php_files:
        headers = _parse_plugin_headers(path)
        if headers.get("name"):
            candidates.append((path, headers))
    if not candidates:
        return None, {}

    exact = [item for item in candidates if item[0].stem.lower() == component.name.lower()]
    return exact[0] if exact else candidates[0]


def _candidate_slugs(component_slug: str, headers: dict[str, str]) -> list[str]:
    values: list[str] = []

    def add(value: str) -> None:
        normalized = value.strip().strip("/").lower()
        if normalized and SLUG_RE.fullmatch(normalized) and normalized not in values:
            values.append(normalized)

    add(component_slug)
    plugin_uri = headers.get("plugin_uri", "")
    match = WPORG_PLUGIN_URI_RE.search(plugin_uri)
    if match:
        add(match.group(1))
    text_domain = headers.get("text_domain", "")
    if text_domain:
        add(text_domain)
    return values


def inventory_backup_plugins(backup_root: Path) -> list[PluginInventoryItem]:
    plugins_root = backup_root / "plugins"
    if not plugins_root.is_dir():
        return []

    inventory: list[PluginInventoryItem] = []
    try:
        entries = sorted(plugins_root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix.lower() != ".php":
            continue
        if entry.is_file() and entry.name.lower() == "index.php":
            continue
        if not entry.is_dir() and not entry.is_file():
            continue

        main, headers = _plugin_main_file(entry)
        if entry.is_file() and main is None:
            continue
        slug = entry.name if entry.is_dir() else entry.stem
        candidates = _candidate_slugs(slug, headers)
        inventory.append(
            PluginInventoryItem(
                slug=slug,
                source_path=str(entry),
                kind="directory" if entry.is_dir() else "single-file",
                name=headers.get("name", "") or slug,
                version=headers.get("version", ""),
                main_file=(str(main.relative_to(plugins_root)).replace("\\", "/") if main else ""),
                plugin_uri=headers.get("plugin_uri", ""),
                text_domain=headers.get("text_domain", ""),
                candidate_slugs=candidates,
            )
        )
    return inventory


def _plugin_api_url(slug: str) -> str:
    params = {
        "action": "plugin_information",
        "request[slug]": slug,
        "request[fields][sections]": "0",
        "request[fields][description]": "0",
        "request[fields][short_description]": "0",
        "request[fields][banners]": "0",
        "request[fields][icons]": "0",
        "request[fields][reviews]": "0",
        "request[fields][versions]": "0",
        "request[fields][downloadlink]": "1",
    }
    return PLUGIN_API_URL + "?" + urlencode(params)


def wordpress_org_plugin_info(slug: str, *, timeout: int = 25) -> WordPressOrgPlugin | None:
    request = Request(
        _plugin_api_url(slug),
        headers={"User-Agent": "WP-Clean-Rebuild/0.6", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"WordPress.org Plugin API HTTP {exc.code} for {slug}") from exc
    except URLError as exc:
        raise RuntimeError(f"WordPress.org Plugin API network error for {slug}: {exc.reason}") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"WordPress.org Plugin API returned invalid JSON for {slug}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"WordPress.org Plugin API returned unexpected data for {slug}")
    error = str(payload.get("error") or "").strip()
    if error:
        if "not found" in error.lower():
            return None
        raise RuntimeError(f"WordPress.org Plugin API error for {slug}: {error}")

    api_slug = str(payload.get("slug") or slug).strip().lower()
    download_link = str(payload.get("download_link") or "").strip()
    version = str(payload.get("version") or "").strip()
    name = str(payload.get("name") or api_slug).strip()
    if not api_slug or not download_link or not version:
        raise RuntimeError(f"WordPress.org Plugin API response is incomplete for {slug}")

    parsed = urlsplit(download_link)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() not in TRUSTED_DOWNLOAD_HOSTS:
        raise RuntimeError(
            f"WordPress.org returned an untrusted plugin download URL for {slug}: {download_link}"
        )

    return WordPressOrgPlugin(
        requested_slug=slug,
        slug=api_slug,
        name=name,
        version=version,
        download_link=download_link,
        requires=str(payload.get("requires") or ""),
        requires_php=str(payload.get("requires_php") or ""),
        tested=str(payload.get("tested") or ""),
    )


def classify_plugin(item: PluginInventoryItem) -> PluginClassification:
    errors: list[str] = []
    for slug in item.candidate_slugs or [item.slug.lower()]:
        try:
            info = wordpress_org_plugin_info(slug)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if info is not None:
            return PluginClassification(inventory=item, status="wordpress.org", wporg=info)

    if errors:
        return PluginClassification(
            inventory=item,
            status="lookup-error",
            detail=" | ".join(errors),
        )
    return PluginClassification(
        inventory=item,
        status="manual",
        detail="Not found in the WordPress.org Plugin Directory.",
    )


def classify_plugins(
    inventory: list[PluginInventoryItem],
    *,
    workers: int = 6,
    progress: ProgressCallback | None = None,
) -> list[PluginClassification]:
    if not inventory:
        return []
    results: list[PluginClassification | None] = [None] * len(inventory)
    max_workers = max(1, min(workers, 8, len(inventory)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(classify_plugin, item): index for index, item in enumerate(inventory)}
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = PluginClassification(
                    inventory=inventory[index],
                    status="lookup-error",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            results[index] = result
            completed += 1
            if progress:
                progress(
                    {
                        "phase": "plugin_lookup",
                        "completed": completed,
                        "total": len(inventory),
                        "slug": inventory[index].slug,
                        "status": result.status,
                    }
                )
    return [item for item in results if item is not None]


def _download_plugin_package(info: WordPressOrgPlugin) -> tuple[bytes, str]:
    request = Request(
        info.download_link,
        headers={"User-Agent": "WP-Clean-Rebuild/0.6", "Accept": "application/zip"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=180) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_PLUGIN_ZIP_DOWNLOAD:
                raise RuntimeError(
                    f"Plugin package is larger than safety limit: {info.slug} ({content_length} bytes)"
                )
            data = response.read(MAX_PLUGIN_ZIP_DOWNLOAD + 1)
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Could not download WordPress.org plugin {info.slug}: {exc}") from exc

    if len(data) > MAX_PLUGIN_ZIP_DOWNLOAD:
        raise RuntimeError(f"Plugin package exceeded safety limit: {info.slug}")
    if len(data) < 100 or not data.startswith(b"PK"):
        raise RuntimeError(f"Downloaded package is not a valid-looking ZIP: {info.slug}")
    return data, hashlib.sha256(data).hexdigest()


def _zip_path(info: zipfile.ZipInfo) -> PurePosixPath:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Unsafe path in plugin ZIP: {info.filename}")
    return path


def _zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return bool(mode and stat.S_ISLNK(mode))


def _extract_plugin_package(data: bytes, destination: Path, info: WordPressOrgPlugin) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            files = [item for item in members if not item.is_dir()]
            if not files:
                raise RuntimeError(f"Plugin ZIP is empty: {info.slug}")
            if len(files) > MAX_PLUGIN_ZIP_FILES:
                raise RuntimeError(f"Plugin ZIP contains too many files: {info.slug}")
            expanded = sum(max(0, item.file_size) for item in files)
            if expanded > MAX_PLUGIN_ZIP_UNCOMPRESSED:
                raise RuntimeError(f"Plugin ZIP expands beyond safety limit: {info.slug}")

            for member in members:
                path = _zip_path(member)
                if _zip_symlink(member):
                    raise RuntimeError(f"Symlink is not allowed in plugin ZIP: {member.filename}")
                target = destination.joinpath(*path.parts)
                resolved = target.resolve()
                if base not in resolved.parents and resolved != base:
                    raise RuntimeError(f"Unsafe path in plugin ZIP: {member.filename}")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"WordPress.org package is not a valid ZIP: {info.slug}") from exc

    preferred = destination / info.slug
    if preferred.is_dir():
        plugin_root = preferred
    else:
        directories = [path for path in destination.iterdir() if path.is_dir() and not path.name.startswith("__MACOSX")]
        root_files = [path for path in destination.iterdir() if path.is_file()]
        plugin_root = directories[0] if len(directories) == 1 and not root_files else destination

    main, headers = _plugin_main_file(plugin_root)
    if main is None or not headers.get("name"):
        raise RuntimeError(f"WordPress.org plugin package has no valid Plugin Name header: {info.slug}")
    return plugin_root


def install_wordpress_org_plugin(
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    classification: PluginClassification,
    *,
    progress: ProgressCallback | None = None,
) -> PluginInstallResult:
    if classification.status != "wordpress.org" or classification.wporg is None:
        raise ValueError("Only a verified WordPress.org plugin classification can be installed.")
    info = classification.wporg
    source = classification.inventory

    if progress:
        progress({"phase": "plugin_download", "slug": source.slug, "wporg_slug": info.slug})
    data, digest = _download_plugin_package(info)

    with TemporaryDirectory(prefix=f"wpclean-plugin-{info.slug}-") as temp_dir:
        root = _extract_plugin_package(data, Path(temp_dir), info)
        remote_slug = source.slug if source.kind == "directory" else info.slug
        if not SLUG_RE.fullmatch(remote_slug):
            raise RuntimeError(f"Unsafe plugin destination slug: {remote_slug}")
        remote = str(PurePosixPath(profile.remote_path) / "wp-content" / "plugins" / remote_slug)
        if progress:
            progress({"phase": "plugin_upload_start", "slug": source.slug, "remote_slug": remote_slug})
        files_uploaded = _upload_tree(
            transport,
            root,
            remote,
            progress_phase="upload_wporg_plugin",
            progress=progress,
        )

    return PluginInstallResult(
        source_slug=source.slug,
        wporg_slug=info.slug,
        name=info.name,
        version=info.version,
        remote_slug=remote_slug,
        files_uploaded=files_uploaded,
        package_sha256=digest,
        download_link=info.download_link,
    )


def classification_to_dict(item: PluginClassification) -> dict[str, object]:
    return {
        "inventory": asdict(item.inventory),
        "status": item.status,
        "wporg": asdict(item.wporg) if item.wporg else None,
        "detail": item.detail,
    }


__all__ = [
    "PluginInventoryItem",
    "WordPressOrgPlugin",
    "PluginClassification",
    "PluginInstallResult",
    "PluginStageReport",
    "inventory_backup_plugins",
    "wordpress_org_plugin_info",
    "classify_plugin",
    "classify_plugins",
    "install_wordpress_org_plugin",
    "classification_to_dict",
]
