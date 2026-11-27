#!/usr/bin/env python3
"""Serve synthetic owner scenarios with real admission and loopback-only transport.

The driver supplies local paths and a scenario; every response retains production
normalisation and pricing admission. Serving failures still close the server.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from webapp import server as webapp


from tests.internal_password_browser_harness import (
    HarnessClock, HarnessHandler, configure_auth_env, configure_sqlite_storage, parse_storage,
)


def main() -> int:
    # --storage sqlite keeps quotes with their owners in a database, as live does; the default is files.
    storage, sys.argv[1:] = parse_storage(sys.argv[1:])
    configure_auth_env()
    os.environ.update({
        'SQAG_DISABLE_DOTENV':'1','QUOTE_DATA_ROOT':sys.argv[2],'QUOTE_OUTPUT_ROOT':sys.argv[3],
        'QUOTE_TMP_ROOT':sys.argv[4],'QUOTE_LOG_ROOT':sys.argv[5],
        'SQAG_LOCAL_PRICING_REFERENCES_ROOT':sys.argv[6],'OPENROUTER_API_KEY':'','DEEPSEEK_API_KEY':''})
    if storage == 'sqlite':
        configure_sqlite_storage()
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
    import socket as _alpha_socket
    _alpha_real_connect=_alpha_socket.socket.connect
    _alpha_real_getaddrinfo=_alpha_socket.getaddrinfo
    def _alpha_loopback_lookup(host,*args,**kwargs):
        if str(host).lower() not in ('127.0.0.1','::1','localhost'): raise RuntimeError('external DNS lookup blocked by owner UAT local harness')
        return _alpha_real_getaddrinfo(host,*args,**kwargs)
    _alpha_socket.getaddrinfo=_alpha_loopback_lookup
    def _alpha_loopback_only(sock,address):
        host=address[0] if isinstance(address,tuple) else str(address)
        if host not in ('127.0.0.1','::1','localhost'): raise RuntimeError('external socket connection blocked by owner UAT local harness')
        return _alpha_real_connect(sock,address)
    _alpha_socket.socket.connect=_alpha_loopback_only
    # The owner driver supplies a synthetic scenario; production normalisation
    # and pricing admission still process every response. No provider is called.
    scenario_path = Path(sys.argv[8]).resolve()
    mock_invocations = 0
    def owner_mock_provider(payload, api_key, auth_session=None, *, draft_configuration):
        nonlocal mock_invocations
        if api_key != 'ordinary-mock-only-no-network': raise RuntimeError('unexpected provider marker')
        if not isinstance(draft_configuration, webapp.OpenAIDraftConfiguration): raise RuntimeError('missing captured draft configuration')
        if draft_configuration.analysis_mode != webapp.draft_analysis_mode(payload): raise RuntimeError('draft mode mismatch')
        if not (webapp.valid_openai_model_identifier(draft_configuration.model) and webapp.valid_openai_reasoning_effort(draft_configuration.reasoning_effort)): raise RuntimeError('malformed synthetic draft configuration')
        authority = webapp.exact_pricing_reference_authority(payload, auth_session=auth_session)
        if not authority.get('ok'): raise RuntimeError('fixture pricing authority did not resolve')
        scenario = json.loads(scenario_path.read_text(encoding='utf-8'))
        raw = scenario['raw']
        # The provider path turns the AI's quantity_estimated flags into Est. marks before admission.
        if scenario.get('mark_estimates'): webapp.mark_ai_estimated_quantities(raw)
        mock_invocations += 1
        (Path(sys.argv[2]).parent/'mock-provider-count.json').write_text(json.dumps({'mock_invocations':mock_invocations,'real_provider_calls':0})+'\n')
        return webapp.normalize_ai_draft(raw,payload,require_confidence=True,auth_session=auth_session)
    original_reader = webapp.read_dotenv_value
    webapp.read_dotenv_value = lambda name: 'ordinary-mock-only-no-network' if name == webapp.OPENROUTER_API_KEY_ENV_NAME else original_reader(name)
    webapp.request_openai_quote_basis = owner_mock_provider
    # A scenario may also answer the pricing import; the reply still passes the import contract.
    pricing_import = json.loads(scenario_path.read_text(encoding='utf-8')).get('pricing_import') if scenario_path.is_file() else None
    if pricing_import is not None:
        original_gateway = webapp.configured_ai_gateway
        webapp.configured_ai_gateway = lambda feature, *args, **kwargs: (
            None if feature == 'pricing_reference_import' else original_gateway(feature, *args, **kwargs))
        webapp.request_openai_pricing_catalog_import = lambda *_args, **_kwargs: webapp.PRICING_IMPORT.admit(pricing_import)
    if storage == 'sqlite':
        # Saving the seed reference would ask the AI for matching terms; the fixture rows already have them.
        webapp.ai_pricing_reference_metadata_enrichment = lambda _filename, items, **_kwargs: (items, [])
    # And the Re chat: the reply keeps the selected line's words and gives it this tag.
    basis_chat_tag = json.loads(scenario_path.read_text(encoding='utf-8')).get('basis_chat_tag') if scenario_path.is_file() else None
    if basis_chat_tag:
        def owner_mock_basis_chat(payload, auth_session=None):
            chat = payload['basis_chat']
            line = next(section for section in payload['quote_basis_sections'] if section['id'] == chat['field'])['lines'][chat['line_index']]
            return webapp.normalize_basis_chat_result({'intent': 'proposal', 'answer': None, 'question': None, 'proposal': {
                'message': 'Change this line?',
                'replacement_line': {'tag': basis_chat_tag, 'text': line['text'], 'quantity': None, 'unit': None},
            }}, payload, 'openrouter')
        webapp.request_configured_basis_chat = owner_mock_basis_chat
    # Or the driver writes each reply's replacement_line (text, tag, quantity, unit) to basis-chat-reply.json
    # beside the scenario before it sends; the server still admits it as a revise_line_v1 provider reply.
    if scenario_path.is_file() and json.loads(scenario_path.read_text(encoding='utf-8')).get('basis_chat_reply'):
        def owner_mock_basis_chat_reply(payload, auth_session=None):
            reply = json.loads((scenario_path.parent / 'basis-chat-reply.json').read_text(encoding='utf-8'))
            return webapp.normalize_basis_chat_result({'intent': 'proposal', 'answer': None, 'question': None, 'proposal': {
                'message': 'Change this line?', 'replacement_line': reply,
            }}, payload, 'openrouter')
        webapp.request_configured_basis_chat = owner_mock_basis_chat_reply
    original_cookie_header = webapp.cookie_header_value
    webapp.cookie_header_value = lambda *args, **kwargs: original_cookie_header(
        *args, **kwargs
    ).replace("; Secure", "")

    clock = HarnessClock()
    server = webapp.ThreadingHTTPServer(("127.0.0.1", 0), HarnessHandler)
    server.internal_auth = webapp.InternalAuthState(clock=clock)
    server.harness_clock = clock
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
