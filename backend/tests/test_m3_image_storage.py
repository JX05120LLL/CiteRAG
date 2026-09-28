import io

import pytest
from PIL import Image

from app.images.storage import PrivateImageStore
from app.services.errors import ServiceError


def image_bytes(fmt="PNG", *, size=(16, 12), exif=None):
    output = io.BytesIO()
    Image.new("RGB", size, color="red").save(output, format=fmt, exif=exif)
    return output.getvalue()


async def chunks(data):
    yield data


@pytest.mark.asyncio
async def test_image_storage_reencodes_without_metadata(tmp_path):
    store = PrivateImageStore(tmp_path / "images")
    exif = Image.Exif()
    exif[270] = "private synthetic metadata"
    stored = await store.stage("sample.jpg", chunks(image_bytes("JPEG", exif=exif)))
    assert stored.mime_type == "image/jpeg"
    assert (stored.width, stored.height) == (16, 12)
    assert b"private synthetic metadata" not in store.read(stored.storage_key)
    assert store.path_for(stored.storage_key).parent == store.root


@pytest.mark.asyncio
@pytest.mark.parametrize("name,data", [
    ("sample.png", b"not an image"),
    ("sample.jpg", image_bytes("PNG")),
    ("small.png", image_bytes(size=(8, 6))),
    ("thin.png", image_bytes(size=(2201, 11))),
    ("../sample.png", image_bytes()),
])
async def test_image_storage_rejects_invalid_inputs(tmp_path, name, data):
    store = PrivateImageStore(tmp_path / "images")
    with pytest.raises(ServiceError) as caught:
        await store.stage(name, chunks(data))
    assert caught.value.code == "invalid_image"
    assert not list(store.root.iterdir())
