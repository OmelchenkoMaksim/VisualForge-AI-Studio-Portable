import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'App/server'))

from range_response import parse_range, serve_file  # noqa: E402


class FakeHandler:
    def __init__(self, range_header=None):
        self.headers = {'Range': range_header} if range_header else {}
        self.sent, self.status, self.wfile, self.answer = [], None, io.BytesIO(), None

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.sent.append((name, value))

    def end_headers(self):
        pass

    def response_json(self, code, data):
        self.answer = (code, data)


class RangeTests(unittest.TestCase):
    def serve(self, range_header=None, **options):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'clip.mp4'
            path.write_bytes(bytes(range(100)))
            handler = FakeHandler(range_header)
            serve_file(handler, path, 'video/mp4', 'My "clip".mp4', **options)
        return handler, dict(handler.sent)

    def test_the_whole_file_without_a_range(self):
        handler, headers = self.serve()
        self.assertEqual((handler.status, handler.wfile.getvalue()), (200, bytes(range(100))))
        self.assertEqual((headers['Content-Length'], headers['Accept-Ranges'], headers['Content-Type']), ('100', 'bytes', 'video/mp4'))
        self.assertNotIn('Content-Range', headers)
        self.assertNotIn('Cache-Control', headers)
        self.assertEqual(headers['Content-Disposition'], 'inline; filename="My clip.mp4"')        # a quote in the name would break the header

    def test_ranges_give_206_with_the_right_bytes(self):
        for header, expected, content_range in (('bytes=10-19', bytes(range(10, 20)), 'bytes 10-19/100'), ('bytes=90-', bytes(range(90, 100)), 'bytes 90-99/100'),
                                                ('bytes=-5', bytes(range(95, 100)), 'bytes 95-99/100'), ('bytes=0-0', b'\x00', 'bytes 0-0/100'),
                                                ('bytes=50-500', bytes(range(50, 100)), 'bytes 50-99/100')):
            with self.subTest(header=header):
                handler, headers = self.serve(header)
                self.assertEqual((handler.status, handler.wfile.getvalue(), headers['Content-Range']), (206, expected, content_range))
                self.assertEqual(headers['Content-Length'], str(len(expected)))

    def test_a_bad_range_is_416_and_nothing_is_sent(self):
        for header in ('bytes=', 'bytes=-', 'items=0-5', 'bytes=200-300', 'bytes=20-10', 'bytes=a-b'):
            with self.subTest(header=header):
                handler, _ = self.serve(header)
                self.assertEqual(handler.answer[0], 416)
                self.assertEqual(handler.wfile.getvalue(), b'')

    def test_the_cache_header_is_sent_only_when_asked_for(self):
        _, headers = self.serve(cache_control='no-store')
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_parse_range_alone(self):
        self.assertEqual(parse_range('bytes=0-9', 10), (0, 9))
        self.assertIsNone(parse_range('bytes=10-', 10))


if __name__ == '__main__':
    unittest.main()
