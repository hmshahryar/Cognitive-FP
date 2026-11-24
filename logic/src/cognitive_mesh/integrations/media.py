"""Download and fingerprint a public image the user linked, so approval binds the exact image.

Meta fetches the image from the same link when it publishes. The app downloads it first to check
the type, size and dimensions and to record its SHA-256. Just before publishing it downloads the
link again and stops if the fingerprint changed. Only public HTTPS hosts are fetched, so a link
can't make the server reach its own network.
"""

import asyncio
import hashlib
import ipaddress
import socket
import struct
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from cognitive_mesh.integrations.gateway import ActionRejectedError

MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15.0
_CHUNK = 64 * 1024


class ImageCheckError(ValueError):
    """The linked image can't be used. The message is safe to show the user."""


@dataclass(frozen=True)
class FetchedImage:
    url: str
    sha256: str
    content_type: str
    size: int
    width: int | None
    height: int | None

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


def _dimensions(data: bytes, content_type: str) -> tuple[int | None, int | None]:
    """Read width and height from the file header, and check it matches the declared type."""
    if content_type == "image/png":
        if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
            raise ImageCheckError("The link didn't return a real PNG image")
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if content_type == "image/gif":
        if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 10:
            raise ImageCheckError("The link didn't return a real GIF image")
        width, height = struct.unpack("<HH", data[6:10])
        return width, height
    if data[:2] != b"\xff\xd8":
        raise ImageCheckError("The link didn't return a real JPEG image")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        i += 2 + length
    return None, None


async def _check_public(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ImageCheckError("The image link must start with https://")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, parts.port or 443, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ImageCheckError("I couldn't reach the image link") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ImageCheckError("The image link must point to a public website")


async def fetch_image(
    url: str, *, allowed_types: frozenset[str], max_bytes: int
) -> FetchedImage:
    data, content_type = await _download(url, allowed_types, max_bytes)
    width, height = _dimensions(data, content_type)
    return FetchedImage(
        url=url,
        sha256=hashlib.sha256(data).hexdigest(),
        content_type=content_type,
        size=len(data),
        width=width,
        height=height,
    )


async def fetch_bytes(url: str, *, allowed_types: frozenset[str], max_bytes: int) -> bytes:
    """The checked image file itself, for drawing (a banner background)."""
    data, content_type = await _download(url, allowed_types, max_bytes)
    _dimensions(data, content_type)
    return data


async def _download(
    url: str, allowed_types: frozenset[str], max_bytes: int
) -> tuple[bytes, str]:
    current = url
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await _check_public(current)
            try:
                async with client.stream("GET", current) as response:
                    if response.is_redirect and "location" in response.headers:
                        current = urljoin(current, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise ImageCheckError(
                            f"The image link returned an error ({response.status_code})"
                        )
                    content_type = (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    if content_type not in allowed_types:
                        names = ", ".join(sorted(t.split("/")[1].upper() for t in allowed_types))
                        raise ImageCheckError(f"The link must be a direct {names} image file")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(_CHUNK):
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise ImageCheckError(
                                f"The image is larger than {max_bytes // (1024 * 1024)} MB"
                            )
            except httpx.HTTPError as exc:
                raise ImageCheckError("I couldn't download the image from that link") from exc
            data = bytes(body)
            if not data:
                raise ImageCheckError("The image link returned an empty file")
            return data, content_type
    raise ImageCheckError("The image link redirects too many times")


def check_image_record(image: Any) -> None:
    keys = {"url", "sha256", "content_type", "size", "width", "height"}
    if not isinstance(image, dict) or set(image) != keys:
        raise ValueError("The approved image record is not valid")
    if not str(image["url"]).startswith("https://") or len(str(image["sha256"])) != 64:
        raise ValueError("The approved image record is not valid")


async def verify_unchanged(image: dict[str, Any], allowed_types: frozenset[str]) -> None:
    """Raise ActionRejectedError (nothing sent) unless the link still serves the approved image."""
    try:
        current = await fetch_image(
            image["url"], allowed_types=allowed_types, max_bytes=max(image["size"], 1)
        )
    except ImageCheckError as exc:
        raise ActionRejectedError(f"The image link can no longer be checked: {exc}") from exc
    if current.sha256 != image["sha256"]:
        raise ActionRejectedError("The image at the link changed after approval")
