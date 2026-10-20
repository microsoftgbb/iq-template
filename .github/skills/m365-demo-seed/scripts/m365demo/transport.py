"""HTTPS calls with the standard library, plus the Entra and Graph base URLs.

M365_DEMO_LOGIN_URL and M365_DEMO_GRAPH_URL exist for the offline tests only: they are accepted
only when they point at a loopback server, so tokens can never be sent anywhere else.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .common import Blocked

LOGIN_URL = "https://login.microsoftonline.com"
GRAPH_URL = "https://graph.microsoft.com"
USER_AGENT = "m365-demo-seed"


class TransportError(Exception):
    """The request never got an HTTP answer (DNS, TLS, connection or timeout)."""


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self.body = body or b""

    def json(self, default=None):
        if not self.body:
            return default
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            return default

    @property
    def ok(self):
        return 200 <= self.status < 300


def _override(variable, default):
    value = os.environ.get(variable)
    if not value:
        return default
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"):
        return value.rstrip("/")
    raise Blocked("bad-endpoint-override", f"{variable} may only point at a loopback test server.")


def login_url():
    return _override("M365_DEMO_LOGIN_URL", LOGIN_URL)


def graph_url():
    return _override("M365_DEMO_GRAPH_URL", GRAPH_URL)


def testing():
    """True when the endpoints point at a loopback test server (no real tenant involved)."""
    return login_url() != LOGIN_URL or graph_url() != GRAPH_URL


def request(method, url, *, headers=None, form=None, json_body=None, data=None, timeout=60):
    """One HTTP request; any HTTP status is returned as a Response, never raised."""
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    body = data
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    prepared = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(prepared, timeout=timeout) as answer:
            return Response(answer.status, dict(answer.headers), answer.read())
    except urllib.error.HTTPError as error:
        try:
            payload = error.read()
        except OSError:
            payload = b""
        finally:
            error.close()
        return Response(error.code, dict(error.headers or {}), payload)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(f"{method} {urllib.parse.urlparse(url).netloc}: {getattr(error, 'reason', error)}") from None
