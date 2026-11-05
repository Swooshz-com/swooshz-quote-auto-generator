#!/usr/bin/env python3
"""Local browser harness for the synthetic internal-password login adapter."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from webapp import server as webapp


SYNTHETIC_INTERNAL_ALPHA_ORIGIN = "https://internal-alpha.example.test"


from tests.internal_password_browser_harness import HarnessHandler, configure_auth_env


def main() -> int:
    configure_auth_env()
    os.environ.update({
        'SQAG_DISABLE_DOTENV':'1','QUOTE_DATA_ROOT':sys.argv[2],'QUOTE_OUTPUT_ROOT':sys.argv[3],
        'QUOTE_TMP_ROOT':sys.argv[4],'QUOTE_LOG_ROOT':sys.argv[5],
        'SQAG_LOCAL_PRICING_REFERENCES_ROOT':sys.argv[6],'OPENROUTER_API_KEY':'','DEEPSEEK_API_KEY':''})
    _alpha_default=webapp.default_runtime_workspace
    def _alpha_workspace():
        value=_alpha_default()
        for key in ('company','workspace'):
            target=value.get(key)
            if isinstance(target,dict): target.update({'id':'workspace-internal-alpha','slug':'workspace-internal-alpha','display_name':'Synthetic Internal Alpha'})
        return value
    webapp.default_runtime_workspace=_alpha_workspace
    webapp.profiles_root=lambda:Path(sys.argv[7]).resolve()
    webapp.is_allowed_host_header=lambda _host:True
    from webapp import server as _alpha_webapp_server
    import socket as _alpha_socket
    _alpha_real_connect=_alpha_socket.socket.connect
    _alpha_real_getaddrinfo=_alpha_socket.getaddrinfo
    def _alpha_loopback_lookup(host,*args,**kwargs):
        if str(host).lower() not in ('127.0.0.1','::1','localhost'): raise RuntimeError('external DNS lookup blocked by Alpha1 local harness')
        return _alpha_real_getaddrinfo(host,*args,**kwargs)
    _alpha_socket.getaddrinfo=_alpha_loopback_lookup
    def _alpha_loopback_only(sock,address):
        host=address[0] if isinstance(address,tuple) else str(address)
        if host not in ('127.0.0.1','::1','localhost'): raise RuntimeError('external socket connection blocked by Alpha1 local harness')
        return _alpha_real_connect(sock,address)
    _alpha_socket.socket.connect=_alpha_loopback_only
    scenario_path = Path(sys.argv[8]).resolve() if len(sys.argv) > 8 else None
    def alpha1_mock_provider(payload, api_key, auth_session=None, *, draft_configuration):
        if api_key != 'ordinary-mock-only-no-network': raise RuntimeError('unexpected provider marker')
        if not isinstance(draft_configuration, _alpha_webapp_server.OpenAIDraftConfiguration): raise RuntimeError('missing captured draft configuration')
        if draft_configuration.analysis_mode != _alpha_webapp_server.draft_analysis_mode(payload): raise RuntimeError('draft mode mismatch')
        if not (_alpha_webapp_server.valid_openai_model_identifier(draft_configuration.model) and _alpha_webapp_server.valid_openai_reasoning_effort(draft_configuration.reasoning_effort)): raise RuntimeError('malformed synthetic draft configuration')
        authority = _alpha_webapp_server.exact_pricing_reference_authority(payload, auth_session=auth_session)
        if not authority.get('ok'): raise RuntimeError('fixture pricing authority did not resolve')
        if scenario_path is not None:
            # Optional caller-written synthetic provider output; normalisation stays production code.
            raw = json.loads(scenario_path.read_text(encoding='utf-8'))['raw']
            server.__alpha1_mock_invocations=getattr(server,'__alpha1_mock_invocations',0)+1
            (Path(sys.argv[2]).parent/'mock-provider-count.json').write_text(json.dumps({'mock_invocations':server.__alpha1_mock_invocations,'real_provider_calls':0})+'\n')
            return _alpha_webapp_server.normalize_ai_draft(raw,payload,require_confidence=True,auth_session=auth_session)
        lookup = _alpha_webapp_server.pricing_catalog_runtime_lookup_for_payload(payload, _alpha_webapp_server.profile_id_from_payload(payload), auth_session=auth_session)
        items=[]; seen=set()
        for item in lookup.values():
            iid=_alpha_webapp_server.clean_text(item.get('id')) if isinstance(item,dict) else ''
            if iid and iid not in seen and item.get('active') is not False and item.get('disabled') is not True: seen.add(iid); items.append(item)
        if len(items)<2: raise RuntimeError('fewer than two active authoritative catalog rows')
        rows=[]; lines=[]
        for i,item in enumerate(items[:2]):
            iid=_alpha_webapp_server.clean_text(item.get('id')); desc=_alpha_webapp_server.clean_text(item.get('description')); unit=_alpha_webapp_server.clean_text(item.get('unit_hint')); section=_alpha_webapp_server.clean_text(item.get('section'))
            if not all((iid,desc,unit,section)): raise RuntimeError('catalog row lacks a required authoritative field')
            line_id='alpha1-basis-'+str(i+1)
            rows.append({'section':section,'source_basis_line_id':line_id,'quantity':1,'unit':unit,'description':desc,'pricing_keyword':iid})
            lines.append({'id':line_id,'tag':'Confirm','text':desc,'quantity':1,'unit':unit,'pricing_keyword':iid,'confidence_pct':95})
        dimensions=_alpha_webapp_server.booth_dimensions_from_payload(payload)
        canonical_note=''
        if dimensions.get('dimension_source')=='default':
            canonical=_alpha_webapp_server.default_booth_dimension_confirmation_line(dimensions)
            if not canonical.startswith('Confirm: '): raise RuntimeError('canonical dimension prefix changed')
            canonical_note=canonical[len('Confirm: '):]
            lines.append({'tag':'Confirm','text':canonical_note,'confidence_pct':50})
        raw={'analysis_findings':[],'blocking_clarification_questions':[],'quote_basis_sections':[{'id':'alpha1-synthetic-scope','title':'Synthetic Alpha1 Scope','lines':lines}],'line_items':rows}
        (Path(sys.argv[2]).parent/'provider-contract.json').write_text(json.dumps({'dimensions':dimensions,'canonical_note':canonical_note,'expected_catalog_rows':rows,'raw':raw},indent=2)+'\n',encoding='utf-8')
        server.__alpha1_mock_invocations=getattr(server,'__alpha1_mock_invocations',0)+1
        (Path(sys.argv[2]).parent/'mock-provider-count.json').write_text(json.dumps({'mock_invocations':server.__alpha1_mock_invocations,'real_provider_calls':0})+'\n')
        if server.__alpha1_mock_invocations!=1: raise RuntimeError('mock draft was invoked more than once')
        return _alpha_webapp_server.normalize_ai_draft(raw,payload,require_confidence=True,auth_session=auth_session)
    _alpha1_reader=_alpha_webapp_server.read_dotenv_value
    _alpha_webapp_server.read_dotenv_value=lambda name: 'ordinary-mock-only-no-network' if name==_alpha_webapp_server.OPENROUTER_API_KEY_ENV_NAME else _alpha1_reader(name)
    _alpha_webapp_server.request_openai_quote_basis=alpha1_mock_provider
    original_cookie_header = webapp.cookie_header_value
    webapp.cookie_header_value = lambda *args, **kwargs: original_cookie_header(
        *args, **kwargs
    ).replace("; Secure", "")

    server = webapp.ThreadingHTTPServer(("127.0.0.1", 0), HarnessHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    print(base_url, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
