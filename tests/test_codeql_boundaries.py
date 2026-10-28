"""Preserve exact pricing-pack and parser boundaries under hostile inputs."""
import json
import math
from pathlib import Path
import random
import re
import tempfile
import unittest
from unittest import mock

from tests import test_webapp as support

webapp = support.webapp


class CodeqlBoundaryTest(unittest.TestCase):
    def test_linear_number_and_key_admission_matches_predecessor(self):
        rng = random.Random(882)
        alphabet = '  \t\r\n\u2003-+,.0123456789abc\u0661\u0968'
        values = [None, False, 0, 1, -1, '', '  -1,234.50 sqm', '\u2003\u0661\u0662.5 nos', '1,.3',
                  '1e2', 'NaN', 'Infinity', *(''.join(rng.choices(alphabet, k=rng.randrange(50))) for _ in range(500))]
        for value in values:
            match = re.match(r'\s*(-?\d[\d,]*(?:\.\d+)?)', str(value or ''))
            try:
                expected = float(match.group(1).replace(',', '')) if match else None
            except ValueError:
                expected = None
            if expected is not None and not math.isfinite(expected):
                expected = None
            self.assertEqual(webapp.leading_number(value), expected)
        keys = ['', '-', 'a--b', 'a-b', 'a0-9b', 'a-', '-a', 'A', 'a_b', 'a\n', '\u0661',
                'constructor', 'prototype', 'a' * 80, 'a' * 81,
                *(''.join(rng.choices('ab09-ABC_\u0661', k=rng.randrange(20))) for _ in range(500))]
        for key in keys:
            admitted = bool(1 <= len(key) <= 80 and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', key)
                            and key not in webapp.QUOTE_BASIS_UNSAFE_KEYS)
            if admitted:
                self.assertEqual(webapp.canonical_quote_basis({key: 'synthetic'}), {key: 'synthetic'})
            else:
                with self.assertRaises(ValueError):
                    webapp.canonical_quote_basis({key: 'synthetic'})
        for raw, expected in [('0001', 1), ('0' * 200000 + '42', 42), ('0' * 200000, None),
                              ('1' * 200000, None), ('9007199254740991', 9007199254740991),
                              ('9007199254740992', None), ('\u0661', None), ('1\n2', None),
                              ('\t0042\r\n', 42), ('\u200342', None), ('+1', None)]:
            self.assertEqual(webapp.canonical_primary_order_value(raw), expected)
        self.assertEqual(webapp.leading_number(' ' * 200000 + '3.5 sqm'), 3.5)

    def test_exact_pack_manifest_cannot_resolve_outside_selected_pack(self):
        for source in ('local', 'bundled'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pack = root / 'synthetic-pack'
                pack.mkdir()
                manifest = pack / 'reference.json'
                outside = root / 'outside.json'
                manifest.write_text(json.dumps({'id': 'synthetic-pack'}), encoding='utf-8')
                outside.write_text(json.dumps({'id': 'synthetic-pack'}), encoding='utf-8')
                (pack / 'pricing-catalog.json').write_text('{"items":[{"id":"synthetic-row"}]}', encoding='utf-8')
                original_resolve = Path.resolve
                original_read = Path.read_text
                reads = []

                def resolved(path, *args, **kwargs):
                    escaped_manifest = path.name == 'reference.json' and original_resolve(path.parent) == original_resolve(pack)
                    return original_resolve(outside if escaped_manifest else path, *args, **kwargs)

                def contained_read(path, *args, **kwargs):
                    resolved_path = path.resolve()
                    self.assertTrue(resolved_path.is_relative_to(original_resolve(pack)),
                                    'Exact pack reader attempted an escaped manifest read')
                    reads.append(resolved_path)
                    return original_read(path, *args, **kwargs)

                with mock.patch.object(webapp, 'pricing_references_root', return_value=root), \
                     mock.patch.object(webapp, 'bundled_pricing_references_root', return_value=root), \
                     mock.patch.object(Path, 'resolve', resolved), \
                     mock.patch.object(Path, 'read_text', contained_read):
                    self.assertIsNone(webapp.PricingReferencePack.resolve_exact('synthetic-pack', source))
                self.assertEqual(reads, [])

    def test_exact_pack_resolution_errors_refuse_before_read(self):
        for failure in (OSError('Synthetic resolution failure'), RuntimeError('Synthetic link loop')):
            with self.subTest(failure=type(failure).__name__), \
                 mock.patch.object(Path, 'resolve', side_effect=failure), \
                 mock.patch.object(Path, 'read_text') as reader:
                self.assertIsNone(webapp.PricingReferencePack.resolve_exact('synthetic-pack', 'local'))
                reader.assert_not_called()
