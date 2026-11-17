"""HTTP with a hard deadline, tiny structural guards for untrusted JSON, and a JSON writer.

Accessed via: every provider and the cache. A voice answer must not wait on a slow network, so
every request carries a timeout and a late answer is an error, never a delay.

Assumptions: provider payloads are never asserted into a type. They are narrowed field by field
with the guards below, and a missing optional field becomes `None`. Writing JSON here keeps
numbers printed the way JavaScript prints them (`8`, not `8.0`) so files and `--json` output stay
byte-compatible with what the TypeScript original wrote.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import math
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, TypeAlias, TypeGuard, TypeVar, cast
from urllib.parse import quote_plus, urlsplit

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonRecord: TypeAlias = dict[str, JsonValue]

T = TypeVar("T")


def is_record(value: object) -> TypeGuard[JsonRecord]:
    return isinstance(value, dict)


def as_array(value: object) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def as_number(value: object) -> int | float | None:
    """A finite number, or None for anything else (null, string, bool, NaN)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:  # an integer literal beyond the float range reads as Infinity in JS
        return None


def as_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


# --- JSON text the way JSON.stringify writes it ---------------------------------------------


def js_number(value: int | float) -> str:
    """`String(number)` as JavaScript prints it: no `.0`, no `-0`, exponent only when huge/tiny."""
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 2**53:
        return str(int(value))
    return _js_decimal(value)


def _js_decimal(value: float) -> str:
    _, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    all_digits = "".join(map(str, digit_tuple))
    point = len(all_digits) + int(exponent)  # value = 0.<digits> * 10**point
    body = _js_layout(all_digits.rstrip("0"), point)
    return f"-{body}" if value < 0 else body


def _js_layout(digits: str, point: int) -> str:
    count = len(digits)
    if count <= point <= 21:
        return digits + "0" * (point - count)
    if 0 < point <= 21:
        return f"{digits[:point]}.{digits[point:]}"
    if -6 < point <= 0:
        return f"0.{'0' * -point}{digits}"
    exp = point - 1
    mantissa = digits if count == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{mantissa}e{'+' if exp >= 0 else '-'}{abs(exp)}"


def dumps(value: object, indent: int | None = None) -> str:
    """`JSON.stringify(value, null, indent)`: insertion order, no spaces unless indented."""
    return "".join(_chunks(value, indent, 0))


def _scalar(value: object) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return js_number(value) if math.isfinite(value) else "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return None


def _chunks(value: object, indent: int | None, depth: int) -> list[str]:
    scalar = _scalar(value)
    if scalar is not None:
        return [scalar]
    if isinstance(value, Mapping):
        return _object_chunks(value, indent, depth)
    if isinstance(value, Sequence):
        parts = ["".join(_chunks(entry, indent, depth + 1)) for entry in value]
        return _wrap("[", "]", parts, indent, depth)
    raise TypeError(f"cannot serialise {type(value).__name__}")


def _object_chunks(value: Mapping[str, object], indent: int | None, depth: int) -> list[str]:
    separator = ":" if indent is None else ": "
    parts = [
        json.dumps(str(key), ensure_ascii=False)
        + separator
        + "".join(_chunks(entry, indent, depth + 1))
        for key, entry in value.items()
    ]
    return _wrap("{", "}", parts, indent, depth)


def _wrap(opener: str, closer: str, parts: list[str], indent: int | None, depth: int) -> list[str]:
    if not parts:
        return [opener + closer]
    if indent is None:
        return [opener, ",".join(parts), closer]
    inner = "\n" + " " * (indent * (depth + 1))
    outer = "\n" + " " * (indent * depth)
    return [opener, inner, ("," + inner).join(parts), outer, closer]


def form_encode(params: Mapping[str, str]) -> str:
    """`URLSearchParams.toString()`: spaces are `+`, `~` is escaped, `*` is not."""

    def enc(text: str) -> str:
        return quote_plus(text, safe="*").replace("~", "%7E")

    return "&".join(f"{enc(k)}={enc(v)}" for k, v in params.items())


# --- HTTP -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpInit:
    """What a fetcher is told about one request; `timeout_s` is a hard cap for the whole call."""

    method: str
    # Carries the bearer token: kept out of repr so logging a request never leaks it.
    headers: dict[str, str] = field(repr=False)
    timeout_s: float
    body: str | None = None
    # Largest body the fetcher may read; None means MAX_RESPONSE_BYTES.
    max_bytes: int | None = None


class HttpResponse(Protocol):
    @property
    def ok(self) -> bool: ...

    @property
    def status(self) -> int: ...

    def text(self) -> str: ...


@dataclass(frozen=True, slots=True)
class StaticResponse:
    """The smallest `HttpResponse`: what the real fetcher returns and what tests build."""

    ok: bool
    status: int
    body: str

    def text(self) -> str:
        return self.body


Fetcher: TypeAlias = Callable[[str, HttpInit], HttpResponse]


_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, parts.hostname, parts.port or _DEFAULT_PORTS.get(scheme)


# No provider answer comes near this; a peer that keeps sending past it is broken or hostile.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _Budget:
    """What one `default_fetch` call may still read: a byte cap and a wall-clock deadline, shared
    by every response of the call (redirect answers included).

    The socket timeout alone restarts on every byte, so a peer dripping data would hold the thread
    (and memory) forever; `urllib` also reads a redirect's body without any limit before it
    follows, so the redirect handler reads through the budget first.
    """

    def __init__(self, deadline: float, cap: int) -> None:
        self.deadline = deadline
        self.remaining = cap
        self.expired = False
        self._sockets: list[socket.socket] = []
        self._lock = threading.Lock()

    def track(self, sock: socket.socket) -> None:
        """Remember a connection so `expire` can cut it, however the peer is stalling it."""
        with self._lock:
            self._sockets.append(sock)
            late = self.expired
        if late:
            _hang_up(sock)

    def expire(self) -> None:
        """The deadline passed: shut every connection down, which wakes any thread blocked on one.

        Reads inside `http.client` (chunk sizes, trailers) never return to our loop, and each
        arriving byte restarts the socket timeout, so only cutting the connection ends them.
        """
        with self._lock:
            self.expired = True
            sockets = list(self._sockets)
        for sock in sockets:
            _hang_up(sock)

    def seconds_left(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise OSError("timeout")
        return left

    def read(self, reply: urllib.response.addinfourl) -> bytes:
        """The whole body, or an error; a reply that is not read to its end is closed here, so a
        failed read never leaves its socket to the garbage collector."""
        chunks: list[bytes] = []
        try:
            while chunk := reply.read1(65536):
                self.remaining -= len(chunk)
                if self.remaining < 0:
                    raise OSError("response too large")
                self.seconds_left()
                chunks.append(chunk)
            self.seconds_left()  # the end of the body may itself arrive late
        except BaseException:
            reply.close()
            raise
        return b"".join(chunks)


def _hang_up(sock: socket.socket) -> None:
    # The plain socket's shutdown, not the object's own: `ssl.SSLSocket.shutdown` drops the TLS
    # layer first, and a request thread writing in that instant would send its headers (the
    # bearer token) as raw TCP. Already closed or never connected: nothing left to wake.
    with contextlib.suppress(OSError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


class _Tracked:
    """Registers the connection's socket with the call's budget as soon as it exists."""

    sock: socket.socket
    _budget: _Budget | None

    def connect(self) -> None:
        super().connect()  # type: ignore[misc]
        if self._budget is not None:
            self._budget.track(self.sock)


class _Connection(_Tracked, http.client.HTTPConnection):
    def __init__(self, host: str, *, budget: _Budget | None, timeout: float) -> None:
        super().__init__(host, timeout=timeout)
        self._budget = budget


class _SecureConnection(_Tracked, http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        *,
        budget: _Budget | None,
        timeout: float,
        context: ssl.SSLContext | None,
    ) -> None:
        super().__init__(host, timeout=timeout, context=context)
        self._budget = budget


def _budget_of(req: urllib.request.Request) -> _Budget | None:
    return getattr(req, "budget", None)


class _TrackedHttp(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        budget = _budget_of(req)
        return self.do_open(lambda host, **kw: _Connection(host, budget=budget, **kw), req)


class _TrackedHttps(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        budget = _budget_of(req)
        return self.do_open(
            lambda host, **kw: _SecureConnection(host, budget=budget, **kw),
            req,
            context=self._context,
        )


class _BudgetedRequest(urllib.request.Request):
    """A request that carries its call's budget, so the redirect handler can find it."""

    def __init__(
        self,
        url: str,
        *,
        budget: _Budget,
        data: bytes | None,
        headers: Mapping[str, str],
        method: str | None,
    ) -> None:
        super().__init__(url, data=data, headers=dict(headers), method=method)
        self.budget = budget

    @classmethod
    def following(cls, redirect: urllib.request.Request, budget: _Budget) -> _BudgetedRequest:
        """The request `urllib` built for a redirect, now under the same budget."""
        carried = cls(
            redirect.full_url,
            budget=budget,
            data=redirect.data,
            headers=redirect.headers,
            method=redirect.get_method(),
        )
        carried.unredirected_hdrs.update(redirect.unredirected_hdrs)
        carried.origin_req_host = redirect.origin_req_host
        carried.unverifiable = redirect.unverifiable
        return carried


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects like a browser's fetch: credentials never leave their origin.

    `urllib` replays every header on a redirect, which would hand a Home Assistant bearer token to
    whatever host answers `30x`. Here `Authorization` is dropped when the origin changes, and an
    https -> http downgrade is refused outright. The redirect answer's own body is read within
    the request's budget, and the deadline is checked before each hop.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme.lower() not in _SCHEMES or (
            urlsplit(req.full_url).scheme.lower() == "https" and target.scheme.lower() == "http"
        ):
            raise urllib.error.HTTPError(req.full_url, code, "unsafe redirect", headers, fp)
        # `urllib` refuses what cannot be replayed (a POST on 307/308) here, so that answer
        # surfaces as its status before any of the body is waited for.
        follow = super().redirect_request(req, fp, code, msg, headers, newurl)
        budget: _Budget | None = getattr(req, "budget", None)
        if budget is not None:
            budget.read(fp)
            # The next hop gets what is left of the deadline, not a fresh socket timeout.
            req.timeout = budget.seconds_left()
        if follow is not None and budget is not None:
            follow = _BudgetedRequest.following(follow, budget)
        if follow is not None and _origin(req.full_url) != _origin(newurl):
            follow.remove_header("Authorization")
        return follow


def _build_opener(
    *extra: urllib.request.BaseHandler, redirects: urllib.request.HTTPRedirectHandler | None = None
) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        *extra, redirects or _SafeRedirects(), _TrackedHttp(), _TrackedHttps()
    )


_OPENER = _build_opener()


def _headers_are_sendable(headers: Mapping[str, str]) -> bool:
    """No line breaks (header injection) and nothing outside latin-1, which HTTP cannot carry."""
    return all(
        "\r" not in value and "\n" not in value and all(ord(c) < 256 for c in value)
        for value in headers.values()
    )


def default_fetch(url: str, init: HttpInit) -> HttpResponse:
    if urlsplit(url).scheme.lower() not in _SCHEMES:
        raise OSError("only http and https URLs are supported")
    if not _headers_are_sendable(init.headers):
        raise OSError("invalid header value")
    data = None if init.body is None else init.body.encode("utf-8")
    budget = _Budget(time.monotonic() + init.timeout_s, init.max_bytes or MAX_RESPONSE_BYTES)
    request = _BudgetedRequest(
        url, data=data, headers=init.headers, method=init.method, budget=budget
    )
    watchdog = threading.Timer(init.timeout_s, budget.expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        with _OPENER.open(request, timeout=init.timeout_s) as reply:
            raw = budget.read(reply)
            return StaticResponse(True, reply.status, raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        error.close()
        return StaticResponse(False, error.code, "")
    except urllib.error.URLError as error:
        if budget.expired:
            raise OSError("timeout") from error
        raise OSError(str(error.reason)) from error
    except (http.client.HTTPException, OSError) as error:
        # A connection the watchdog cut surfaces as whatever the reader trips over (EOF, bad
        # framing): to the caller it is the deadline.
        if budget.expired:
            raise OSError("timeout") from error
        raise
    finally:
        watchdog.cancel()


class ProviderError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


def request_json(
    provider: str,
    url: str,
    *,
    timeout_ms: float,
    headers: Mapping[str, str] | None = None,
    body: JsonValue = None,
    fetch_impl: Fetcher | None = None,
    max_bytes: int | None = None,
) -> JsonValue:
    do_fetch = fetch_impl or default_fetch
    payload = {"accept": "application/json"}
    if body is not None:
        payload["content-type"] = "application/json"
    payload.update(headers or {})
    init = HttpInit(
        method="GET" if body is None else "POST",
        headers=payload,
        timeout_s=timeout_ms / 1000,
        body=None if body is None else dumps(body),
        max_bytes=max_bytes,
    )
    if not _headers_are_sendable(payload):
        # Refused here, before any exception can quote the value: `http.client` would put the
        # whole bearer token into its error message (and into the exception context).
        raise ProviderError(provider, "request failed (invalid header value)")
    try:
        response = do_fetch(url, init)
    except Exception as error:
        raise ProviderError(provider, f"request failed ({error})") from error
    if not response.ok:
        raise ProviderError(provider, f"HTTP {response.status}")
    try:
        return _loads(response.text())
    except (ValueError, RecursionError) as error:
        raise ProviderError(provider, "response is not JSON") from error


def _reject_constant(name: str) -> float:
    raise ValueError(name)


def _loads(text: str) -> JsonValue:
    parsed: JsonValue = json.loads(text, parse_constant=_reject_constant)
    return parsed


def parse_json(text: str) -> JsonValue | None:
    """`JSON.parse` that answers None for anything it cannot read (NaN/Infinity, garbage, depth)."""
    try:
        return _loads(text)
    except (ValueError, RecursionError):
        return None


# --- concurrency under one deadline ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of one concurrent task: a value or the error that ended it (never both)."""

    value: object = None
    error: Exception | None = None


def gather(tasks: Sequence[Callable[[], T]], timeout_s: float) -> list[Outcome]:
    """Run tasks in daemon threads and wait at most `timeout_s` in total. A task that has not
    finished by then yields `TimeoutError`; its thread is abandoned so the caller never waits."""
    outcomes: list[Outcome] = [Outcome(error=TimeoutError("deadline exceeded")) for _ in tasks]

    def work(index: int, task: Callable[[], T]) -> None:
        try:
            outcomes[index] = Outcome(value=task())
        except Exception as error:
            outcomes[index] = Outcome(error=error)

    threads = [
        threading.Thread(target=work, args=(i, task), daemon=True) for i, task in enumerate(tasks)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_s
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return list(outcomes)


def call_within(task: Callable[[], T], timeout_s: float) -> T:
    """Run one task under a hard wall-clock cap: its result, its own error, or `TimeoutError`.

    `urlopen`'s timeout is per socket operation and skips DNS, so a lone request needs this."""
    (outcome,) = gather([task], timeout_s)
    if outcome.error is not None:
        raise outcome.error
    return cast(T, outcome.value)
