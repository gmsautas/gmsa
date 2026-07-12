import secrets
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

# *** DATA LOSS RISK — READ BEFORE CHANGING HOSTS ***
# save_upload() below writes to STATIC_DIR / "uploads" / <subdir>, i.e. local
# container disk. Neither render.yaml nor backend/Dockerfile (nor, as of this
# writing, any Railway config) attaches a persistent volume at that path —
# container filesystems are ephemeral and reset on every redeploy on both
# Render and Railway. Every profile photo, candidate photo, blog cover image,
# resource document, and MoMo payment-proof screenshot uploaded through this
# function is plausibly being silently lost on the next deploy. This is a
# real, likely-already-occurring bug, not a hypothetical.
# STOPGAP (do this now, before/at Railway cutover): mount a Railway
# persistent volume at static/uploads/ on the web service. See
# backend/scripts/RAILWAY_DEPLOYMENT_NOTES.md for details.
# REAL FIX (separate, larger, future phase — do not attempt as part of this
# change): migrate storage to S3/R2-compatible object storage so uploads
# survive redeploys/restarts regardless of host. Ideally sequence that after
# image optimization (see _process_image below) so object storage receives
# already-resized bytes instead of raw originals.

# Extension whitelists per kind of upload. These matter for security, not just
# tidiness: files under static/uploads/ are served directly from our own origin,
# so an uploaded .svg/.html/.js/.xml with embedded script would execute as
# same-origin content (bypassing the httponly/SameSite cookie defenses). Only
# inert, non-executable formats are allowed through.
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
DOCUMENT_EXTS = frozenset(
    {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt"}
)
# Payment proofs are typically a screenshot but occasionally a PDF receipt.
PROOF_EXTS = IMAGE_EXTS | frozenset({".pdf"})

# Magic-byte signatures for the image formats we accept, as a defense-in-depth
# check so a script file merely *renamed* to .png is still rejected.
_IMAGE_SIGNATURES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",   # png
    b"\xff\xd8\xff",         # jpeg
    b"GIF87a",               # gif
    b"GIF89a",               # gif
    b"RIFF",                 # webp (RIFF....WEBP)
)

DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # 8 MB

# Longest-edge cap (px) for resized images. Checked against actual template
# usage: avatars/candidate photos render at 32-80px (member_base.html,
# election_ballot.html, leadership.html, election_results.html — all
# `w-8 h-8` through `w-20 h-20`), and blog cover images render `w-full`
# capped at `h-64 sm:h-80` (blog_post.html, ~320px tall). 1200px longest
# edge is generous headroom for retina/high-DPI displays on every one of
# those while still cutting typical 3000px+ phone-camera originals down
# substantially.
MAX_IMAGE_DIMENSION = 1200

# Payment (MoMo) proof screenshots are a special case: admins open them
# full-size in a new tab to read a transaction amount/reference number
# (admin/finance/momo.html's "View" link) rather than viewing them as a
# small thumbnail, so legibility of text matters more than for avatars.
# Use a larger cap for that call site rather than the generic default.
MAX_PROOF_IMAGE_DIMENSION = 2000


class UploadError(Exception):
    """Raised when an uploaded file fails validation (type or size)."""


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{max(1, round(num_bytes / 1024)):.0f} KB"


def _looks_like_image(contents: bytes) -> bool:
    return any(contents.startswith(sig) for sig in _IMAGE_SIGNATURES)


def _process_image(contents: bytes, max_dimension: int) -> bytes:
    """Re-encode an uploaded image: strip EXIF metadata and downscale to
    max_dimension on the longest edge if it's larger.

    EXIF stripping is a real privacy fix, not just tidiness — phone camera
    photos routinely carry GPS coordinates in their EXIF block, and these
    files get served back out publicly from static/uploads/<subdir>/.

    Keeps the original format (no PNG->JPEG conversion) since PNG's alpha
    transparency has no equivalent in JPEG — forcing that conversion would
    silently break any transparent logo/graphic uploaded as a PNG. Animated
    images (multi-frame GIF/WEBP) are passed through unresized/untouched:
    Pillow's `.thumbnail()`/`.save()` only operate on a single frame by
    default, so "resizing" one here would silently drop every frame but the
    first and break the animation.

    Raises UploadError (wrapping Pillow's decode exceptions) if the bytes
    that passed the magic-byte sniff still aren't a Pillow-decodable image
    (e.g. truncated upload, malformed file).
    """
    try:
        with Image.open(BytesIO(contents)) as img:
            img.load()
            fmt = img.format
            if getattr(img, "is_animated", False):
                return contents

            # Bakes in the EXIF orientation flag (phones often store
            # landscape photos as raw-sensor-orientation + a rotate flag) so
            # the image still displays right-side-up after we strip EXIF.
            img = ImageOps.exif_transpose(img) or img

            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

            save_kwargs: dict = {}
            if fmt == "JPEG":
                # thumbnail()/exif_transpose() can leave the image in a mode
                # JPEG can't encode (e.g. palette "P"); JPEG has no alpha.
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                save_kwargs = {"quality": 88, "optimize": True}

            out = BytesIO()
            # Passing no exif=... kwarg is what actually strips the
            # metadata — re-saving via Pillow without forwarding img.info's
            # "exif" key drops it from the output.
            img.save(out, format=fmt, **save_kwargs)
            return out.getvalue()
    except UnidentifiedImageError as exc:
        raise UploadError("File does not appear to be a valid image.") from exc
    except OSError as exc:
        # Pillow raises OSError (not UnidentifiedImageError) for a
        # truncated/corrupt data stream discovered during img.load()/save(),
        # e.g. "broken data stream when reading image file".
        raise UploadError(
            "The uploaded image could not be processed — it may be corrupted."
        ) from exc


async def save_upload(
    file: UploadFile,
    subdir: str,
    *,
    allowed_exts: frozenset[str] = IMAGE_EXTS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_dimension: int = MAX_IMAGE_DIMENSION,
) -> tuple[str, str]:
    """Persist an uploaded file under static/uploads/<subdir>/ and return
    (public_url, human_size).

    Rejects, via UploadError, anything whose extension is not in allowed_exts,
    is empty, exceeds max_bytes, or (for image extensions) doesn't carry a real
    image signature.

    For image extensions, the file is also re-encoded before being written:
    resized down to max_dimension on the longest edge and stripped of EXIF
    metadata (see _process_image). Non-image extensions (documents, PDFs)
    pass through unchanged.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_exts:
        allowed = ", ".join(sorted(e.lstrip(".") for e in allowed_exts))
        raise UploadError(f"Unsupported file type. Allowed: {allowed}.")

    contents = await file.read()
    if not contents:
        raise UploadError("The uploaded file is empty.")
    if len(contents) > max_bytes:
        raise UploadError(f"File is too large (max {_human_size(max_bytes)}).")

    if suffix in IMAGE_EXTS:
        if not _looks_like_image(contents):
            raise UploadError("File does not appear to be a valid image.")
        contents = _process_image(contents, max_dimension)

    target_dir = STATIC_DIR / "uploads" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{secrets.token_hex(8)}{suffix}"
    (target_dir / filename).write_bytes(contents)

    return f"/static/uploads/{subdir}/{filename}", _human_size(len(contents))
