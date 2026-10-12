"""Validation helpers for user-uploaded profile pictures.

The upload used to be written to disk with a client-chosen extension and no content check, so a
file such as ``x_profile.html`` could be stored and served from /media/. Here the content is
opened with Pillow and the stored extension comes from the *detected* image format.
"""
import base64
import binascii
import re
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {'JPEG': 'jpg', 'PNG': 'png', 'GIF': 'gif', 'WEBP': 'webp'}
_DATA_URL = re.compile(r'^data:image/[a-zA-Z0-9.+-]+;base64,(?P<data>[A-Za-z0-9+/=\s]+)$')


def detect_extension(raw: bytes) -> str:
    """Return the safe file extension for image bytes or raise ValidationError."""
    if len(raw) > settings.MAX_PROFILE_PIC_BYTES:
        raise ValidationError('Profile picture is too large (limit 5 MB).')
    try:
        with Image.open(BytesIO(raw)) as img:
            img.verify()
            fmt = img.format
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValidationError('Profile picture must be a valid image.')
    if fmt not in ALLOWED_FORMATS:
        raise ValidationError('Profile picture must be a JPEG, PNG, GIF or WebP image.')
    return ALLOWED_FORMATS[fmt]


def image_from_data_url(data_url: str, base_name: str) -> ContentFile:
    """Decode ``data:image/...;base64,...`` into a ContentFile named ``<base_name>.<real ext>``."""
    match = _DATA_URL.match(data_url or '')
    if not match:
        raise ValidationError('Profile picture must be an image data URL.')
    try:
        raw = base64.b64decode(match.group('data'), validate=False)
    except (binascii.Error, ValueError):
        raise ValidationError('Profile picture is not valid base64.')
    ext = detect_extension(raw)
    return ContentFile(raw, name=f'{base_name}.{ext}')
