"""Profile fields S3 adds or tightens: template choice, logo size and declared type, upload wording."""
import base64
import unittest

from webapp import server as s


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def data_url(mime, raw):
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def profile(**company):
    return {"id": "synthetic-profile", "label": "Synthetic", "defaults": {"company": {"name": "Synthetic Co", **company}}}


class ProfileQuoteTemplateFieldsTest(unittest.TestCase):
    def test_logo_must_be_a_small_png_or_jpeg(self):
        for mime, raw in (("image/png", PNG), ("image/jpeg", JPEG), ("image/jpg", JPEG)):
            with self.subTest(mime=mime):
                saved = s.normalize_profile_payload(profile(logo_data_url=data_url(mime, raw)))
                self.assertTrue(saved["defaults"]["company"]["logo_data_url"])
        for label, url in (
            ("gif", data_url("image/gif", b"GIF89a" + b"\x00" * 32)),
            ("webp", data_url("image/webp", b"RIFF" + b"\x00" * 32)),
            ("not base64", "data:image/png;base64,***"),
            ("svg", data_url("image/svg+xml", b"<svg/>")),
            ("not a data url", "https://example.test/logo.png"),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "Profile logo must be a PNG or JPEG image"):
                s.normalize_profile_payload(profile(logo_data_url=url))
        oversized = data_url("image/png", PNG + b"\x00" * s.MAX_PROFILE_LOGO_BYTES)
        with self.assertRaisesRegex(ValueError, "^Profile logo is larger than 2 MB"):
            s.normalize_profile_payload(profile(logo_data_url=oversized))
        self.assertNotIn("logo_data_url", s.normalize_profile_payload(profile())["defaults"]["company"])

    def test_oversized_profile_files_name_the_profile_file_not_a_pricing_reference(self):
        too_big = data_url("application/octet-stream", b"\x00" * (s.MAX_PROFILE_LAYOUT_BYTES + 1))
        with self.assertRaises(ValueError) as caught:
            s.normalize_profile_payload({**profile(), "pack": {"quotation_layout": {"data_url": too_big}}})
        self.assertTrue(str(caught.exception).startswith("Quote layout file is larger than"), caught.exception)
        self.assertNotIn("Pricing reference", str(caught.exception))
        self.assertTrue(s.decode_data_url_bytes("data:a;base64,AAAA", 10).startswith(b"\x00"))
        with self.assertRaisesRegex(ValueError, "^Pricing reference file is larger than"):
            s.decode_data_url_bytes(data_url("text/plain", b"x" * (2 * 1024 * 1024)), 1024 * 1024)

    def test_template_choice_is_kept_only_when_well_formed(self):
        for choice in ("default", "standard", "tpl-" + "0a" * 16):
            with self.subTest(choice=choice):
                self.assertEqual(s.normalize_profile_payload({**profile(), "quote_template_id": choice})["quote_template_id"], choice)
        for choice in ("", "Standard", "tpl-XYZ", "../tpl", 3, None, ["standard"]):
            with self.subTest(choice=choice), self.assertRaisesRegex(ValueError, "Quote template choice is invalid"):
                s.normalize_profile_payload({**profile(), "quote_template_id": choice})
        self.assertNotIn("quote_template_id", s.normalize_profile_payload(profile()))


if __name__ == "__main__":
    unittest.main()
