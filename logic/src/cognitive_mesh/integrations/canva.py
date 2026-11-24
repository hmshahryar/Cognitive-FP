"""Make a banner on the Canva Free plan: draw it, put it in the user's Canva, export it as JPEG.

Canva's API can't lay out text on Free plans (Autofill needs Pro), so the app draws the banner
with Pillow using only the user's own words. It is then uploaded to the user's Canva as a
private 1080x1080 design and exported. Tool inputs come from Composio's Canva toolkit docs
(version 20261116_00); export links last 24 hours (Canva's export API docs).
"""

import io
import re
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

TOOLKIT = "canva"
UPLOAD_TOOL = "CANVA_CREATE_URL_ASSET_UPLOAD_JOB"
UPLOAD_STATUS_TOOL = "CANVA_GET_URL_ASSET_UPLOADS_JOBID"
DESIGN_TOOL = "CANVA_POST_DESIGNS"
EXPORT_TOOL = "CANVA_POST_EXPORTS"
EXPORT_STATUS_TOOL = "CANVA_GET_DESIGN_EXPORT_JOB_RESULT"
SIZE = 1080
MAX_HEADLINE_CHARS = 80
MAX_SUBTEXT_CHARS = 160
DEFAULT_COLOR = "#0F172A"
POLL_SECONDS = 2.0
POLL_ATTEMPTS = 20

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class BannerError(ValueError):
    """The banner can't be made. The message is safe to show the user."""


@dataclass(frozen=True)
class CanvaBanner:
    design_id: str
    edit_url: str | None
    export_url: str


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _contrast(color: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = color
    return (17, 24, 39) if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else (255, 255, 255)


def _draw_block(
    draw: ImageDraw.ImageDraw, lines: list[str], font: Any, top: int, fill: Any, gap: int
) -> int:
    y = top
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        draw.text(((SIZE - width) / 2 - box[0], y - box[1]), line, font=font, fill=fill)
        y += height + gap
    return y


def render_banner(
    *, headline: str, subtext: str = "", color: str = DEFAULT_COLOR, background: bytes | None = None
) -> bytes:
    """A 1080x1080 JPEG with the user's exact words, centred."""
    if not headline.strip() or len(headline) > MAX_HEADLINE_CHARS:
        raise BannerError(f"The headline must be 1 to {MAX_HEADLINE_CHARS} characters")
    if len(subtext) > MAX_SUBTEXT_CHARS:
        raise BannerError(f"The banner text must be under {MAX_SUBTEXT_CHARS} characters")
    if not _HEX.match(color):
        raise BannerError("The colour must look like #1E3A8A")
    base = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    if background:
        try:
            photo = Image.open(io.BytesIO(background)).convert("RGB")
        except Exception as exc:
            raise BannerError("The background image couldn't be read") from exc
        image = ImageOps.fit(photo, (SIZE, SIZE))
        image = Image.blend(image, Image.new("RGB", (SIZE, SIZE), (0, 0, 0)), 0.45)
        ink: tuple[int, int, int] = (255, 255, 255)
    else:
        image = Image.new("RGB", (SIZE, SIZE), base)  # type: ignore[arg-type]
        ink = _contrast(base)  # type: ignore[arg-type]
    draw = ImageDraw.Draw(image)
    head_lines = textwrap.wrap(headline.strip(), width=16) or [headline.strip()]
    sub_lines = textwrap.wrap(subtext.strip(), width=30) if subtext.strip() else []
    head_font, sub_font = _font(104 if len(head_lines) <= 2 else 84), _font(48)
    head_h = sum(
        draw.textbbox((0, 0), line, font=head_font)[3] - draw.textbbox((0, 0), line, font=head_font)[1] + 24
        for line in head_lines
    )
    sub_h = sum(
        draw.textbbox((0, 0), line, font=sub_font)[3] - draw.textbbox((0, 0), line, font=sub_font)[1] + 16
        for line in sub_lines
    )
    rule_gap = 72 if sub_lines else 0
    top = int((SIZE - head_h - rule_gap - sub_h) / 2)
    y = _draw_block(draw, head_lines, head_font, top, ink, 24)
    if sub_lines:
        draw.rectangle(((SIZE / 2 - 60, y + 24), (SIZE / 2 + 60, y + 30)), fill=ink)
        _draw_block(draw, sub_lines, sub_font, y + rule_gap, ink, 16)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


def _unwrap(data: Any) -> Any:
    for key in ("response_data", "data"):
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            return data[key]
    return data


def _job(data: Any) -> dict[str, Any]:
    body = _unwrap(data)
    job = body.get("job") if isinstance(body, dict) else None
    if not isinstance(job, dict) or not isinstance(job.get("id"), str):
        raise BannerError("Canva returned a job I couldn't read")
    return job


def _wait(run: Callable[[str, dict[str, Any]], Any], tool: str, args: dict[str, Any],
          sleep: Callable[[float], None]) -> dict[str, Any]:
    for _ in range(POLL_ATTEMPTS):
        job = _job(run(tool, args))
        if job.get("status") == "success":
            return job
        if job.get("status") == "failed":
            error = job.get("error") if isinstance(job.get("error"), dict) else {}
            raise BannerError(f"Canva couldn't finish: {error.get('message') or 'unknown error'}")
        sleep(POLL_SECONDS)
    raise BannerError("Canva took too long, so I stopped")


def make_in_canva(
    session: Any, jpeg: bytes, *, title: str, sleep: Callable[[float], None] = time.sleep
) -> CanvaBanner:
    """Runs in a worker thread inside one draft session. Creates only private items."""
    link = session.upload(jpeg, filename="banner.jpg", mimetype="image/jpeg")
    started = _job(session.run(UPLOAD_TOOL, {"url": link, "name": title[:50]}))
    uploaded = _wait(session.run, UPLOAD_STATUS_TOOL, {"jobId": started["id"]}, sleep)
    asset = uploaded.get("asset") if isinstance(uploaded.get("asset"), dict) else {}
    asset_id = asset.get("id")
    if not isinstance(asset_id, str):
        raise BannerError("Canva didn't return the uploaded image")
    created = _unwrap(
        session.run(
            DESIGN_TOOL,
            {
                "type": "type_and_asset",
                "title": title[:255],
                "asset_id": asset_id,
                "design_type": {"type": "custom", "width": SIZE, "height": SIZE},
            },
        )
    )
    design = created.get("design") if isinstance(created, dict) else None
    if not isinstance(design, dict) or not isinstance(design.get("id"), str):
        raise BannerError("Canva didn't return the new design")
    urls = design.get("urls") if isinstance(design.get("urls"), dict) else {}
    export = _job(
        session.run(
            EXPORT_TOOL,
            {"design_id": design["id"], "format": {"type": "jpg", "quality": 92}},
        )
    )
    finished = _wait(session.run, EXPORT_STATUS_TOOL, {"exportId": export["id"]}, sleep)
    links = finished.get("urls")
    if not isinstance(links, list) or not links or not str(links[0]).startswith("https://"):
        raise BannerError("Canva didn't return the exported image")
    edit_url = urls.get("edit_url")
    return CanvaBanner(
        design_id=design["id"],
        edit_url=edit_url if isinstance(edit_url, str) and edit_url.startswith("https://www.canva.com/") else None,
        export_url=str(links[0]),
    )
