"""Offline reference image limits (#384): 48 MiB uploads, header pixel guards and the bounded AI copy."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

from PIL import Image, ImageOps, features

from tests import test_openai_draft_request_contract as draft_contract
from tests.test_pdf_reference_limits import padded_pdf
from webapp import server


ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024


def jpeg_bytes(image, **options):
    stream = io.BytesIO()
    image.save(stream, format="JPEG", **options)
    return stream.getvalue()


def png_bytes(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def data_url(raw, mime="image/jpeg"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def decode_data_url(url):
    return base64.b64decode(url.partition(",")[2], validate=True)


def padded_jpeg(size, base=None):
    """A valid JPEG of exactly size bytes; COM segments after SOI keep the pixels tiny."""
    raw = base or jpeg_bytes(Image.new("RGB", (8, 8), "white"))
    padding = size - len(raw)
    assert padding == 0 or padding >= 4
    segments = []
    while padding:
        chunk = min(padding, 4 + 65533)
        if 0 < padding - chunk < 4:
            chunk -= 4
        segments.append(b"\xff\xfe" + struct.pack(">H", chunk - 2) + b"x" * (chunk - 4))
        padding -= chunk
    padded = raw[:2] + b"".join(segments) + raw[2:]
    assert len(padded) == size
    return padded


def jpeg_with_header_size(width, height):
    """A small real JPEG whose SOF header claims width x height."""
    raw = bytearray(jpeg_bytes(Image.new("RGB", (8, 8), "white")))
    sof = raw.index(b"\xff\xc0")
    raw[sof + 5:sof + 9] = struct.pack(">HH", height, width)
    return bytes(raw)


def compressible_png(width, height, *, alpha=False):
    """Stream a flat PNG of any size without holding its pixels in memory."""
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    channels = 4 if alpha else 3
    compressor = zlib.compressobj(level=9)
    compressed = bytearray()
    scanline = b"\0" + b"\x80" * (width * channels)
    for _ in range(height):
        compressed.extend(compressor.compress(scanline))
    compressed.extend(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", bytes(compressed)) + chunk(b"IEND", b"")


def phone_photo(orientation=6):
    """A real-sized 12.2 MP noise photo (about 11 to 14 MiB) stored landscape with an EXIF rotation."""
    exif = Image.Exif()
    exif[0x0112] = orientation
    return jpeg_bytes(Image.effect_noise((4032, 3024), 64).convert("RGB"), quality=95, exif=exif)


class ReferenceImageLimitsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("Network disabled")))
        server._AI_IMAGE_COPIES.clear()
        self.addCleanup(server._AI_IMAGE_COPIES.clear)

    def draft_fixture(self):
        fixture = draft_contract.DraftRequestContractTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def read_json(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        handler = object.__new__(server.QuoteRunnerHandler)
        handler.path = path
        handler.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        return handler.read_json()

    def test_limits_match_the_pdf_ceiling_and_ai_copy_matches_pdf_pages(self):
        self.assertEqual(server.MAX_IMAGE_BYTES, 48 * MIB)
        self.assertEqual(server.MAX_IMAGE_BYTES, server.MAX_PDF_BYTES)
        self.assertEqual(server.MAX_AI_IMAGE_BYTES, server.MAX_RENDERED_PDF_PAGE_BYTES)
        self.assertEqual(server.MAX_AI_IMAGE_BYTES, MIB)
        self.assertEqual(server.AI_IMAGE_LONG_EDGE_PX, server.PDF_RENDER_TARGET_LONG_EDGE_PX)
        self.assertEqual(server.AI_IMAGE_LONG_EDGE_PX, 1600)
        self.assertEqual(server.MAX_SOURCE_JPEG_PIXELS, 89_478_485)
        self.assertEqual(server.MAX_SOURCE_LOSSLESS_PIXELS, 40_000_000)
        self.assertEqual((server.MAX_REFERENCE_IMAGES, server.MAX_DRAFT_SOURCE_BYTES), (8, 128 * MIB))

    def test_real_sized_phone_photo_reaches_ai_upright_and_bounded_while_original_is_stored(self):
        raw = phone_photo()
        self.assertGreater(len(raw), 10 * MIB)
        upload = data_url(raw)
        fixture = self.draft_fixture()
        sent = fixture.capture(fixture.payload(upload))["input"][0]["content"][1]
        self.assertEqual(sent["detail"], "high")
        self.assertNotEqual(sent["image_url"], upload)
        copy = decode_data_url(sent["image_url"])
        self.assertLessEqual(len(copy), server.MAX_AI_IMAGE_BYTES)
        with Image.open(io.BytesIO(copy)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (1200, 1600))
            self.assertNotIn(0x0112, image.getexif())
        record = {"name": "phone.jpg", "data_url": upload, "session_file_key": "phone",
                  "file_role": "reference", "size": len(raw)}
        stored = server.quote_session_draft_file_record(record)
        self.assertEqual(hashlib.sha256(decode_data_url(stored["data_url"])).hexdigest(),
                         hashlib.sha256(raw).hexdigest())

    def test_exif_rotation_matches_pillow_for_every_orientation(self):
        base = Image.new("RGB", (40, 20), "white")
        base.paste((255, 0, 0), (0, 0, 10, 10))
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                exif = Image.Exif()
                exif[0x0112] = orientation
                raw = jpeg_bytes(base, quality=95, exif=exif)
                with Image.open(io.BytesIO(raw)) as original:
                    expected = ImageOps.exif_transpose(original).convert("RGB")
                with Image.open(io.BytesIO(decode_data_url(server.ai_image_data_url_from_bytes(raw)))) as copy:
                    copy = copy.convert("RGB")
                    self.assertEqual(copy.size, expected.size)
                    self.assertEqual(copy.getpixel((2, 2))[0] > 200, expected.getpixel((2, 2))[0] > 200)
                    self.assertEqual(copy.getpixel((2, 2))[1] < 80, expected.getpixel((2, 2))[1] < 80)

    def test_48_mib_boundary_and_encoded_length_precheck(self):
        for size in (server.MAX_IMAGE_BYTES - 1, server.MAX_IMAGE_BYTES, server.MAX_IMAGE_BYTES + 1):
            with self.subTest(size=size):
                url = data_url(padded_jpeg(size))
                if size > server.MAX_IMAGE_BYTES:
                    with self.assertRaises(server.OpenAIAnalysisError) as caught:
                        server.validate_draft_references({"images": [{"name": "large.jpg", "data_url": url}]})
                    self.assertEqual(caught.exception.diagnostics["request_validation_reason"], "media_byte_limit")
                    continue
                self.assertEqual(server.validate_draft_media(url, upload=True), "image/jpeg")
                copy = decode_data_url(server.ai_image_copy_for_upload(decode_data_url(url)))
                self.assertLessEqual(len(copy), server.MAX_AI_IMAGE_BYTES)
        over = data_url(padded_jpeg(server.MAX_IMAGE_BYTES + 4))
        with mock.patch.object(server.base64, "b64decode") as decode, self.assertRaises(server.OpenAIAnalysisError) as caught:
            server.validate_draft_media(over, upload=True)
        decode.assert_not_called()
        self.assertEqual(caught.exception.diagnostics["request_validation_reason"], "media_byte_limit")

    def test_eight_large_images_fill_128_mib_through_request_and_draft(self):
        each = server.MAX_DRAFT_SOURCE_BYTES // server.MAX_REFERENCE_IMAGES
        urls = [data_url(padded_jpeg(each, jpeg_bytes(Image.new("RGB", (2000 + n, 1500), (n * 20, 90, 160)))))
                for n in range(server.MAX_REFERENCE_IMAGES)]
        fixture = self.draft_fixture()
        payload = fixture.payload(*urls) | {"_pricing_reference_snapshot": None}
        self.assertEqual(self.read_json("/api/draft", payload)["images"][7]["data_url"], urls[7])
        checkpoint = {"draft_files": [{"name": f"r{n}.jpg", "data_url": url, "session_file_key": f"r{n}",
                                       "file_role": "reference"} for n, url in enumerate(urls)]}
        self.assertEqual(len(self.read_json("/api/quote-sessions", checkpoint)["draft_files"]), 8)
        parts = fixture.capture(fixture.payload(*urls))["input"][0]["content"]
        images = [part for part in parts if part["type"] == "input_image"]
        self.assertEqual(len(images), 8)
        for part in images:
            self.assertLessEqual(len(decode_data_url(part["image_url"])), server.MAX_AI_IMAGE_BYTES)
        # One byte over the 128 MiB total: refused at the request and at validation.
        over = urls[:-1] + [data_url(padded_jpeg(each + 1, jpeg_bytes(Image.new("RGB", (8, 8)))))]
        with self.assertRaises(server.RequestBodyError) as caught:
            self.read_json("/api/draft", fixture.payload(*over) | {"_pricing_reference_snapshot": None})
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(server.DraftBatchError) as caught:
            server.validate_draft_references({"images": [{"name": f"r{n}", "data_url": url} for n, url in enumerate(over)]})
        self.assertEqual(caught.exception.diagnostics["batch_failure_reason"], "source_byte_limit")

    def test_mixed_pdf_and_images_share_the_128_mib_allowance(self):
        images = [data_url(padded_jpeg(16 * MIB)) for _ in range(5)]
        payload = {"images": [{"name": "large.pdf", "data_url": padded_pdf(48 * MIB)}]
                   + [{"name": f"r{n}.jpg", "data_url": url} for n, url in enumerate(images)]}
        self.assertEqual(len(self.read_json("/api/draft", payload)["images"]), 6)
        payload["images"].append({"name": "one-more.jpg", "data_url": data_url(padded_jpeg(1024))})
        with self.assertRaises(server.RequestBodyError) as caught:
            self.read_json("/api/draft", payload)
        self.assertEqual(caught.exception.status, 413)

    def test_request_allowance_counts_reference_images_only(self):
        url = data_url(padded_jpeg(2 * MIB))
        encoded = len(url) - len("data:image/jpeg;base64,")
        for path, payload in (
            ("/api/draft", {"images": [{"name": "r.jpg", "data_url": url}], "notes": "n"}),
            ("/api/quote-sessions", {"draft_files": [{"name": "r.jpg", "data_url": url, "session_file_key": "r",
                                                      "file_role": "reference"}], "notes": "n"}),
        ):
            length = len(json.dumps(payload).encode("utf-8"))
            with self.subTest(path=path), mock.patch.object(server, "MAX_REQUEST_BYTES", length - encoded):
                self.assertTrue(server.reference_media_request_fits_budget(path, payload, length))
                self.assertFalse(server.reference_media_request_fits_budget(path, payload, length + 1))
        logo = {"draft_files": [{"name": "logo.jpg", "data_url": url, "session_file_key": "logo", "file_role": "logo"}]}
        self.assertFalse(server.reference_media_request_fits_budget(
            "/api/quote-sessions", logo, len(json.dumps(logo)) + server.MAX_REQUEST_BYTES))
        corrupt = {"images": [{"name": "r.png", "data_url": data_url(b"\x89PNG\r\n\x1a\ncorrupt", "image/png")}]}
        self.assertFalse(server.reference_media_request_fits_budget("/api/draft", corrupt, 1))

    def test_pixel_guards_check_the_header_before_any_decode(self):
        allowed = server.prompt_image_dimensions_are_allowed
        self.assertTrue(allowed((89_478_485, 1), "JPEG"))
        self.assertFalse(allowed((89_478_486, 1), "JPEG"))
        for image_format in ("PNG", "WEBP"):
            self.assertTrue(allowed((40_000_000, 1), image_format))
            self.assertFalse(allowed((40_000_001, 1), image_format))
            self.assertTrue(allowed((8000, 5000), image_format))
            self.assertFalse(allowed((8000, 5001), image_format))
        over = [("JPEG", jpeg_with_header_size(14351, 6236)), ("PNG", compressible_png(8000, 5001))]
        if features.check("webp"):
            stream = io.BytesIO()
            Image.new("RGB", (8000, 5001), "white").save(stream, format="WEBP", lossless=True, method=0)
            over.append(("WEBP", stream.getvalue()))
        for image_format, raw in over:
            with self.subTest(format=image_format), mock.patch.object(
                Image.Image, "load", side_effect=AssertionError("over-limit image reached pixel decode")
            ):
                mime = "image/" + image_format.lower()
                with self.assertRaises(server.OpenAIAnalysisError) as caught:
                    server.validate_draft_media(data_url(raw, mime), upload=True)
                self.assertEqual(caught.exception.diagnostics["request_validation_reason"], "media_dimension_limit")
                with self.assertRaises(server.OpenAIAnalysisError):
                    server.validate_draft_media(data_url(raw, mime), header_only=True)
        message = server.failed_result_payload("ERR-IMAGE-LIMIT", exception=caught.exception)["errors"][0]
        self.assertIn("89 megapixels", message)
        self.assertIn("40 megapixels", message)
        self.assertIn("ERR-IMAGE-LIMIT", message)

    def test_pixel_guards_admit_images_exactly_at_the_cap(self):
        jpeg = jpeg_bytes(Image.linear_gradient("L").resize((14351, 6235)), quality=50)
        self.assertEqual(14351 * 6235, server.MAX_SOURCE_JPEG_PIXELS)
        cases = [("image/jpeg", jpeg), ("image/png", compressible_png(8000, 5000))]
        if features.check("webp"):
            stream = io.BytesIO()
            Image.new("RGB", (8000, 5000), "white").save(stream, format="WEBP", lossless=True, method=0)
            cases.append(("image/webp", stream.getvalue()))
        for mime, raw in cases:
            with self.subTest(mime=mime):
                self.assertEqual(server.validate_draft_media(data_url(raw, mime), upload=True), mime)
                with Image.open(io.BytesIO(decode_data_url(server.ai_image_copy_for_upload(raw)))) as copy:
                    self.assertEqual(max(copy.size), server.AI_IMAGE_LONG_EDGE_PX)

    def test_pass_through_only_for_small_upright_images(self):
        small = jpeg_bytes(Image.new("RGB", (1600, 900), (30, 60, 90)))
        exact = padded_jpeg(server.MAX_AI_IMAGE_BYTES, small)
        self.assertEqual(server.ai_image_data_url_from_bytes(exact), data_url(exact))
        cases = {
            "one byte over": padded_jpeg(server.MAX_AI_IMAGE_BYTES + 1, small),
            "1601 px": jpeg_bytes(Image.new("RGB", (1601, 900), (30, 60, 90))),
        }
        exif = Image.Exif()
        exif[0x0112] = 6
        cases["rotated"] = jpeg_bytes(Image.new("RGB", (160, 90), (30, 60, 90)), exif=exif)
        for label, raw in cases.items():
            with self.subTest(case=label):
                copy = server.ai_image_data_url_from_bytes(raw)
                self.assertNotEqual(copy, data_url(raw))
                with Image.open(io.BytesIO(decode_data_url(copy))) as image:
                    self.assertLessEqual(max(image.size), server.AI_IMAGE_LONG_EDGE_PX)
                    self.assertEqual(image.size[0] < image.size[1], label == "rotated")

    def test_transparent_png_is_flattened_onto_white(self):
        image = Image.new("RGBA", (2400, 30), (0, 0, 0, 0))
        image.paste((200, 0, 0, 255), (0, 0, 100, 30))
        copy = decode_data_url(server.ai_image_data_url_from_bytes(png_bytes(image)))
        with Image.open(io.BytesIO(copy)) as decoded:
            self.assertEqual((decoded.format, decoded.mode, decoded.size), ("JPEG", "RGB", (1600, 20)))
            self.assertTrue(all(channel > 240 for channel in decoded.getpixel((1500, 10))))
            self.assertGreater(decoded.getpixel((10, 10))[0], 150)

    def test_outbound_oracle_refuses_any_high_detail_image_over_the_ai_copy_limits(self):
        fixture = self.draft_fixture()
        valid = fixture.capture()
        oversized = [
            data_url(padded_jpeg(server.MAX_AI_IMAGE_BYTES + 1)),
            data_url(jpeg_bytes(Image.new("RGB", (server.AI_IMAGE_LONG_EDGE_PX + 1, 2)))),
        ]
        for url in oversized:
            body = json.loads(json.dumps(valid))
            body["input"][0]["content"][1]["image_url"] = url
            with self.subTest(size=len(url)), self.assertRaises(server.OpenAIAnalysisError):
                server.validate_draft_responses_envelope(body, "standard")
        body = json.loads(json.dumps(valid))
        body["input"][0]["content"][1]["image_url"] = data_url(jpeg_bytes(Image.new("RGB", (1600, 2))))
        server.validate_draft_responses_envelope(body, "standard")

    def test_each_upload_is_decoded_once_per_draft(self):
        fixture = self.draft_fixture()
        urls = [data_url(jpeg_bytes(Image.new("RGB", (2400, 1800), color))) for color in ("red", "blue")]
        with mock.patch.object(server, "ai_image_data_url_from_bytes", wraps=server.ai_image_data_url_from_bytes) as normalise:
            payload = fixture.payload(*urls)
            server.validate_draft_references(payload)
            fixture.capture(payload)
            server.validate_draft_references(payload)
        self.assertEqual(normalise.call_count, 2)

    def test_failed_normalise_after_validation_never_sends_the_original(self):
        fixture = self.draft_fixture()
        url = data_url(jpeg_bytes(Image.new("RGB", (2400, 1800), "red")))
        server.validate_draft_references(fixture.payload(url))
        server._AI_IMAGE_COPIES.clear()
        with mock.patch.object(server, "ai_image_data_url_from_bytes", side_effect=server.ReferenceImageError("media_decode_failed")):
            with self.assertRaises(server.DraftBatchError) as caught:
                server.prepare_draft_media(fixture.payload(url)["images"])
        self.assertEqual(caught.exception.diagnostics["batch_failure_reason"], "image_normalise")
        self.assertEqual(caught.exception.diagnostics["source_number"], 1)

    def test_cache_is_bounded(self):
        for n in range(3 * server.MAX_REFERENCE_IMAGES):
            server.ai_image_copy_for_upload(jpeg_bytes(Image.new("RGB", (4, 4), (n, 0, 0))))
        self.assertEqual(len(server._AI_IMAGE_COPIES), 2 * server.MAX_REFERENCE_IMAGES)

    def test_normaliser_memory_stays_bounded_at_the_caps(self):
        # VmHWM is this process's own peak; ru_maxrss would carry the test
        # runner's peak across exec on Linux.
        probe = (
            "import sys\n"
            "from webapp import server\n"
            "def peak():\n"
            "    for line in open('/proc/self/status'):\n"
            "        if line.startswith('VmHWM:'):\n"
            "            return int(line.split()[1])\n"
            "raw = open(sys.argv[1], 'rb').read()\n"
            "before = peak()\n"
            "server.ai_image_data_url_from_bytes(raw)\n"
            "print((peak() - before) // 1024)\n"
        )
        if not Path("/proc/self/status").exists():
            self.skipTest("Peak memory needs Linux /proc.")
        cases = (
            ("jpeg-89mp.jpg", jpeg_bytes(Image.linear_gradient("L").resize((14351, 6235)).convert("RGB"), quality=50), 128),
            ("rgba-40mp.png", compressible_png(8000, 5000, alpha=True), 450),
        )
        with tempfile.TemporaryDirectory() as folder:
            for name, raw, limit_mib in cases:
                path = Path(folder) / name
                path.write_bytes(raw)
                result = subprocess.run([sys.executable, "-c", probe, str(path)], cwd=ROOT, capture_output=True,
                                        text=True, check=True, timeout=120, env={**os.environ, "PYTHONPATH": str(ROOT)})
                growth_mib = int(result.stdout.strip().splitlines()[-1])
                print(f"normaliser peak growth {name}: {growth_mib} MiB", file=sys.stderr)
                with self.subTest(case=name, growth_mib=growth_mib):
                    self.assertLess(growth_mib, limit_mib)

    def test_visible_copy_and_docs_show_48_mib_images(self):
        html = (ROOT / "webapp/static/index.html").read_text(encoding="utf-8")
        js = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")
        self.assertIn("Up to 8 references, up to 48 MiB each and 128 MiB in total.", html)
        self.assertNotIn("12 MiB", html + js)
        self.assertIn("const MAX_REFERENCE_IMAGE_MIB = 48;", js)
        self.assertIn("Choose files up to ${MAX_REFERENCE_IMAGE_MIB} MiB each.", js)
        api = (ROOT / "docs/quotation-api-v1.md").read_text(encoding="utf-8")
        self.assertIn("48 MiB per image", api)
        self.assertIn("1600 px", api)


if __name__ == "__main__":
    unittest.main()
