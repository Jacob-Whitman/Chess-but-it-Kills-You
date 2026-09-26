import logging
from dataclasses import dataclass
from enum import Enum

import requests

from .config import API_MAX_DURATION_MS, API_MAX_INTENSITY, API_MIN_DURATION_MS, API_MIN_INTENSITY

log = logging.getLogger(__name__)

# Header name comes from AuthConstants.cs in the OpenShock/API repo.
# Body field names are camelCase and the enum is sent as a string.
TOKEN_HEADER = "OpenShockToken"


class ControlType(str, Enum):
    STOP = "Stop"
    SHOCK = "Shock"
    VIBRATE = "Vibrate"
    SOUND = "Sound"


class OpenShockError(Exception):
    pass


@dataclass
class ShockerInfo:
    id: str
    name: str
    hub_name: str
    is_paused: bool


class OpenShockClient:
    def __init__(self, api_url, token, dry_run=False, timeout=10, session=None):
        self.api_url = api_url.rstrip("/")
        self.dry_run = dry_run
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            TOKEN_HEADER: token,
            "Content-Type": "application/json",
            "User-Agent": "ChessButItKillsYou/1.0",
        })

    def _request(self, method, path, **kwargs):
        try:
            r = self.session.request(method, self.api_url + path, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise OpenShockError(f"couldn't reach the OpenShock API: {e}")

        if r.status_code >= 400:
            try:
                body = r.json()
                detail = body.get("title") or body.get("message") or str(body)
            except ValueError:
                detail = r.text[:200]
            raise OpenShockError(f"{method} {path} -> HTTP {r.status_code}: {detail}")

        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def list_shockers(self):
        data = self._request("GET", "/1/shockers/own")
        found = []
        for hub in (data or {}).get("data") or []:
            for s in hub.get("shockers") or []:
                found.append(ShockerInfo(
                    id=str(s.get("id", "")),
                    name=str(s.get("name", "")),
                    hub_name=str(hub.get("name", "")),
                    is_paused=bool(s.get("isPaused", False)),
                ))
        return found

    def control(self, shocker_id, control_type, intensity, duration_ms, custom_name=None, exclusive=True):
        if not shocker_id:
            raise ValueError("no shocker id")
        if not API_MIN_INTENSITY <= intensity <= API_MAX_INTENSITY:
            raise ValueError(f"intensity {intensity} is out of range")
        if not API_MIN_DURATION_MS <= duration_ms <= API_MAX_DURATION_MS:
            raise ValueError(f"duration {duration_ms}ms is out of range")

        body = {
            "shocks": [{
                "id": shocker_id,
                "type": control_type.value,
                "intensity": int(intensity),
                "duration": int(duration_ms),
                "exclusive": exclusive,
            }],
            "customName": custom_name,
        }

        if self.dry_run:
            log.info("[dry run] would POST /2/shockers/control %s", body)
            return

        self._request("POST", "/2/shockers/control", json=body)

    def stop(self, shocker_id):
        self.control(shocker_id, ControlType.STOP, 0, API_MIN_DURATION_MS, custom_name="Chess: STOP")
