import io
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))

from PIL import Image  # noqa: E402

import image_convert  # noqa: E402
from image_convert import ImageConvertError, Profile, convert  # noqa: E402


def encode(image, fmt, **options):
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def picture(size=(64, 48), color=(200, 40, 40)):
    return Image.new('RGB', size, color)


class FormatTests(unittest.TestCase):
    def test_png_inside_the_limits_is_returned_untouched(self):
        raw = encode(picture(), 'PNG')
        result = convert(raw, 'video')
        self.assertTrue(result.unchanged)
        self.assertEqual((result.data, result.format, result.width, result.height), (raw, 'PNG', 64, 48))

    def test_every_common_format_becomes_a_png_for_video(self):
        base = picture((80, 60))
        cases = {'JPEG': encode(base, 'JPEG'), 'WEBP': encode(base, 'WEBP'), 'BMP': encode(base, 'BMP'), 'GIF': encode(base.convert('P'), 'GIF'),
                 'TIFF': encode(base, 'TIFF'), 'TGA': encode(base, 'TGA'), 'ICO': encode(base.resize((32, 32)), 'ICO'), 'PPM': encode(base, 'PPM'),
                 'AVIF': encode(base, 'AVIF'), 'JPEG2000': encode(base, 'JPEG2000'), 'DDS': encode(base, 'DDS')}
        for name, raw in cases.items():
            with self.subTest(source=name):
                result = convert(raw, 'video')
                self.assertEqual(result.format, 'PNG')
                self.assertFalse(result.unchanged)
                with Image.open(io.BytesIO(result.data)) as check:
                    self.assertEqual((check.format, check.mode), ('PNG', 'RGB'))
                self.assertEqual(result.source['format'], name)

    def test_the_image_profile_keeps_jpeg_and_webp_and_converts_the_rest(self):
        base = picture((80, 60))
        for name in ('JPEG', 'WEBP', 'PNG'):
            self.assertTrue(convert(encode(base, name), 'image').unchanged, name)
        self.assertEqual(convert(encode(base, 'BMP'), 'image').format, 'PNG')

    def test_the_file_decides_not_the_name_or_the_declared_type(self):
        raw = encode(picture(), 'JPEG')                         # a JPEG that would arrive as "photo.png"
        self.assertEqual(convert(raw, 'video').format, 'PNG')
        self.assertTrue(convert(raw, 'chat').unchanged)


class ModeTests(unittest.TestCase):
    def test_transparency_is_flattened_for_video_and_kept_for_the_image_section(self):
        raw = encode(Image.new('RGBA', (40, 40), (255, 0, 0, 0)), 'PNG')
        flat = convert(raw, 'video')
        with Image.open(io.BytesIO(flat.data)) as check:
            self.assertEqual((check.mode, check.getpixel((5, 5))), ('RGB', image_convert.NEUTRAL))
        self.assertIn('transparency flattened on grey', flat.changes)
        kept = convert(raw, 'image')
        self.assertTrue(kept.unchanged)
        with Image.open(io.BytesIO(kept.data)) as check:
            self.assertEqual(check.mode, 'RGBA')

    def test_palette_cmyk_grey_and_16_bit_pictures_become_rgb(self):
        palette = Image.new('P', (20, 20))
        palette.putpalette([255, 0, 0] * 256)
        cmyk = Image.new('CMYK', (20, 20), (0, 255, 255, 0))
        grey16 = Image.new('I;16', (20, 20), 40000)
        for name, image, fmt in (('palette', palette, 'PNG'), ('cmyk', cmyk, 'JPEG'), ('grey16', grey16, 'PNG'), ('grey', Image.new('L', (20, 20), 90), 'PNG')):
            with self.subTest(source=name):
                result = convert(encode(image, fmt), 'video')
                with Image.open(io.BytesIO(result.data)) as check:
                    self.assertEqual(check.mode, 'RGB')
        with Image.open(io.BytesIO(convert(encode(cmyk, 'JPEG'), 'video').data)) as check:
            red, green, blue = check.getpixel((3, 3))
            self.assertGreater(red, 150)                       # a CMYK red stays red
            self.assertLess(max(green, blue), 120)

    def test_an_animation_gives_its_first_frame(self):
        first, second = picture((30, 30), (255, 0, 0)), picture((30, 30), (0, 0, 255))
        raw = encode(first, 'GIF', save_all=True, append_images=[second], duration=100, loop=0)
        result = convert(raw, 'video')
        self.assertIn('first frame of an animation', result.changes)
        with Image.open(io.BytesIO(result.data)) as check:
            self.assertGreater(check.getpixel((3, 3))[0], 200)

    def test_exif_rotation_is_applied(self):
        exif = Image.Exif()
        exif[0x0112] = 6                                       # turned a quarter: the picture must come out 20 wide and 60 high
        raw = encode(Image.new('RGB', (60, 20), (10, 200, 10)), 'JPEG', exif=exif)
        result = convert(raw, 'image')
        self.assertEqual((result.width, result.height), (20, 60))
        self.assertIn('EXIF rotation applied', result.changes)


class LimitTests(unittest.TestCase):
    def test_a_long_side_over_the_limit_is_scaled_down(self):
        result = convert(encode(picture((9000, 40)), 'PNG'), 'video')
        self.assertEqual((result.width, result.height), (8192, 36))
        self.assertTrue(any(change.startswith('scaled') for change in result.changes))
        chat = convert(encode(picture((3000, 1500)), 'PNG'), 'chat')
        self.assertEqual(max(chat.width, chat.height), 2560)

    def test_a_picture_over_the_byte_limit_is_compressed_then_shrunk(self):
        noise = Image.frombytes('RGB', (300, 300), os.urandom(300 * 300 * 3))
        raw = encode(noise, 'PNG')
        tight = Profile(keep=('PNG', 'JPEG'), max_bytes=len(raw) // 3, allow_jpeg=True)
        result = convert(raw, tight)
        self.assertLessEqual(len(result.data), tight.max_bytes)
        self.assertEqual(result.format, 'JPEG')
        with self.assertRaises(ImageConvertError):
            convert(raw, Profile(max_bytes=200))

    def test_a_bomb_is_refused_before_it_is_decoded(self):
        old = Image.MAX_IMAGE_PIXELS
        try:
            raw = encode(picture((3000, 2000)), 'PNG')
            with unittest.mock.patch.object(image_convert, 'MAX_PIXELS', 1_000_000):
                with self.assertRaises(ImageConvertError) as caught:
                    convert(raw, 'video')
            self.assertIn('250', str(caught.exception))
        finally:
            Image.MAX_IMAGE_PIXELS = old


class ConsumerTests(unittest.TestCase):
    """The places a picture enters the studio take any format through the converter."""
    def test_a_video_frame_may_be_any_format_and_is_stored_as_png(self):
        import base64
        import tempfile
        from video.video_service import Jobs
        for name in ('WEBP', 'JPEG', 'BMP', 'GIF', 'AVIF', 'TIFF'):
            raw = encode(picture((50, 40)).convert('P') if name == 'GIF' else picture((50, 40)), name)
            value = 'data:image/%s;base64,%s' % (name.lower(), base64.b64encode(raw).decode())
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'first.png'
                digest = Jobs.image(None, value, target)
                self.assertEqual(target.read_bytes()[:4], bytes([0x89]) + b'PNG', name)
                self.assertEqual(len(digest), 64)
        for bad in ('data:image/png;base64,AAAA', 'not a data url', None, 'data:image/png;base64,%%%'):
            with self.subTest(bad=str(bad)[:20]), self.assertRaises(ValueError):
                Jobs.image(None, bad, None)

    def test_the_image_check_of_the_bridge_and_the_chat_store_take_other_formats(self):
        import base64
        from text.chat_store import decode_image
        raw = encode(picture((90, 70)), 'WEBP')
        record = decode_image('data:image/webp;base64,' + base64.b64encode(raw).decode(), 'photo.webp')
        self.assertIn(record['mime'], ('image/png', 'image/jpeg'))
        self.assertEqual((record['width'], record['height']), (90, 70))
        with self.assertRaises(ValueError):
            decode_image('data:image/webp;base64,' + base64.b64encode(b'garbage garbage garbage garbage garbage').decode())


HEIC_PROBE = '''import sys, io
sys.path.insert(0, %r)
try:
    import pillow_heif
except ImportError:
    print("NOPLUGIN")
    raise SystemExit
from PIL import Image
import image_convert
pillow_heif.register_heif_opener()
buffer = io.BytesIO()
Image.new("RGB", (120, 80), (30, 90, 160)).save(buffer, "HEIF")
result = image_convert.convert(buffer.getvalue(), "video")
print(result.format, result.width, result.height, result.source["format"])
'''


class HeicTests(unittest.TestCase):
    """HEIC needs pillow-heif, which lives in the Image engine's Python (the bridge); the test runs the converter there."""
    def test_a_heic_photo_is_converted_by_the_bridge_python(self):
        import subprocess
        python = Path(__file__).resolve().parents[1] / 'Runtime/ComfyUI_windows_portable/python_embeded/python.exe'
        if not python.is_file():
            self.skipTest('the Image runtime is not installed')
        code = HEIC_PROBE % str(Path(__file__).resolve().parents[1] / 'App/server')
        output = subprocess.run([str(python), '-c', code], capture_output=True, text=True, timeout=120).stdout.strip()
        if output == 'NOPLUGIN':
            self.skipTest('the HEIC component is not installed')
        self.assertEqual(output, 'PNG 120 80 HEIF')


class ErrorTests(unittest.TestCase):
    def test_garbage_and_truncated_files_are_refused_with_a_message(self):
        for raw in (b'', b'not a picture at all', encode(picture(), 'PNG')[:60]):
            with self.subTest(size=len(raw)), self.assertRaises(ImageConvertError):
                convert(raw, 'video')

    def test_a_heic_file_without_the_plugin_gets_its_own_hint(self):
        try:
            import pillow_heif  # noqa: F401
        except ImportError:
            pillow_heif = None
        if pillow_heif is not None:
            self.skipTest('the HEIC plugin is installed here')
        heic = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic' + bytes(40)
        with self.assertRaises(ImageConvertError) as caught:
            convert(heic, 'video')
        self.assertIn('HEIC', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
