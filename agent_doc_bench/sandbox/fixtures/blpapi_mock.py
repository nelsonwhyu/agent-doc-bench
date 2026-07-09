"""A minimal stand-in for the real `blpapi` package.

Real BLPAPI requires a running Bloomberg Terminal and isn't installable from
PyPI, so there's no way to execute generated code against the real SDK. This
mock implements just enough of the Session/Request/Event/Element surface —
matching the getElement(...).appendValue(...) request-building style taught
in docs_library/blpapi/v1.md — for the tasks in task_suites/blpapi to run
end-to-end, so the execution scorer can check runtime behavior instead of
only regex-matching source text.

Scope is intentionally narrow: it covers the specific request types, fields,
and securities named in task_suites/blpapi/*.yaml. Calls outside that
surface raise NotImplementedError with a "blpapi_mock:" prefixed message, so
a scorer failure caused by a mock gap is distinguishable from a real defect
in the generated code (see execution_scorer.py, which greps for this
prefix).

This file is copied as `blpapi.py` into a throwaway sandbox directory
alongside the generated script — it is never imported directly by
agent_doc_bench itself.
"""

from __future__ import annotations


class Event:
    TIMEOUT = 0
    SESSION_STATUS = 1
    RESPONSE = 2
    PARTIAL_RESPONSE = 3

    def __init__(self, event_type, messages):
        self._event_type = event_type
        self._messages = messages

    def eventType(self):
        return self._event_type

    def __iter__(self):
        return iter(self._messages)


class Element:
    """A read/write view over a (container, key) pair, where container is a
    dict or list living inside a Request (while building a request) or a
    Message (while parsing a response).
    """

    def __init__(self, container, key):
        self._container = container
        self._key = key

    @property
    def _value(self):
        return self._container[self._key]

    def setValue(self, value):
        self._container[self._key] = value

    def appendValue(self, value):
        target = self._value
        if not isinstance(target, list):
            raise NotImplementedError(
                f"blpapi_mock: appendValue() called on non-repeating element {self._key!r}"
            )
        target.append(value)

    def hasElement(self, name):
        v = self._value
        return isinstance(v, dict) and name in v

    def getElement(self, name):
        v = self._value
        if not isinstance(v, dict) or name not in v:
            raise NotImplementedError(
                f"blpapi_mock: getElement({name!r}) not present under {self._key!r} — "
                "extend the canned response in blpapi_mock.py if this is expected."
            )
        return Element(v, name)

    def getElementAsString(self, name):
        return str(self.getElement(name)._value)

    def getElementAsFloat(self, name):
        return float(self.getElement(name)._value)

    def getElementAsInteger(self, name):
        return int(self.getElement(name)._value)

    def numValues(self):
        v = self._value
        return len(v) if isinstance(v, list) else 1

    def getValue(self, index=0):
        v = self._value
        return Element(v, index) if isinstance(v, list) else self

    def getValueAsString(self):
        return str(self._value)

    def getValueAsFloat(self):
        return float(self._value)

    def __getitem__(self, index):
        return self.getValue(index)

    def name(self):
        return self._key


class Message:
    def __init__(self, message_type, data):
        self._message_type = message_type
        self._root = {"__root__": data}
        self._element = Element(self._root, "__root__")

    def messageType(self):
        return self._message_type

    def hasElement(self, name):
        return self._element.hasElement(name)

    def getElement(self, name):
        return self._element.getElement(name)

    def correlationIds(self):
        return []


class Request:
    def __init__(self, request_type):
        self.request_type = request_type
        # securities/fields are pre-seeded as repeating (list) elements,
        # matching the appendValue() pattern in v1.md; any other field name
        # auto-vivifies to [] too, but setValue() overwrites it regardless
        # of that placeholder type, so scalar fields (periodicitySelection,
        # startDate, ...) work via either request.set(...) or
        # request.getElement(...).setValue(...).
        self._data: dict = {"securities": [], "fields": []}

    def append(self, name, value):
        self._data.setdefault(name, [])
        self._data[name].append(value)

    def set(self, name, value):
        self._data[name] = value

    def getElement(self, name):
        self._data.setdefault(name, [])
        return Element(self._data, name)


class Service:
    def __init__(self, name):
        self.name = name

    def createRequest(self, request_type):
        return Request(request_type)


_CANNED_REFDATA = {
    "AAPL US Equity": {"PX_LAST": 227.52, "NAME": "APPLE INC"},
    "INVALID_TICKER Equity": None,  # triggers a securityError, per blpapi_error_handling task
}

_CANNED_HISTORICAL = {
    "MSFT US Equity": [
        {"date": "2026-06-08", "PX_LAST": 410.12},
        {"date": "2026-06-09", "PX_LAST": 412.30},
        {"date": "2026-06-10", "PX_LAST": 408.77},
    ],
}


def _build_response_for(request: Request) -> Message:
    if request.request_type == "ReferenceDataRequest":
        securities = request._data.get("securities", [])
        security_data = []
        for sec in securities:
            canned = _CANNED_REFDATA.get(sec)
            if canned is None:
                security_data.append(
                    {
                        "security": sec,
                        "securityError": {"category": "BAD_SEC", "message": "Unknown/Invalid Security"},
                    }
                )
            else:
                security_data.append({"security": sec, "fieldData": canned})
        return Message("ReferenceDataResponse", {"securityData": security_data})

    if request.request_type == "HistoricalDataRequest":
        securities = request._data.get("securities", [])
        sec = securities[0] if securities else None
        bars = _CANNED_HISTORICAL.get(sec, [])
        return Message("HistoricalDataResponse", {"securityData": {"security": sec, "fieldData": bars}})

    raise NotImplementedError(
        f"blpapi_mock: no canned response for request type {request.request_type!r} — "
        "extend _build_response_for() in blpapi_mock.py to support it."
    )


class SessionOptions:
    def __init__(self):
        self._config: dict = {}

    def __getattr__(self, name):
        # Permissive: connection-config setters (setServerHost,
        # setServerPort, setAuthenticationOptions, ...) are accepted and
        # recorded but have no effect on mock behavior.
        def _setter(*args, **kwargs):
            self._config[name] = (args, kwargs)

        return _setter


class Session:
    def __init__(self, options=None):
        self._options = options
        self._services: dict = {}
        self._pending: list = []

    def start(self):
        self._pending.append(("session_status", None))
        return True

    def stop(self):
        return True

    def openService(self, name):
        self._services[name] = Service(name)
        return True

    def getService(self, name):
        return self._services.setdefault(name, Service(name))

    def sendRequest(self, request, correlationId=None, **kwargs):
        self._pending.append(("response", request))
        return correlationId

    def nextEvent(self, timeout=0):
        if not self._pending:
            # Mirrors real blpapi: nextEvent() returns a TIMEOUT event
            # rather than blocking forever when nothing is queued. If
            # generated code never breaks its loop on this, the sandbox's
            # own subprocess timeout will (correctly) fail the run.
            return Event(Event.TIMEOUT, [])
        kind, payload = self._pending.pop(0)
        if kind == "session_status":
            return Event(Event.SESSION_STATUS, [Message("SessionStarted", {})])
        message = _build_response_for(payload)
        return Event(Event.RESPONSE, [message])
