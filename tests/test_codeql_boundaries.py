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
    def test_pack_containment_preserves_exact_parent_and_nested_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'synthetic-root'
            for directory in (root, root / 'synthetic-pack'):
                (directory / 'assets').mkdir(parents=True, exist_ok=True)
                for filename in ('.', 'assets/catalogue.json'):
                    with self.subTest(nested_directory=directory != root, filename=filename):
                        actual = webapp.PricingReferencePack._contained_path(root, directory, filename)
                        self.assertEqual(actual, (directory / filename).resolve())

    def test_pack_containment_rejects_directory_and_asset_prefix_siblings_before_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'synthetic-root'
            sibling = Path(tmp) / 'synthetic-root-extra'
            root.mkdir()
            sibling.mkdir()
            original_resolve = Path.resolve
            resolved_root = root.resolve()
            for directory, filename in ((sibling, 'catalogue.json'), (root, '../synthetic-root-extra/catalogue.json')):
                with self.subTest(directory_sibling=directory == sibling):
                    resolved = []
                    def observed_resolve(path, *args, **kwargs):
                        resolved.append(path)
                        return original_resolve(path, *args, **kwargs)
                    with mock.patch.object(Path, 'resolve', observed_resolve):
                        with self.assertRaises(webapp.PricingReferencePathError):
                            webapp.PricingReferencePack._contained_path(root, directory, filename)
                    self.assertTrue(all(original_resolve(path).is_relative_to(resolved_root) for path in resolved),
                                    'Prefix sibling reached Path resolution before rejection')

    def test_pack_normalisation_preserves_unicode_and_rejects_prefix_siblings(self):
        for source in ('local', 'bundled'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'Synthetic-İ-Å-根'
                pack = root / 'synthetic-pack'
                sibling = root / 'synthetic-pack-extra'
                (pack / 'assets').mkdir(parents=True)
                sibling.mkdir()
                catalogue = pack / 'assets' / 'İ-catalogue.json'
                catalogue.write_text('{"items":[{"id":"synthetic-row","description":"Synthetic row","sale_unit_price":1}]}', encoding='utf-8')
                manifest = pack / 'reference.json'
                manifest.write_text(json.dumps({'id': 'synthetic-pack', 'pricing_catalog': 'assets/İ-catalogue.json'}), encoding='utf-8')
                outside = sibling / 'catalogue.json'
                outside.write_text('{"items":[{"id":"synthetic-outside"}]}', encoding='utf-8')
                with mock.patch.object(webapp, 'pricing_references_root', return_value=root), \
                     mock.patch.object(webapp, 'bundled_pricing_references_root', return_value=root):
                    resolved = webapp.PricingReferencePack.resolve_exact('synthetic-pack', source)
                    self.assertIsNotNone(resolved)
                    self.assertEqual(resolved.pricing_catalog_path, catalogue.resolve())
                    self.assertEqual(resolved.public_detail()['items'][0]['id'], 'synthetic-row')
                    manifest.write_text(json.dumps({'id': 'synthetic-pack', 'pricing_catalog': '../synthetic-pack-extra/catalogue.json'}), encoding='utf-8')
                    original_read = Path.read_text
                    def read_inside_selected(path, *args, **kwargs):
                        self.assertNotEqual(path.resolve(), outside.resolve(), 'Prefix sibling was read')
                        return original_read(path, *args, **kwargs)
                    with mock.patch.object(Path, 'read_text', read_inside_selected):
                        self.assertIsNone(webapp.PricingReferencePack.resolve_exact('synthetic-pack', source))
                        with self.assertRaises(webapp.PricingReferencePathError):
                            webapp.PricingReferencePack.resolve('synthetic-pack', source)

    def test_pack_asset_paths_reject_escape_without_substituting_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'synthetic-pack'
            directory.mkdir()
            for key in ('pricing_catalog', 'pricing_reference'):
                for filename in ('../outside.json', str((directory / 'inside.json').resolve())):
                    with self.subTest(key=key, filename_kind='absolute' if Path(filename).is_absolute() else 'relative'):
                        pack = webapp.PricingReferencePack('synthetic-pack', directory, {key: filename})
                        with self.assertRaises(webapp.PricingReferencePathError), \
                             mock.patch.object(Path, 'read_text') as reader:
                            pack.asset_path(key, 'synthetic-default.json')
                        reader.assert_not_called()

    def test_all_pack_read_entrypoints_reject_unsafe_paths_without_fallback(self):
        for source in ('local', 'bundled'):
            for target in ('manifest', 'catalogue'):
                for route in ('resolve', 'summary', 'detail', 'list'):
                    with self.subTest(source=source, target=target, route=route), tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        pack_dir = root / 'synthetic-pack'
                        pack_dir.mkdir()
                        manifest = pack_dir / 'reference.json'
                        catalog = pack_dir / 'pricing-catalog.json'
                        outside = root / 'outside.json'
                        manifest.write_text(json.dumps({'id': 'synthetic-pack', 'pricing_catalog':
                            '../outside.json' if target == 'catalogue' else 'pricing-catalog.json'}), encoding='utf-8')
                        catalog.write_text('{"items":[{"id":"synthetic-default"}]}', encoding='utf-8')
                        outside.write_text('{"id":"synthetic-pack","label":"Synthetic outside sentinel","items":[]}', encoding='utf-8')
                        original_resolve, original_read = Path.resolve, Path.read_text
                        reads = []

                        def resolved(path, *args, **kwargs):
                            if target == 'manifest' and path.name == 'reference.json' and original_resolve(path.parent) == original_resolve(pack_dir):
                                return original_resolve(outside)
                            return original_resolve(path, *args, **kwargs)

                        def contained_read(path, *args, **kwargs):
                            actual = path.resolve()
                            self.assertTrue(actual.is_relative_to(original_resolve(pack_dir)), 'Pack read escaped its selected directory')
                            reads.append(actual)
                            return original_read(path, *args, **kwargs)

                        with mock.patch.object(webapp, 'pricing_references_root', return_value=root), \
                             mock.patch.object(webapp, 'bundled_pricing_references_root', return_value=root), \
                             mock.patch.object(webapp, 'DEFAULT_PRICING_REFERENCE_ID', 'synthetic-pack'), \
                             mock.patch.object(webapp, 'BUNDLED_DEFAULT_PRICING_REFERENCE_ID', 'synthetic-pack'), \
                             mock.patch.object(Path, 'resolve', resolved), \
                             mock.patch.object(Path, 'read_text', contained_read):
                            if route == 'list':
                                listing = webapp.list_local_pricing_references if source == 'local' else webapp.list_bundled_pricing_references
                                self.assertEqual(listing(), [])
                            else:
                                with self.assertRaises(ValueError):
                                    pack = webapp.PricingReferencePack.resolve('synthetic-pack', source)
                                    if route == 'summary': pack.public_summary()
                                    if route == 'detail': pack.public_detail()
                        self.assertNotIn(original_resolve(catalog), reads, 'Unsafe selection must not substitute its default catalogue')

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
                with self.assertRaises(webapp.PricingReferencePathError):
                    webapp.PricingReferencePack.resolve('synthetic-pack', 'local')
                reader.assert_not_called()

    def test_listings_keep_safe_peers_and_do_not_hide_unrelated_failures(self):
        for source in ('local', 'bundled'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name in ('safe-pack', 'unsafe-pack'):
                    directory = root / name
                    directory.mkdir()
                    (directory / 'reference.json').write_text(json.dumps({'id': name,
                        'pricing_catalog': '../outside.json' if name == 'unsafe-pack' else 'pricing-catalog.json'}), encoding='utf-8')
                    (directory / 'pricing-catalog.json').write_text('{"items":[]}', encoding='utf-8')
                with mock.patch.object(webapp, 'pricing_references_root', return_value=root), \
                     mock.patch.object(webapp, 'bundled_pricing_references_root', return_value=root):
                    listing = webapp.list_local_pricing_references if source == 'local' else webapp.list_bundled_pricing_references
                    self.assertEqual([item['id'] for item in listing()], ['safe-pack'])
                    with mock.patch.object(webapp, 'load_pricing_reference_pack', side_effect=RuntimeError('Synthetic unrelated error')):
                        with self.assertRaises(RuntimeError):
                            listing()
