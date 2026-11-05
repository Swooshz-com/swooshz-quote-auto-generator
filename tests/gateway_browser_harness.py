"""Run the real local app with a loopback-only synthetic gateway opener.

Only the browser smoke runner uses this entry point. Production routing remains
fixed to HTTPS OpenRouter; this fixture admits one literal synthetic credential.
"""
import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp import ai_gateway, server


class LoopbackResponse:
    def __init__(self, response):
        self.response = response
        self.status, self.headers = response.status, response.headers
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.response.close()
    def geturl(self):
        return ai_gateway.OPENROUTER_RESPONSES_URL
    def read(self, limit):
        return self.response.read(limit)


class LoopbackOpener:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.opener = urllib.request.build_opener(ai_gateway._NoRedirect())
    def open(self, request, *, timeout):
        if request.full_url != ai_gateway.OPENROUTER_RESPONSES_URL or request.get_header("Authorization") != "Bearer synthetic-loopback-provider-key":
            raise AssertionError("Unexpected synthetic gateway request")
        forwarded = urllib.request.Request(self.endpoint + "/responses", data=request.data,
            headers={"Content-Type":"application/json", "Accept":"application/json"}, method="POST")
        return LoopbackResponse(self.opener.open(forwarded, timeout=timeout))


def main():
    endpoint = os.environ.get("SQAG_TEST_GATEWAY_ENDPOINT", "")
    parsed = urlparse(endpoint)
    if (os.environ.get("APP_MODE") != "local" or os.environ.get("SQAG_DISABLE_DOTENV") != "true"
        or os.environ.get("OPENROUTER_API_KEY") != "synthetic-loopback-provider-key"
        or parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
        or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise SystemExit("Synthetic loopback configuration required")
    original = ai_gateway.BoundedHttpsTransport
    class FixtureTransport(original):
        def __init__(self, *, opener=None):
            super().__init__(opener=opener if opener is not None else LoopbackOpener(endpoint))
    ai_gateway.BoundedHttpsTransport = FixtureTransport
    return server.main()


if __name__ == "__main__":
    raise SystemExit(main())
