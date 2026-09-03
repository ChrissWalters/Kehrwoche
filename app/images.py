"""Turning an upload into something safe to serve.

Nothing that arrives from a browser is stored as it came in. Every picture is decoded,
rotated upright, scaled down and **re-encoded** — which strips metadata, kills anything
hidden behind an image extension, and keeps the data volume small. The result is named
after the hash of its own bytes, so the same picture is never stored twice and a file
name can never collide or be guessed.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.errors import AppError, ErrorCode

#: Largest upload accepted, before decoding.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
#: Longest edge after scaling. Avatars and household pictures never need more.
MAX_EDGE = 512
#: One output format for everything — small, widely supported, animation free.
OUTPUT_SUFFIX = ".webp"
WEBP_QUALITY = 82
#: Guard against decompression bombs: a 50 megapixel picture has no business here.
MAX_PIXELS = 50_000_000


def _reject(message: str, key: str) -> AppError:
    return AppError(400, ErrorCode.VALIDATION_ERROR, message, "file", message_key=key)


def _not_found() -> AppError:
    return AppError(
        404,
        ErrorCode.NOT_FOUND,
        "Picture not found.",
        message_key="error.image.not_found",
    )


def process_image(data: bytes) -> bytes:
    """Decode, rotate upright, scale and re-encode as WebP.

    The file type is decided by the **content**, never by the name or the declared
    content type: a shell script called ``avatar.png`` fails here, which is exactly the
    point.
    """
    if not data:
        raise _reject("The file is empty.", "error.image.empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(413, ErrorCode.PAYLOAD_TOO_LARGE, "The picture is too large.", "file")

    try:
        with Image.open(BytesIO(data)) as probe:
            if probe.width * probe.height > MAX_PIXELS:
                raise _reject("The picture has too many pixels.", "error.image.too_many_pixels")
            # Phone cameras store the orientation as metadata; without this a portrait
            # photo would arrive lying on its side.
            upright = ImageOps.exif_transpose(probe)
            upright.thumbnail((MAX_EDGE, MAX_EDGE))
            # WebP has no palette or alpha surprises this way, and no CMYK.
            converted = upright.convert("RGB")
            buffer = BytesIO()
            converted.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=4)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise _reject("That is not a picture we can read.", "error.image.unreadable") from error

    return buffer.getvalue()


def store_image(data: bytes, media_dir: Path) -> str:
    """Process an upload and put it into the data volume. Returns the file name.

    Identical pictures collapse into one file — two people with the same avatar cost one
    file, and re-uploading the same picture changes nothing on disk.
    """
    processed = process_image(data)
    name = f"{hashlib.sha256(processed).hexdigest()}{OUTPUT_SUFFIX}"

    media_dir.mkdir(parents=True, exist_ok=True)
    target = media_dir / name
    if not target.exists():
        # Write beside the target and move it into place: a half-written file must never
        # be served, and the name promises exactly these bytes.
        temporary = target.with_suffix(".part")
        temporary.write_bytes(processed)
        temporary.replace(target)
    return name


def resolve_image(name: str, media_dir: Path) -> Path:
    """The path of a stored picture — refusing anything that is not one of our names.

    File names are hashes plus suffix. Checking that shape is what keeps a request for
    ``../../etc/passwd`` from ever touching the file system.
    """
    stem = name.removesuffix(OUTPUT_SUFFIX)
    is_hash = len(stem) == 64 and all(character in "0123456789abcdef" for character in stem)
    if not name.endswith(OUTPUT_SUFFIX) or not is_hash:
        raise _not_found()

    path = media_dir / name
    if not path.is_file():
        raise _not_found()
    return path


def delete_image(name: str | None, media_dir: Path) -> None:
    """Remove a stored picture; missing files are not an error.

    Only call this once nothing points at the name any more — pictures are shared by
    content, so the last reference has to be gone.
    """
    if not name:
        return
    with_suffix = media_dir / name
    with_suffix.unlink(missing_ok=True)
