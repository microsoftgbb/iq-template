"""A small Microsoft Graph client over the standard library.

It sends a session's in-memory token, retries throttling (429/503/504, honouring Retry-After),
renews the token once on 401, and follows @odata.nextLink only on the same Graph host.
"""

import urllib.parse

from .common import Blocked
from . import pace, transport

RETRY_STATUSES = frozenset({429, 503, 504})
# Throttling answers mean the request was not processed; a POST that timed out upstream (504) may have been.
RETRY_POST = frozenset({429, 503})
MAX_WAIT = 60
# Conditional Access changes made for a run reach Teams and Exchange minutes after sign-in honours them (a live
# run needed about 13 minutes). Until then a call can answer a claims challenge; one run waits at most this
# schedule (about 22 minutes) in total, shared by all its accounts.
CLAIMS_WAITS = (30, 60, 90, 120, 150, 180, 180, 180, 180, 180)


class GraphError(Blocked):
    """A Graph call answered with an error status; reported like any other blocked stop."""

    def __init__(self, method, path, response):
        body = response.json(default={})
        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
        self.status = response.status
        self.code = str(error.get("code") or "")
        self.detail = str(error.get("message") or f"HTTP {response.status}").splitlines()[0][:300]
        self.response = response
        text = f"{method} {path.split('?', 1)[0]}: {self.status} {self.code} {self.detail}".replace("  ", " ")
        super().__init__("graph-error", text, status=self.status, code=self.code)

    def __str__(self):
        return self.message


class ClaimsWaits:
    """The claims-challenge waits a run has left; share one between all of a run's Graph clients."""

    def __init__(self, schedule=CLAIMS_WAITS):
        self._left = list(schedule)

    def next(self):
        return self._left.pop(0) if self._left else None


class Graph:
    def __init__(self, session, *, version="v1.0", sleep=None, attempts=6, log=None, claims=None):
        self.session, self.version, self._attempts = session, version, attempts
        self._sleep = sleep or pace.sleep
        self._log = log
        self._claims = claims if claims is not None else ClaimsWaits()

    def url(self, path, version=None):
        base = transport.graph_url()
        if path.startswith(("http://", "https://")):
            if not path.startswith(base + "/"):
                raise Blocked("unexpected-link", f"Graph returned a link outside {base}; it was not followed.")
            return path
        return f"{base}/{version or self.version}/{path.lstrip('/')}"

    def call(self, method, path, *, json_body=None, data=None, headers=None, version=None):
        """The raw Response after retries; the caller inspects its status."""
        url = self.url(path, version)
        renewed = False
        attempt = 0
        while True:
            attempt += 1
            request_headers = {"Authorization": f"Bearer {self.session.token()}", **(headers or {})}
            try:
                response = transport.request(method, url, headers=request_headers, json_body=json_body, data=data)
            except transport.TransportError as error:
                # A POST that timed out may have been processed (a sent mail, a new chat); never repeat it.
                if method == "POST" or attempt >= self._attempts:
                    raise Blocked("network", str(error)) from None
                self._sleep(min(2 ** attempt, MAX_WAIT))
                continue
            if response.status == 401 and claims_challenge(response):
                # Renewing would not help (and can itself be refused while the change propagates): wait, then retry.
                # A refused request created nothing, so even a POST is safe to repeat.
                wait = self._claims.next()
                if wait is None:
                    return response
                if self._log:
                    self._log(f"Waiting {wait}s: Conditional Access changes have not reached the "
                              f"service yet (claims challenge on {method} {path.split('?', 1)[0]}).")
                self._sleep(wait)
                continue
            if response.status == 401 and not renewed:
                renewed = True
                self.session.renew()
                continue
            retryable = RETRY_POST if method == "POST" else RETRY_STATUSES
            if response.status in retryable and attempt < self._attempts:
                self._sleep(retry_after(response, attempt))
                continue
            return response

    def request(self, method, path, **options):
        """The JSON answer of a successful call ({} when empty); any other status raises GraphError."""
        response = self.call(method, path, **options)
        if not response.ok:
            raise GraphError(method, path, response)
        return response.json(default={}) or {}

    def get(self, path, **options):
        return self.request("GET", path, **options)

    def post(self, path, body=None, **options):
        return self.request("POST", path, json_body=body if body is not None else {}, **options)

    def patch(self, path, body, **options):
        return self.request("PATCH", path, json_body=body, **options)

    def put(self, path, data, content_type="application/octet-stream", **options):
        return self.request("PUT", path, data=data, headers={"Content-Type": content_type}, **options)

    def delete(self, path, *, missing_ok=True, **options):
        """True when deleted, False when it was already gone (404) and missing_ok."""
        response = self.call("DELETE", path, **options)
        if response.ok:
            return True
        if response.status == 404 and missing_ok:
            return False
        raise GraphError("DELETE", path, response)

    def pages(self, path, *, limit=None, **options):
        """Every item of a collection, following @odata.nextLink."""
        count = 0
        link = path
        while link:
            page = self.get(link, **options)
            for item in page.get("value") or []:
                yield item
                count += 1
                if limit and count >= limit:
                    return
            link = page.get("@odata.nextLink")
            options.pop("version", None)

    def me(self):
        return self.get("me?$select=id,userPrincipalName,displayName")


def claims_challenge(response):
    """A Conditional Access claims challenge: the service applies a policy the token does not satisfy (yet)."""
    if "insufficient_claims" in str(response.headers.get("www-authenticate") or ""):
        return True
    body = response.json(default={})
    error = body.get("error") if isinstance(body, dict) else None
    message = str(error.get("message") or "") if isinstance(error, dict) else ""
    return "additional claims required" in message.lower() or '"capolids"' in message


def retry_after(response, attempt):
    value = response.headers.get("retry-after")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 2 ** attempt
    return max(1.0, min(seconds, MAX_WAIT))


def quote(value):
    """One URL path segment or OData string literal value, safely encoded."""
    return urllib.parse.quote(str(value).replace("'", "''"), safe="")


def segment(value):
    """One URL path segment, percent-encoded (for example a file name)."""
    return urllib.parse.quote(str(value), safe="")


def odata_string(value):
    """An OData string literal: O'Brien -> 'O''Brien'."""
    return "'" + str(value).replace("'", "''") + "'"


def query(**options):
    """OData query options: query(filter="a eq 'b'", select="id") -> "$filter=a%20eq%20%27b%27&$select=id"."""
    return "&".join(f"${name}={urllib.parse.quote(str(value), safe=',')}" for name, value in options.items())


def add_to_group(graph, group_id, object_id, relation="members"):
    """Add a user to a group's members or owners, waiting out replication; already there counts as done."""
    body = {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{object_id}"}
    path = f"groups/{group_id}/{relation}/$ref"
    for attempt, last in pace.waits([0, 5, 10, 15, 20, 30, 30, 45]):
        response = graph.call("POST", path, json_body=body)
        if response.ok:
            return
        if response.status == 400 and "already exist" in response.body.decode("utf-8", "replace"):
            return
        if response.status in (403, 404, 408, 409, 423, 429, 500, 502, 503, 504) and not last:
            continue
        raise GraphError("POST", path, response)
