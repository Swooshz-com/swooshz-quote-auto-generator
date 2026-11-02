"""G3-only synthetic metadata Responses; real gateway admission, no external network."""
import contextlib
import json
import os
import re
import socket
from unittest import mock
from webapp import ai_gateway
from tests.gateway_test_support import json_response


class G3GatewayFixture:
    def __init__(self):
        self.stack = contextlib.ExitStack()
        self.metadata_requests = 0
        self.external_attempts = 0

    def open(self, request, *, timeout):
        if request.full_url != ai_gateway.OPENROUTER_RESPONSES_URL or request.get_header("Authorization") != "Bearer synthetic-playwright-provider-key":
            raise AssertionError("Unexpected G3 fixture destination or credential")
        body = json.loads(request.data)
        if (body.get("model") != "synthetic/pricing_metadata" or body.get("reasoning") != {"effort":"high"}
            or body.get("store") is not False or body.get("provider") != {"only":["synthetic-fixture"],"order":["synthetic-fixture"],"allow_fallbacks":False,"require_parameters":True,"data_collection":"deny","zdr":True}):
            raise AssertionError("Unexpected G3 fixture operation or policy")
        messages = body.get("input")
        if not isinstance(messages,list) or len(messages)!=1 or messages[0].get("role")!="user":
            raise AssertionError("Unexpected G3 metadata envelope")
        parts = messages[0].get("content")
        if not isinstance(parts,list) or len(parts)!=1 or parts[0].get("type")!="input_text":
            raise AssertionError("Unexpected G3 metadata content")
        prompt = parts[0].get("text")
        marker = "Pricing rows JSON: "
        if not isinstance(prompt,str) or prompt.count(marker)!=1:
            raise AssertionError("G3 metadata prompt contract changed")
        rows = json.loads(prompt.split(marker,1)[1])
        if not isinstance(rows,list) or not rows or len(rows)>500:
            raise AssertionError("Unexpected G3 metadata rows")
        output=[]; seen=set()
        for row in rows:
            identity=row.get("id"); description=row.get("description")
            if not isinstance(identity,str) or not identity or identity in seen or not isinstance(description,str) or not description.strip():
                raise AssertionError("Unexpected G3 metadata row identity")
            seen.add(identity)
            # Synthetic model output uses only this input row's words. No price,
            # quantity, unit or catalogue identity is fabricated or replaced.
            term=" ".join(description.lower().split())[:180]
            family="_".join(re.findall(r"[a-z0-9]+",description.lower())[:6])[:64].strip("_")
            if not family:raise AssertionError("Synthetic metadata needs source words")
            output.append({"id":identity,"match_terms":[term],"object_families":[family]})
        self.metadata_requests+=1
        return json_response(body["model"],{"items":output})

    def __enter__(self):
        if (os.environ.get("APP_MODE")!="local" or os.environ.get("SQAG_DISABLE_DOTENV") not in {"true","1"}
            or os.environ.get("OPENROUTER_API_KEY")!="synthetic-playwright-provider-key"):
            raise AssertionError("Isolated synthetic G3 configuration required")
        original_init=ai_gateway.BoundedHttpsTransport.__init__
        original_connect=socket.socket.connect
        original_lookup=socket.getaddrinfo
        def initialize(subject,*,opener=None):
            if opener is not None:raise AssertionError("Unexpected G3 opener override")
            original_init(subject,opener=self)
        def connect(subject,address):
            if not isinstance(address,tuple) or address[0] not in {"127.0.0.1","::1","localhost"}:
                self.external_attempts+=1
                raise AssertionError("External G3 socket forbidden")
            return original_connect(subject,address)
        def lookup(host,*args,**kwargs):
            if str(host) not in {"127.0.0.1","::1","localhost"}:
                self.external_attempts+=1
                raise AssertionError("External G3 DNS forbidden")
            return original_lookup(host,*args,**kwargs)
        self.stack.enter_context(mock.patch.object(ai_gateway.BoundedHttpsTransport,"__init__",initialize))
        self.stack.enter_context(mock.patch.object(socket.socket,"connect",connect))
        self.stack.enter_context(mock.patch.object(socket,"getaddrinfo",lookup))
        return self

    def __exit__(self,*exception):
        self.stack.close()
        if self.external_attempts:raise AssertionError("G3 attempted external access")
        return False
