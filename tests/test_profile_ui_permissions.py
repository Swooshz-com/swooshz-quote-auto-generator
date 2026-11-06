"""Existing controls and invisible request fields, using isolated JS functions."""
from pathlib import Path
import shutil
import subprocess
import unittest


class ProfileUiPermissionsTest(unittest.TestCase):
    def test_existing_controls_consume_per_record_permissions_and_import_creates_copy(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the isolated permission state harness")
        root = Path(__file__).resolve().parents[1]
        script = r'''
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
function button() { return {setAttribute(){},querySelector(){return null;},dataset:{}}; }
const state = {permissions:{canManageProfiles:true},companyProfiles:[
  {id:'shared',label:'Synthetic',permissions:{canUpdate:false,canDelete:false}},
  {id:'owned',label:'Owned',revision:3,permissions:{canUpdate:true,canDelete:true}}
]};
let preset = {id:'shared',source:'company',permissions:{canUpdate:false,canDelete:false}};
const elements = Object.fromEntries(['loadPresetButton','deletePresetButton','savePresetButton',
 'importPresetButton','exportPresetButton','profileActionsMenuButton'].map(key=>[key,button()]));
const context = {state,elements,selectedPreset:()=>preset,appIsBusy:()=>false,
  updatePresetSourceBadge(){},closeProfileActionsMenu(){},safeProfileLabel:(x)=>String(x),
  safeProfileId:(x)=>String(x),profilePackPayloadForSave:()=>null,
  collectQuoteCompanyProfileDetails:()=>({}),normalizeCompanyProfile:x=>x};
vm.createContext(context);
vm.runInContext(['canManageProfiles','profileNoAccessReason','setButtonLabel','updatePresetButtons',
 'existingCompanyProfileForLabel','profilePayloadForLabel','companyProfilePresets'].map(extract).join('\n'),context);
context.updatePresetButtons();
assert.equal(elements.loadPresetButton.disabled,false);
assert.equal(elements.savePresetButton.disabled,false);
assert.equal(elements.importPresetButton.disabled,false);
assert.equal(elements.deletePresetButton.disabled,true);
assert.equal(elements.exportPresetButton.disabled,false);
assert.equal(context.existingCompanyProfileForLabel('Synthetic'),null);
assert.equal(context.existingCompanyProfileForLabel('Owned',{mode:'import'}),null);
assert.equal(context.profilePayloadForLabel('Owned',{existingProfile:state.companyProfiles[1]}).operation,'update');
assert.equal(context.profilePayloadForLabel('Owned',{mode:'import',existingProfile:state.companyProfiles[1]}).operation,'create');
assert.equal(context.profilePayloadForLabel('Owned',{existingProfile:state.companyProfiles[1]}).revision,3);
assert.equal(context.companyProfilePresets()[0].permissions.canDelete,false);
preset={id:'owned',source:'company',permissions:{canDelete:true}};
context.updatePresetButtons(); assert.equal(elements.deletePresetButton.disabled,false);
state.permissions.canManageProfiles=false;
context.updatePresetButtons(); assert.equal(elements.savePresetButton.disabled,true);
state.permissions.canManageProfiles=true;state.profileSaveBusy=true;
context.updatePresetButtons(); assert.equal(elements.loadPresetButton.disabled,true);
assert.equal(elements.exportPresetButton.disabled,true);
'''
        result = subprocess.run([node, "-e", script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
