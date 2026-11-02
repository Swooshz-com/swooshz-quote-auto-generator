"""Explicit synthetic Responses fixtures with the real bounded gateway transport.

No envelope repair, configuration refill, live network or provider calls. Business
suites inject only the HTTPS opener; the production send/admission code still runs.
"""
import json
import urllib.request
from email.message import Message
from types import SimpleNamespace
from unittest import mock
from webapp.ai_gateway import BoundedHttpsTransport, OPENROUTER_RESPONSES_URL, ROUTE_KEYS


def synthetic_configuration(*, credential=None, overrides=None):
    values={"OPENROUTER_REQUIRE_PARAMETERS":"true","OPENROUTER_ALLOW_FALLBACKS":"false","OPENROUTER_DATA_COLLECTION":"deny","OPENROUTER_ZDR":"true","OPENROUTER_REQUEST_TIMEOUT_SECONDS":"180"}
    for operation,suffix in ROUTE_KEYS.items():
        values["OPENROUTER_"+suffix+"_MODEL"]="synthetic/"+operation
        values["OPENROUTER_"+suffix+"_REASONING_EFFORT"]="high"
        values["OPENROUTER_"+suffix+"_PROVIDER_ONLY"]=json.dumps(["synthetic-"+operation.replace("_","-")])
    if credential is not None:
        values["OPENROUTER_API_KEY"] = credential
    if overrides is not None:
        values.update(overrides)
    return values



def responses_envelope(model, text, *, status="completed", **changes):
    return {"model":model, "status":status, "output":[{"type":"message", "role":"assistant",
            "status":"completed", "content":[{"type":"output_text", "text":text}]}], **changes}


class FixtureHttpResponse:
    def __init__(self, payload, *, status=200, mime="application/json", url=OPENROUTER_RESPONSES_URL):
        self.body = json.dumps(payload).encode("utf-8")
        self.status, self.url = status, url
        self.headers = Message()
        self.headers["Content-Type"] = mime
        self.headers["Content-Length"] = str(len(self.body))
        self.closed = False
    @classmethod
    def from_bytes(cls, body, **kwargs):
        if not isinstance(body, bytes):
            raise TypeError("Fixture bytes required")
        response = cls(None, **kwargs)
        response.body = body
        response.headers.replace_header("Content-Length", str(len(body)))
        return response
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed = True
    def geturl(self):
        return self.url
    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


def json_response(model, payload, **changes):
    return FixtureHttpResponse(responses_envelope(model, json.dumps(payload), **changes))


def install_mock_responses_transport(testcase):
    def initialize(subject, *, opener=None):
        subject._opener = opener if opener is not None else SimpleNamespace(open=urllib.request.urlopen)
    testcase.enterContext(mock.patch.object(BoundedHttpsTransport, "__init__", initialize))
