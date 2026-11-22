from io import BytesIO
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from mutagen.flac import FLAC, Picture
from PIL import Image, PngImagePlugin

from core.cover_engine import ArtworkError, existing_artwork, inspect_artwork, prepare_cover
from core.flac_input import inspect_release
from tests.test_read_only_workflow import write_track


def image_bytes(format_name: str, size: tuple[int, int], **options) -> bytes:
    with Image.new("RGB", size, (32, 55, 88)) as image, BytesIO() as stream:
        image.save(stream, format_name, **options)
        return stream.getvalue()


class CoverEngineTests(unittest.TestCase):
    def test_requires_both_axes_and_leaves_a_qualifying_jpeg_unchanged(self):
        with self.assertRaisesRegex(ArtworkError, "999 × 1200"):
            prepare_cover(image_bytes("PNG", (999, 1200)))

        source = image_bytes("JPEG", (1000, 1300))
        cover = prepare_cover(source)
        self.assertEqual((cover.info.format, cover.info.width, cover.info.height), ("JPEG", 1000, 1300))
        self.assertEqual((cover.filename, cover.data), ("cover.jpg", source))

    def test_exact_byte_limit_requires_reencoding_and_retains_pixels(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "")
        overhead = len(image_bytes("PNG", (1000, 1000), pnginfo=metadata))
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "x" * (5_000_000 - overhead))
        source = image_bytes("PNG", (1000, 1000), pnginfo=metadata)

        self.assertEqual(len(source), 5_000_000)
        self.assertFalse(inspect_artwork(source).meets_output_requirements)
        output = prepare_cover(source)
        self.assertEqual(output.filename, "cover.png")
        self.assertLess(len(output.data), 5_000_000)
        with Image.open(BytesIO(source)) as before, Image.open(BytesIO(output.data)) as after:
            self.assertEqual(after.tobytes(), before.tobytes())

    def test_oversized_transparent_art_downscales_without_cropping_or_flattening(self):
        width, height = 1500, 1250
        noise = Random(37).randbytes(width * height * 4)
        with Image.frombytes("RGBA", (width, height), noise) as image, BytesIO() as stream:
            image.save(stream, "PNG")
            source = stream.getvalue()

        self.assertGreater(len(source), 5_000_000)
        cover = prepare_cover(source)
        self.assertTrue(cover.info.meets_output_requirements)
        self.assertLess(cover.info.width, width)
        self.assertLess(cover.info.height, height)
        self.assertAlmostEqual(cover.info.width / cover.info.height, width / height, delta=0.001)
        with Image.open(BytesIO(cover.data)) as output:
            self.assertIn("A", output.getbands())

    def test_corrupt_bytes_cannot_be_a_cover(self):
        with self.assertRaisesRegex(ArtworkError, "Could not decode"):
            prepare_cover(b"<html>unavailable</html>")

    def test_lists_existing_folder_and_selected_embedded_artwork_without_writes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected, sibling = root / "A.flac", root / "B.flac"
            cover = root / "cover.jpg"
            cover_bytes = image_bytes("JPEG", (500, 500))
            cover.write_bytes(cover_bytes)
            for path in (selected, sibling):
                write_track(path, "Trance")
                picture = Picture()
                picture.type = 3
                picture.data = cover_bytes
                audio = FLAC(str(path))
                audio.add_picture(picture)
                audio.save()
            before = {path: path.read_bytes() for path in (selected, sibling, cover)}

            sources, warnings = existing_artwork(inspect_release([selected], release_folder=root))

            self.assertEqual(warnings, ())
            self.assertEqual([source.path for source in sources], [cover, selected])
            self.assertEqual([source.read() for source in sources], [cover_bytes, cover_bytes])
            self.assertEqual({path: path.read_bytes() for path in before}, before)
