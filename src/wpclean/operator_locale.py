from __future__ import annotations

from typing import Any


_REPLACEMENTS = (
    ("THEME RESTORE STAGE", "KHÔI PHỤC THEME"),
    ("Validating and uploading trusted Flatsome package...", "Đang kiểm tra và tải gói Flatsome tin cậy..."),
    ("Uploading Flatsome:", "Đang tải Flatsome:"),
    ("Flatsome installed from trusted package:", "Đã cài Flatsome từ gói tin cậy:"),
    ("Flatsome package SHA-256:", "SHA-256 gói Flatsome:"),
    ("Child-theme files scanned:", "Số file theme con đã quét:"),
    ("Uploading child theme", "Đang tải theme con"),
    ("Child theme", "Theme con"),
    ("installed:", "đã cài:"),
    ("PLUGIN RESTORE STAGE", "KHÔI PHỤC PLUGIN"),
    ("WordPress.org lookup:", "Kiểm tra WordPress.org:"),
    ("Starting WordPress.org plugin install", "Bắt đầu cài plugin sạch từ WordPress.org"),
    ("Downloading/installing", "Đang tải/cài"),
    ("Downloading", "Đang tải"),
    ("Uploading clean", "Đang tải bản sạch"),
    ("Uploading", "Đang tải"),
    ("Installed", "Đã cài"),
    ("PLUGIN STAGE COMPLETED", "HOÀN TẤT PLUGIN"),
    ("WordPress.org plugins installed:", "Plugin WordPress.org đã cài:"),
    ("Manual/private plugins", "Plugin thủ công/private"),
    ("Plugin lookup", "Kiểm tra plugin"),
    ("Plugin report:", "Báo cáo plugin:"),
    ("Warning:", "Cảnh báo:"),
)


def dich(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    translated = text
    for source, target in _REPLACEMENTS:
        translated = translated.replace(source, target)
    return translated


class VietnameseConsoleProxy:
    """Proxy Rich Console while translating operator-facing engine strings."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def print(self, *objects: Any, **kwargs: Any) -> Any:
        return self._inner.print(*(dich(item) for item in objects), **kwargs)

    def status(self, status: Any, *args: Any, **kwargs: Any):
        return self._inner.status(dich(status), *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


__all__ = ["VietnameseConsoleProxy", "dich"]
