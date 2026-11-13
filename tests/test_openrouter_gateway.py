"""G01-G13 synthetic gateway boundaries; no network or paid inference."""
import copy
import dataclasses
import io
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import ssl
import subprocess
import tempfile
from threading import Event, Thread
import unittest
import urllib.error
from email.message import Message
from unittest import mock
from webapp import ai_gateway as gateway
from tests.gateway_test_support import synthetic_configuration




def envelope(route):
    return {"model":route.model,"reasoning":{"effort":route.effort},"input":[{"role":"user","content":[{"type":"input_text","text":"synthetic request"}]}]}


def completed(route, **changes):
    return {"model":route.model,"status":"completed","output":[{"type":"message","role":"assistant","status":"completed","content":[{"type":"output_text","text":"{\"items\":[]}"}]}],**changes}


class RecordingTransport:
    def __init__(self,outcomes): self.outcomes=list(outcomes); self.calls=[]
    def send(self,body,**kwargs):
        self.calls.append((json.loads(body),kwargs))
        value=self.outcomes.pop(0)
        if isinstance(value,BaseException): raise value
        return value


class Response:
    def __init__(self,body,*,mime="application/json",url=gateway.OPENROUTER_RESPONSES_URL,status=200,length=None):
        self.body=body; self.status=status; self.url=url; self.closed=False
        self.headers=Message(); self.headers["Content-Type"]=mime
        if length is not None: self.headers["Content-Length"]=str(length)
    def __enter__(self): return self
    def __exit__(self,*args): self.closed=True
    def geturl(self): return self.url
    def read(self,limit): return self.body[:limit]


class OpenRouterGatewayTest(unittest.TestCase):
    def config(self, values=None): return gateway.GatewayConfiguration.capture((values or synthetic_configuration()).get)

    def test_G01_G03_exact_routes_snapshot_and_distinct_endpoint_pins(self):
        values=synthetic_configuration(); config=self.config(values)
        for operation in gateway.ROUTE_KEYS:
            route=config.route(operation); source=envelope(route); original=copy.deepcopy(source)
            transport=RecordingTransport([completed(route)])
            credential=mock.Mock(return_value="synthetic-credential")
            subject=gateway.AiGateway(config,credential_reader=credential,transport=transport)
            values["OPENROUTER_"+gateway.ROUTE_KEYS[operation]+"_MODEL"]="other/model"
            subject.request(operation,source)
            sent=transport.calls[0][0]
            self.assertEqual(source,original)
            self.assertEqual(sent["model"],route.model); self.assertEqual(sent["reasoning"],{"effort":route.effort})
            self.assertEqual(sent["provider"]["only"],[route.provider]); self.assertFalse(sent["provider"]["allow_fallbacks"])
            self.assertTrue(sent["provider"]["require_parameters"]); self.assertTrue(sent["provider"]["zdr"])
            self.assertEqual(sent["provider"]["data_collection"],"deny"); self.assertFalse(sent["store"])
            credential.assert_called_once()
        self.assertEqual(self.config(values).route("draft").model,"other/model")

    def test_G04_zdr_boolean_is_explicit_and_snapshotted_for_all_operations(self):
        for raw_zdr, expected in (("true", True), ("false", False)):
            values=synthetic_configuration(overrides={"OPENROUTER_ZDR":raw_zdr})
            config=self.config(values)
            self.assertIs(config.zdr,expected)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                config.zdr = not expected
            values["OPENROUTER_ZDR"]="false" if expected else "true"
            for operation in gateway.ROUTE_KEYS:
                with self.subTest(zdr=raw_zdr,operation=operation):
                    route=config.route(operation); transport=RecordingTransport([completed(route)])
                    gateway.AiGateway(config,credential_reader=lambda:"synthetic",transport=transport).request(operation,envelope(route))
                    self.assertEqual(len(transport.calls),1)
                    sent=transport.calls[0][0]
                    self.assertIs(sent["provider"]["zdr"],expected)
                    self.assertEqual(sent["provider"],{"only":[route.provider],"order":[route.provider],
                        "allow_fallbacks":False,"require_parameters":True,"data_collection":"deny","zdr":expected})
                    self.assertIs(sent["store"],False)

    def test_G04_missing_empty_or_invalid_zdr_never_reads_credentials_or_sends(self):
        for value in (None,"","True","False","1","no"," true","false ",True,False,1,[],{}):
            values=synthetic_configuration()
            if value is None:
                values.pop("OPENROUTER_ZDR")
            else:
                values["OPENROUTER_ZDR"]=value
            for operation in gateway.ROUTE_KEYS:
                transport=RecordingTransport([]); credential=mock.Mock(return_value="synthetic")
                with self.subTest(zdr=value,operation=operation),self.assertRaises(gateway.GatewayError) as caught:
                    config=self.config(values)
                    gateway.AiGateway(config,credential_reader=credential,transport=transport).request(operation,envelope(config.route(operation)))
                self.assertEqual(caught.exception.reason,"provider_policy")
                self.assertEqual(transport.calls,[])
                credential.assert_not_called()

    def test_G04_zdr_false_keeps_other_provider_policy_guards(self):
        cases=[("OPENROUTER_DATA_COLLECTION",value) for value in (None,"","allow","DENY")]
        cases += [("OPENROUTER_REQUIRE_PARAMETERS","false"),("OPENROUTER_ALLOW_FALLBACKS","true")]
        for operation,suffix in gateway.ROUTE_KEYS.items():
            route_cases=cases+[("OPENROUTER_"+suffix+"_PROVIDER_ONLY",value) for value in ('[]','["one","two"]')]
            for key,value in route_cases:
                values=synthetic_configuration(overrides={"OPENROUTER_ZDR":"false",key:value})
                transport=RecordingTransport([])
                with self.subTest(operation=operation,key=key,value=value),self.assertRaises(gateway.GatewayError) as caught:
                    config=self.config(values)
                    gateway.AiGateway(config,credential_reader=lambda:"synthetic",transport=transport).request(operation,envelope(config.route(operation)))
                self.assertEqual(caught.exception.reason,"provider_policy")
                self.assertEqual(transport.calls,[])

    def test_G02_G04_invalid_or_old_configuration_never_sends(self):
        values=synthetic_configuration()
        for key in values:
            for value in [None,"", "unexpected value"]:
                changed=values|{key:value}
                with self.subTest(key=key,kind=type(value).__name__),self.assertRaises(gateway.GatewayError): self.config(changed)
        for key,value in [("OPENROUTER_DRAFT_PROVIDER_ONLY",'["one","two"]'),("OPENROUTER_DRAFT_PROVIDER_ONLY",'"one"'),("OPENROUTER_DRAFT_MODEL","openrouter/auto"),("OPENROUTER_DRAFT_MODEL","synthetic/model:nitro"),("OPENROUTER_DRAFT_MODEL","model"),("OPENROUTER_REQUEST_TIMEOUT_SECONDS","1801")]:
            with self.subTest(key=key),self.assertRaises(gateway.GatewayError): self.config(values|{key:value})
        with self.assertRaises(gateway.GatewayError): self.config({"OPENAI_API_KEY":"synthetic","DEEPSEEK_API_KEY":"synthetic"})
        config=self.config(); route=config.route("draft"); transport=RecordingTransport([])
        for secret in [None,"", "synthetic\ncredential"]:
            with self.assertRaises(gateway.GatewayError): gateway.AiGateway(config,credential_reader=lambda:secret,transport=transport).request("draft",envelope(route))
        self.assertEqual(transport.calls,[])

    def test_G02_supported_efforts_are_exact_and_unknown_shape_valid_values_refuse(self):
        values=synthetic_configuration()
        for operation,suffix in gateway.ROUTE_KEYS.items():
            key="OPENROUTER_"+suffix+"_REASONING_EFFORT"
            for effort in gateway.SUPPORTED_REASONING_EFFORTS:
                with self.subTest(operation=operation,effort=effort):
                    config=self.config(values|{key:effort}); route=config.route(operation)
                    transport=RecordingTransport([completed(route)])
                    gateway.AiGateway(config,credential_reader=lambda:"synthetic-key",transport=transport).request(operation,envelope(route))
                    self.assertEqual(len(transport.calls),1)
                    self.assertEqual(transport.calls[0][0]["reasoning"],{"effort":effort})
            for effort in ["banana","future_effort","ultra","HIGH"," high","high ","","unexpected value"]:
                with self.subTest(operation=operation,rejected=effort),self.assertRaises(gateway.GatewayError):
                    self.config(values|{key:effort})

    def test_G05_G06_envelope_changes_refuse_and_media_is_unchanged(self):
        config=self.config(); route=config.route("draft"); transport=RecordingTransport([])
        subject=gateway.AiGateway(config,credential_reader=lambda:"synthetic",transport=transport)
        for changed in [envelope(route)|{"model":"other/model"},envelope(route)|{"reasoning":{"effort":"low"}},envelope(route)|{"provider":{}},envelope(route)|{"models":[]},envelope(route)|{"input":[]}]:
            with self.assertRaises(gateway.GatewayError): subject.request("draft",changed)
        with self.assertRaises(gateway.GatewayError): subject.request("unknown",envelope(route))
        self.assertEqual(transport.calls,[])
        body=envelope(route); body["input"][0]["content"].extend([{"type":"input_file","filename":"synthetic.pdf","file_data":"data:application/pdf;base64,c3ludGhldGlj"},{"type":"input_image","image_url":"data:image/png;base64,c3ludGhldGlj","detail":"high"},{"type":"input_image","image_url":"data:image/png;base64,Y2F0YWxvZw==","detail":"low"}])
        transport.outcomes=[completed(route)]; subject.request("draft",body)
        self.assertEqual(transport.calls[0][0]["input"],body["input"])

    def test_G07_G08_response_shape_matrix(self):
        route=self.config().route("draft")
        cases=[None,[],{},completed(route,status="failed"),completed(route,status=[]),completed(route,status={}),completed(route,status="incomplete"),completed(route,error={"message":"private-canary"}),completed(route,error_type="private-canary"),completed(route,refusal="private-canary"),completed(route,output=[]),completed(route,model="other/model"),completed(route,output_text="conflict"),completed(route,incomplete_details={"reason":"max_output_tokens"})]
        for kind in ["refusal","tool_call","input_text"]:
            changed=completed(route); changed["output"][0]["content"][0]["type"]=kind; cases.append(changed)
        mixed=completed(route); mixed["output"][0]["content"].append({"type":"refusal","refusal":"private-canary"}); cases.append(mixed)
        multiple=completed(route); multiple["output"].append(copy.deepcopy(multiple["output"][0])); cases.append(multiple)
        for index,value in enumerate(cases):
            with self.subTest(index=index),self.assertRaises(gateway.GatewayError) as caught: gateway.admit_response(value,route)
            self.assertNotIn("private-canary",str(caught.exception))
        self.assertEqual(gateway.admit_response(completed(route),route)["status"],"completed")

    def test_G09_G12_actual_transport_shape_bounds_and_safe_failures(self):
        config=self.config(); route=config.route("draft"); body=json.dumps(completed(route)).encode()
        responses=[Response(body,mime="text/plain"),Response(body,url="https://other.example.test"),Response(body,status=302),Response(body,length=len(body)+1),Response(b'{"a":1,"a":2}'),Response(b'{"a":NaN}'),Response(b'not-json'),Response(b'\xff'),Response(body,length=gateway.MAX_RESPONSE_BYTES+1)]
        for response in responses:
            opener=mock.Mock(); opener.open.return_value=response
            with self.subTest(index=responses.index(response)),self.assertRaises(gateway.GatewayError): gateway.BoundedHttpsTransport(opener=opener).send(b"{}",credential="synthetic",timeout=10)
            self.assertTrue(response.closed)
        opener=mock.Mock(); opener.open.return_value=Response(body)
        result=gateway.BoundedHttpsTransport(opener=opener).send(b"{}",credential="synthetic",timeout=10)
        self.assertEqual(result["status"],"completed")
        request=opener.open.call_args.args[0]; self.assertEqual(request.full_url,gateway.OPENROUTER_RESPONSES_URL)
        handler=gateway._NoRedirect(); self.assertIsNone(handler.redirect_request(None,None,302,"",{},"https://other.example.test"))
        for status in [301,400,401,402,403,429,500,503]:
            opener=mock.Mock(); opener.open.side_effect=urllib.error.HTTPError(gateway.OPENROUTER_RESPONSES_URL,status,"private-canary",{},io.BytesIO(b"private-canary"))
            with self.subTest(status=status),self.assertRaises(gateway.GatewayError) as caught: gateway.BoundedHttpsTransport(opener=opener).send(b"{}",credential="synthetic",timeout=10)
            self.assertNotIn("private-canary",str(caught.exception)); self.assertEqual(caught.exception.diagnostics["http_status"],status)

    def test_G08_G12_failures_keep_classification_and_only_allowlisted_diagnostics(self):
        config=self.config()
        for operation in gateway.ROUTE_KEYS:
            route=config.route(operation)
            refused=completed(route); refused["output"][0]["content"]=[{"type":"refusal","refusal":"PRIVATE_BODY"}]
            malformed=completed(route); malformed["output"][0]["content"]=7
            for value,boundary in ((completed(route,status="incomplete",incomplete_details={"reason":"max_output_tokens"}),"provider_response_contract"),(refused,"provider_response_contract"),(malformed,"provider_response_shape")):
                records=[];transport=RecordingTransport([value])
                subject=gateway.AiGateway(config,credential_reader=lambda:"synthetic",transport=transport,telemetry=lambda *args:records.append(args))
                with self.subTest(operation=operation,boundary=boundary),self.assertRaises(gateway.GatewayError) as caught:
                    subject.request(operation,envelope(route))
                self.assertEqual(caught.exception.diagnostics["failure_boundary"],boundary)
                self.assertEqual(len(records),1);self.assertEqual(len(transport.calls),1)
                self.assertNotIn("PRIVATE",str(caught.exception)+json.dumps(records))
        for param in ("reasoning.effort","input[0].content[1].image_url","PRIVATE_PARAMETER"):
            raw={"error":{"type":"invalid_request_error","code":"unsupported_value","param":param,"message":"PRIVATE_MESSAGE"}}
            opener=mock.Mock();opener.open.side_effect=urllib.error.HTTPError(gateway.OPENROUTER_RESPONSES_URL,400,"PRIVATE_REASON",{},io.BytesIO(json.dumps(raw).encode()))
            with self.subTest(param=param),self.assertRaises(gateway.GatewayError) as caught:
                gateway.BoundedHttpsTransport(opener=opener).send(b"{}",credential="synthetic",timeout=10)
            self.assertEqual(caught.exception.diagnostics["failure_boundary"],"provider_http")
            self.assertEqual(caught.exception.diagnostics["provider_error_class"],"invalid_request_error")
            self.assertEqual(caught.exception.diagnostics["provider_error_code"],"unsupported_value")
            if param.startswith("PRIVATE"):
                self.assertNotIn("provider_error_param",caught.exception.diagnostics)
            else:
                self.assertEqual(caught.exception.diagnostics["provider_error_param"],param)
            self.assertNotIn("PRIVATE",str(caught.exception)+json.dumps(caught.exception.diagnostics))

    def test_G10_full_one_send_text_bounded_retry_and_no_partial_replay(self):
        config=self.config()
        for operation in gateway.ROUTE_KEYS:
            route=config.route(operation); allowed=3 if operation in {"basis_line","basis_answer"} else 1
            transport=RecordingTransport([gateway.GatewayError("connection_failed",boundary="provider_transport",retryable=True) for _ in range(3)])
            sleep=mock.Mock(); subject=gateway.AiGateway(config,credential_reader=lambda:"synthetic",transport=transport,sleep=sleep)
            with self.assertRaises(gateway.GatewayError): subject.request(operation,envelope(route))
            self.assertEqual(len(transport.calls),allowed); self.assertEqual(sleep.call_count,allowed-1)
            transport=RecordingTransport([completed(route,status="incomplete")])
            with self.assertRaises(gateway.GatewayError): gateway.AiGateway(config,credential_reader=lambda:"synthetic",transport=transport,sleep=sleep).request(operation,envelope(route))
            self.assertEqual(len(transport.calls),1)

    def test_G10_http_semantic_errors_override_transient_status_without_private_diagnostics(self):
        config=self.config()
        negatives=[{"error_type":value,"error":{"code":"server_error"}} for value in gateway.NONRETRY_ERROR_TYPES]
        negatives += [{"error":{"type":value,"code":"server_error"}} for value in gateway.SUPPORTED_ERROR_CLASSES - {"server_error"}]
        negatives += [{"error":{"type":"server_error","code":value}} for value in gateway.NONRETRY_ERROR_CODES]
        negatives += [{"error":{"code":value}} for value in (400,401,402,403,404,413,422,429)]
        negatives += [{"error_type":"PRIVATE_UNKNOWN_TYPE"},{"error":{"code":"PRIVATE_UNKNOWN_CODE"}},
                      {"error_type":["PRIVATE_TYPE"]},{"output_text":"PRIVATE_PARTIAL"},
                      {"output":[{"text":"PRIVATE_PARTIAL"}]},{"refusal":"PRIVATE_REFUSAL"},
                      {"refusal":""},{"incomplete_details":[]},{"incomplete_details":{"reason":"PRIVATE_REASON"}},
                      {"status":"incomplete"},{"status":"incomplete","incomplete_details":None},
                      {"status":"in_progress"},{"status":"queued"},{"status":"cancelled"}]
        for operation in gateway.ROUTE_KEYS:
            route=config.route(operation)
            for status in gateway.TRANSIENT_HTTP_STATUSES:
                for value in negatives:
                    payload=copy.deepcopy(value)
                    payload.setdefault("error",{}).update(message="PRIVATE_MESSAGE",metadata={"raw":"PRIVATE_RAW"})
                    records=[]; delays=[]; opener=mock.Mock()
                    def fail(*args,**kwargs):
                        raise urllib.error.HTTPError(gateway.OPENROUTER_RESPONSES_URL,status,"PRIVATE_REASON",{},io.BytesIO(json.dumps(payload).encode()))
                    opener.open.side_effect=fail
                    subject=gateway.AiGateway(config,credential_reader=lambda:"PRIVATE_CREDENTIAL",
                        transport=gateway.BoundedHttpsTransport(opener=opener),sleep=delays.append,telemetry=lambda *args:records.append(args))
                    with self.subTest(operation=operation,status=status,payload=value),self.assertRaises(gateway.GatewayError):
                        subject.request(operation,envelope(route))
                    self.assertEqual(opener.open.call_count,1)
                    self.assertEqual(delays,[])
                    self.assertEqual(len(records),1)
                    self.assertFalse(records[0][-1]["retry_pending"])
                    self.assertNotIn("PRIVATE",json.dumps(records))

    def test_G10_genuine_http_transients_keep_same_route_limits_delays_and_one_terminal_per_send(self):
        config=self.config()
        positives=[{}, {"status":"failed","error":{"type":"server_error","code":"server_error"}}]
        positives += [{"error_type":value,"error":{"code":"server_error"}} for value in gateway.TRANSIENT_ERROR_TYPES]
        for operation in gateway.ROUTE_KEYS:
            route=config.route(operation)
            for status in gateway.TRANSIENT_HTTP_STATUSES:
                for value in positives:
                    records=[];delays=[];opener=mock.Mock()
                    def fail(*args,**kwargs):
                        raise urllib.error.HTTPError(gateway.OPENROUTER_RESPONSES_URL,status,"PRIVATE_REASON",{},io.BytesIO(json.dumps(value).encode()))
                    opener.open.side_effect=fail
                    subject=gateway.AiGateway(config,credential_reader=lambda:"synthetic-key",
                        transport=gateway.BoundedHttpsTransport(opener=opener),sleep=delays.append,telemetry=lambda *args:records.append(args))
                    with self.subTest(operation=operation,status=status,payload=value),self.assertRaises(gateway.GatewayError):
                        subject.request(operation,envelope(route))
                    expected=3 if operation in {"basis_line","basis_answer"} else 1
                    self.assertEqual(opener.open.call_count,expected)
                    self.assertEqual(delays,[2.0,5.0] if expected==3 else [])
                    self.assertEqual([row[1] for row in records],list(range(1,expected+1)))
                    requests=[call.args[0] for call in opener.open.call_args_list]
                    self.assertEqual(len({request.data for request in requests}),1)
                    self.assertEqual({request.full_url for request in requests},{gateway.OPENROUTER_RESPONSES_URL})
                    self.assertFalse(records[-1][-1]["retry_pending"])

    def test_G11_usage_numeric_matrix(self):
        self.assertEqual(gateway.admitted_usage({"input_tokens":2,"output_tokens":3,"total_tokens":5,"private":"canary"}),{"input_tokens":2,"output_tokens":3,"total_tokens":5})
        for value in [None,[],{}, {"input_tokens":True},{"input_tokens":-1},{"total_tokens":float("inf")},{"total_tokens":"3"},{"input_tokens":2,"output_tokens":3,"total_tokens":4},{"output_tokens":2,"output_tokens_details":{"reasoning_tokens":3}}]:
            with self.subTest(kind=type(value).__name__): self.assertIsNone(gateway.admitted_usage(value))


class LoopbackResponseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Generate a short-lived fixture identity locally. No stored key or external service.
        openssl = shutil.which("openssl")
        if openssl is None and os.name == "nt":
            openssl = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
        if not openssl or not Path(openssl).is_file():
            raise RuntimeError("OpenSSL CLI is required for the gateway's loopback TLS tests.")
        with tempfile.TemporaryDirectory() as directory:
            key = str(Path(directory) / "key.pem")
            cert = str(Path(directory) / "cert.pem")
            subprocess.run(
                [str(openssl), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", "/CN=openrouter.ai", "-addext", "subjectAltName=DNS:openrouter.ai",
                 "-keyout", key, "-out", cert],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15, check=True,
            )
            cls.server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            cls.server_tls.load_cert_chain(cert, key)
            cls.client_tls = ssl.create_default_context(cafile=cert)

    def setUp(self):
        self.reply = b""
        self.hold_open = False
        self.orderly_close = True
        self.release = Event()
        self.requests = []
        fixture = self

        class Provider(socketserver.StreamRequestHandler):
            def handle(self):
                request_line = self.rfile.readline()
                length = 0
                while (line := self.rfile.readline()) not in (b"\r\n", b""):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                reply, hold_open, orderly_close = fixture.reply, fixture.hold_open, fixture.orderly_close
                fixture.requests.append((request_line, json.loads(self.rfile.read(length))))
                self.wfile.write(reply)
                if hold_open:
                    fixture.release.wait(5)
                if orderly_close:
                    # Send close_notify. urllib may close before sending its own alert.
                    try:
                        self.request.unwrap().close()
                    except OSError:
                        pass

        class TlsServer(socketserver.ThreadingTCPServer):
            def get_request(self):
                raw, address = super().get_request()
                raw.settimeout(1)
                try:
                    return fixture.server_tls.wrap_socket(raw, server_side=True), address
                except OSError:
                    raw.close()
                    raise

        provider = TlsServer(("127.0.0.1", 0), Provider)
        self.addCleanup(provider.server_close)
        worker = Thread(target=provider.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(provider.shutdown)
        self.addCleanup(self.release.set)

        # Preserve the HTTPS opener, handshake, hostname check and response parser.
        # Substitute only the trust root and TCP destination.
        create_connection = socket.create_connection

        def connect(address, *args, **kwargs):
            self.assertEqual(address, ("openrouter.ai", 443))
            return create_connection(provider.server_address, timeout=0.5)

        real_connect = socket.socket.connect

        def local_only(sock, address):
            self.assertEqual(address, provider.server_address)
            return real_connect(sock, address)

        self.enterContext(mock.patch.object(socket.socket, "connect", local_only))
        self.enterContext(mock.patch.object(socket, "create_connection", connect))
        # Model a context that would otherwise turn a ragged EOF into a clean one.
        self.client_tls.options |= getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
        self.enterContext(mock.patch.object(ssl, "_create_default_https_context", return_value=self.client_tls))
        self.enterContext(mock.patch.object(urllib.request, "getproxies", return_value={}))
        self.config = gateway.GatewayConfiguration.capture(synthetic_configuration(overrides={
            "OPENROUTER_DRAFT_IMAGE_SCHEMA_ENABLED": "true",
        }).get)

    def request(self, operation, reply, *, reason=None, sends=1, cost=None, hold_open=False,
                orderly_close=True, draft_batch=False, output_text=None, response_status=None):
        self.requests.clear()
        self.reply, self.hold_open, self.orderly_close = reply, hold_open, orderly_close
        spend, sleep, telemetry = mock.Mock(), mock.Mock(), mock.Mock()
        route = self.config.route(operation)
        subject = gateway.AiGateway(self.config, credential_reader=lambda: "synthetic-key",
                                    quote_spend=spend, telemetry=telemetry, sleep=sleep)
        if draft_batch:
            from tests.test_image_observation_contract import batch_body
            body = batch_body(route)
        else:
            body = envelope(route)
        if reason is None:
            expected = completed(route)["output"]
            if output_text is not None:
                expected[0]["content"][0]["text"] = output_text
            self.assertEqual(subject.request(operation, body, draft_batch=draft_batch)["output"], expected)
        else:
            with self.assertRaises(gateway.GatewayError) as caught:
                subject.request(operation, body, draft_batch=draft_batch)
            self.assertEqual(caught.exception.reason, reason)
            if response_status is not None:
                self.assertEqual(caught.exception.diagnostics["responses_status"], response_status)
                self.assertFalse(caught.exception.retryable)
            if reason in {"response_partial", "response_framing"}:
                self.assertFalse(caught.exception.retryable)
                self.assertFalse(hasattr(caught.exception, "reported_cost"))
                self.assertNotIn("provider_error_code", caught.exception.diagnostics)
                self.assertNotIn("synthetic-private-value", repr(caught.exception.diagnostics))
        self.assertEqual(len(self.requests), sends)
        for request_line, body in self.requests:
            self.assertEqual(request_line, b"POST /api/v1/responses HTTP/1.1\r\n")
            self.assertEqual(body["model"], route.model)
            self.assertEqual(body["provider"], self.config.provider_policy(operation))
        self.assertEqual(spend.reserve.call_count, sends)
        self.assertEqual([call.args[1] for call in spend.settle.call_args_list], [cost] * sends)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 5.0][:sends-1])
        self.assertEqual([call.args[1] for call in telemetry.call_args_list], list(range(1, sends+1)))
        self.assertEqual([call.args[4]["retry_pending"] for call in telemetry.call_args_list],
                         [True] * (sends-1) + [False])
        self.assertNotIn("synthetic-private-value", repr(telemetry.call_args_list))

    def test_only_complete_response_heads_can_authorise_http_status_retries(self):
        status = b"HTTP/1.1 503 Synthetic fixture\r\n"
        header = b"X-Synthetic: synthetic-private-value\r\n"
        interim = b"HTTP/1.1 100 Continue\r\n"
        cases = (
            ("pre-response EOF", b"", False, "connection_failed", 3),
            ("pre-response timeout", b"", True, "connection_failed", 3),
            ("status EOF", status[:-2], False, "response_partial", 1),
            ("status timeout", status[:-2], True, "response_partial", 1),
            ("header EOF", status + header, False, "response_partial", 1),
            ("header timeout", status + header, True, "response_partial", 1),
            ("mid header EOF", status + header[:-2], False, "response_partial", 1),
            ("bare LF", status + b"\n", False, "response_partial", 1),
            ("invalid status", b"HTTP/1.1 +503\r\n\r\n", False, "response_framing", 1),
            ("invalid field", status + b"Invalid field\r\n\r\n", False, "response_framing", 1),
            ("long field", status + b"X-Test: " + b"x" * 65536 + b"\r\n\r\n", False, "response_framing", 1),
            ("many fields", status + header * 101 + b"\r\n", False, "response_framing", 1),
            ("folded field", status + header + b" value\r\n\r\n", False, "response_framing", 1),
            ("interim header EOF", interim + header, False, "response_partial", 1),
            ("final status EOF", interim + b"\r\n", False, "response_partial", 1),
            ("final header EOF", interim + b"\r\n" + status + header, False, "response_partial", 1),
            ("interim limit", (interim + b"\r\n") * 100 + status + b"\r\n", False, "response_framing", 1),
            ("interim complete", interim + b"\r\n" + status + header + b"\r\n", False, "http_error", 3),
            ("final complete", status + header + b"\r\n", False, "http_error", 3),
        )
        for operation in ("basis_line", "basis_answer"):
            for name, reply, hold_open, reason, sends in cases:
                with self.subTest(operation=operation, name=name):
                    self.request(operation, reply, hold_open=hold_open, reason=reason, sends=sends)

    def test_only_valid_body_framing_exposes_cost_and_allows_retry(self):
        for operation in ("draft", "draft_high_quality", "basis_line", "basis_answer"):
            basis = operation.startswith("basis_")
            status = b"503 Service Unavailable" if basis else b"200 OK"
            route = self.config.route(operation)
            reply = {"usage": {"cost": 0.125}}
            reply = {"model": route.model, **reply} if basis else completed(route, **reply)
            payload = json.dumps(reply).encode()
            size = str(len(payload)).encode()
            chunk = f"{len(payload):x}\r\n".encode() + payload + b"\r\n"
            cases = (
                ("fixed", b"1.1", b"Content-Length: " + size, payload, None),
                ("fixed OWS", b"1.1", b"Content-Length:\t " + size + b" \t", payload, None),
                ("fixed HTTP/1.0", b"1.0", b"Content-Length: " + size, payload, None),
                ("close-delimited", b"1.1", b"Connection: close", payload, None),
                ("short fixed", b"1.1", b"Content-Length: " + size, payload[:-1], "response_partial"),
                ("invalid fixed", b"1.1", b"Content-Length: +" + size, payload, "response_framing"),
                ("duplicate length", b"1.1", b"Content-Length: " + size + b"\r\nContent-Length: " + size, payload, "response_framing"),
                ("conflicting fields", b"1.1", b"Content-Length: " + size + b"\r\nTransfer-Encoding: chunked", payload, "response_framing"),
                ("chunked", b"1.1", b"Transfer-Encoding: chunked", chunk + b"0\r\nX-Test: example\r\n\r\n", None),
                ("chunked OWS", b"1.1", b"Transfer-Encoding: Chunked \t", chunk + b"0\r\n\r\n", None),
                ("chunked HTTP/1.0", b"1.0", b"Transfer-Encoding: chunked", chunk + b"0\r\n\r\n", "response_framing"),
                ("unsupported coding", b"1.1", b"Transfer-Encoding: gzip, chunked", chunk + b"0\r\n\r\n", "response_framing"),
                ("incomplete chunk trailers", b"1.1", b"Transfer-Encoding: chunked", chunk + b"0\r\n", "response_partial"),
            )
            for name, version, header, body, framing_error in cases:
                with self.subTest(operation=operation, name=name):
                    head = b"HTTP/" + version + b" " + status + b"\r\nContent-Type: application/json\r\n"
                    self.request(operation, head + header + b"\r\n\r\n" + body,
                                 reason=framing_error or ("http_error" if basis else None),
                                 sends=3 if basis and not framing_error else 1,
                                 cost=None if framing_error else 0.125)

    def test_tls_close_alert_required_only_for_close_delimited_reply(self):
        observation = '{"observations":["Synthetic observation"]}'
        for operation in ("draft", "draft_high_quality", "basis_line", "basis_answer"):
            route = self.config.route(operation)
            image = operation.startswith("draft")
            value = completed(route) if image else {"model": route.model, "error_type": "provider_unavailable"}
            value["usage"] = {"cost": 0.125}
            if image:
                value["output"][0]["content"][0]["text"] = observation
            payload = json.dumps(value).encode()
            status = b"200 OK" if image else b"503 Service Unavailable"
            head = b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
            length = str(len(payload)).encode()
            chunk = f"{len(payload):x}\r\n".encode() + payload + b"\r\n"
            cases = (
                ("orderly close", b"Connection: close", payload, True, True, False),
                ("missing close alert", b"Connection: close", payload, False, False, False),
                ("timed out waiting for close alert", b"Connection: close", payload, True, False, True),
                ("fixed without close alert", b"Content-Length: " + length, payload, False, True, False),
                ("short fixed without close alert", b"Content-Length: " + length, payload[:-1], False, False, False),
                ("chunked without close alert", b"Transfer-Encoding: chunked", chunk + b"0\r\n\r\n", False, True, False),
                ("short chunked without close alert", b"Transfer-Encoding: chunked", chunk + b"0\r\n", False, False, False),
            )
            for name, header, body, orderly, complete, hold_open in cases:
                with self.subTest(operation=operation, name=name):
                    self.request(operation, head + header + b"\r\n\r\n" + body,
                                 reason=(None if image else "http_error") if complete else "response_partial",
                                 sends=3 if complete and not image else 1, cost=0.125 if complete else None,
                                 orderly_close=orderly, draft_batch=image, output_text=observation,
                                 hold_open=hold_open)

    def test_orderly_close_with_explicit_incomplete_status_never_retries(self):
        for operation in ("basis_line", "basis_answer"):
            route = self.config.route(operation)
            payload = json.dumps({"model": route.model, "status": "incomplete", "incomplete_details": None,
                                  "error_type": "provider_unavailable", "usage": {"cost": 0.125}}).encode()
            with self.subTest(operation=operation):
                self.request(operation, b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n" + payload,
                             reason="http_error", response_status="incomplete", cost=0.125)


class ReportedResponseCostTest(unittest.TestCase):
    def test_accepts_only_bounded_nonnegative_numbers_for_the_configured_model(self):
        model = "openai/gpt-6.1-sol"
        for cost in (0, 0.125, 2**53 - 1):
            response = {"model": model, "usage": {"cost": cost}}
            self.assertEqual(gateway.reported_response_cost_usd(response, model), cost)
            self.assertIsNone(gateway.reported_response_cost_usd(response, "openai/gpt-6-astra"))
        for cost in (None, False, True, "0.125", -1, float("nan"), float("inf"), 2**53, 10**1000):
            with self.subTest(cost=cost):
                self.assertIsNone(gateway.reported_response_cost_usd(
                    {"model": model, "usage": {"cost": cost}}, model,
                ))
