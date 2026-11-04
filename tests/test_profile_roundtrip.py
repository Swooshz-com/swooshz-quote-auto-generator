"""Reusable profile operations preserve quotations and carry their own layout."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import test_webapp as support

webapp = support.webapp


class ProfileRoundtripTest(unittest.TestCase):
    def test_import_preserves_quote_state_and_invalidates_old_exports(self):
        script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('webapp/static/app.js', 'utf8');
function extract(name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name);
  const body = source.indexOf(') {', start) + 2;
  let depth = 0;
  for (let i = body; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw Error('Unclosed function');
}
const functions = ['applySavedCompanyProfileProfile', 'invalidateGeneratedExportsForPresentationChange',
  'markOutputRowsDirty', 'revisionNumber', 'downloadFileIsFresh', 'pdfFileIsFresh'].map(extract).join('\n');
for (const phase of ['empty', 'basis', 'output']) {
  for (const defaults of [{company:{name:'Synthetic Updated Company'}}, {}]) {
    const state = {images:[], quoteBasis:{summary:phase === 'empty' ? '' : 'Synthetic basis'},
      quoteBasisSections:phase === 'empty' ? [] : [{id:'section-1'}],
      lineItems:phase === 'empty' ? [] : [{id:'row-1',quantity:2,unit_price:7}],
      outputRows:phase === 'output' ? [{id:'row-1',quantity:2,unit_price:7,amount:14}] : [],
      originalOutputRows:phase === 'output' ? [{id:'row-1',quantity:2,unit_price:7,amount:14}] : [],
      basisConfirmed:phase !== 'empty', workflowStage:phase === 'empty' ? 'needs_images' : phase === 'basis' ? 'basis_review' : 'generated',
      analysisFindings:[{code:'synthetic-finding'}], originalAnalysisSnapshot:{id:'analysis-1'},
      quoteCommercialLifecycle:'EXISTING',quoteCommercialSnapshot:{currency:'SGD',exchange_rate:1,tax_rate:0},
      profileId:'synthetic-profile',pricingReferenceId:'synthetic-reference',pricingReferenceSource:'company',
      activeQuoteSessionId:'quote-synthetic',outputRevision:3,downloadFileRevision:3,pdfFileRevision:3,
      downloadFile:phase === 'output' ? {url:'/synthetic.xlsx'} : null,
      pdfFile:phase === 'output' ? {url:'/synthetic.pdf'} : null};
    const before = JSON.parse(JSON.stringify(state));
    let applied = 0, downloadsUpdated = 0;
    const context = {state,applyQuoteDetails(value, options) {
      assert.deepStrictEqual(value, defaults);
      assert.deepStrictEqual(JSON.parse(JSON.stringify(options)),{includeLogo:true,clearLogo:true,partial:true});
      applied++;
    },updateDownloadButton(){downloadsUpdated++;},
      clearGeneratedQuoteState(){throw Error('Profile import discarded the quote');},
      setWorkflowStage(){throw Error('Profile import changed the quote workflow');}};
    vm.createContext(context);
    vm.runInContext(functions,context);
    context.applySavedCompanyProfileProfile({defaults});
    assert.strictEqual(applied,Object.keys(defaults).length ? 1 : 0);
    assert.strictEqual(state.outputRevision,phase === 'output' ? 4 : 3);
    assert.strictEqual(downloadsUpdated,phase === 'output' ? 1 : 0);
    assert.strictEqual(context.downloadFileIsFresh(),false);
    assert.strictEqual(context.pdfFileIsFresh(),false);
    state.outputRevision = before.outputRevision;
    assert.deepStrictEqual(state,before);
  }
}
console.log(JSON.stringify({cases:6,status:'PASS'}));
"""
        result = subprocess.run([support.require_node(self), '-e', script], cwd=support.ROOT,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'cases': 6, 'status': 'PASS'})

    def test_managed_profile_export_roundtrips_layout_and_workspace_scope(self):
        for mode in ('database', 'object'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                database_url = f"sqlite:///{(Path(tmp) / 'storage.sqlite3').as_posix()}"
                backend = webapp.InMemoryObjectStorageBackend()
                env = {'SQAG_STORAGE_MODE': 'database', 'SQAG_ARTIFACT_STORAGE_MODE': mode,
                       'SQAG_DATABASE_URL': database_url}
                with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                    webapp, 'configured_object_storage_backend', return_value=backend
                ):
                    webapp.apply_sqag_storage_migrations(database_url)
                    identity = support.WebappServerTest()
                    stores = [webapp.app_storage_for_auth_session(identity.platform_auth_session(f'workspace-profile-{i}')) for i in range(3)]
                    owner, target, stranger = stores
                    source = webapp.normalize_profile_payload({
                        'id': 'synthetic-portable-profile', 'label': 'Synthetic Portable Profile',
                        'defaults': {'company': {'name': 'Synthetic Profile Company'},
                                     'quote_text': {'payment_terms': ['Synthetic terms.']}},
                        'pack': {'quotation_layout': {'filename': 'quotation-layout.xlsx',
                            'data_url': 'data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,'
                            + base64.b64encode(support.KONCEPT_LAYOUT.read_bytes()).decode('ascii')}},
                    })
                    owner.save_profile(source)
                    self.assertIsNone(stranger.company_profile_export_payload(source['id']))
                    expected = owner.profile_layout_artifact(source['id'])['content']
                    exported = owner.company_profile_export_payload(source['id'])
                    layout = exported['pack']['quotation_layout']
                    self.assertEqual(base64.b64decode(layout['data_url'].split(',', 1)[1]), expected)
                    self.assertEqual(layout['filename'], 'quotation-layout.xlsx')
                    self.assertEqual(exported['profile']['defaults'], source['defaults'])
                    target.save_profile(webapp.normalize_profile_payload(exported))
                    self.assertEqual(target.profile_layout_artifact(source['id'])['content'], expected)
                    self.assertEqual(target.profile_detail(source['id'])['defaults'], source['defaults'])
                    self.assertTrue(owner.delete_profile(source['id']))
                    self.assertIsNone(owner.company_profile_export_payload(source['id']))
                    self.assertIsNotNone(target.company_profile_export_payload(source['id']))

    def test_managed_export_refuses_unavailable_layout_and_propagates_storage_failure(self):
        for mode in ('database', 'object'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                database_url = f"sqlite:///{(Path(tmp) / 'storage.sqlite3').as_posix()}"
                with mock.patch.dict(os.environ, {'SQAG_STORAGE_MODE': 'database',
                    'SQAG_ARTIFACT_STORAGE_MODE': mode, 'SQAG_DATABASE_URL': database_url}, clear=True):
                    webapp.apply_sqag_storage_migrations(database_url)
                    storage = webapp.app_storage_for_auth_session(support.WebappServerTest().platform_auth_session())
                    with mock.patch.object(storage, '_read_payload', return_value={'id': 'synthetic-profile'}):
                        with mock.patch.object(storage, 'profile_layout_artifact', return_value=None):
                            with self.assertRaises(webapp.SqagStorageAccessError) as missing:
                                storage.company_profile_export_payload('synthetic-profile')
                            self.assertEqual(missing.exception.reason, 'profile_layout_unavailable')
                        with mock.patch.object(storage, 'profile_layout_artifact', side_effect=
                            webapp.SqagStorageAccessError('Synthetic unavailable storage.', status=503, reason='storage_unavailable')):
                            with self.assertRaises(webapp.SqagStorageAccessError) as failure:
                                storage.company_profile_export_payload('synthetic-profile')
                            self.assertEqual(failure.exception.reason, 'storage_unavailable')
