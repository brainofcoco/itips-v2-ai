"""Onboard face-recognition database client for Dahua cameras.

This replaces the Jetson-side InsightFace personnel cache. Worker faces
live on the camera in a Face Group; recognition happens entirely on the
camera silicon. We just push/pull membership.

Reference: Dahua HTTP API V3.98, `faceRecognitionServer.cgi`.

Group lifecycle
---------------
* One canonical "workers" group per site, replicated to every camera.
* `ensure_workers_group()` is idempotent: creates the group on first run,
  returns the existing groupID afterwards.
* `bind_group_to_channel()` deploys the group to the camera's video
  channel with a similarity threshold; the camera fires
  `FaceRecognition` events only against that group.

Personnel CRUD
--------------
* `add_person(name, jpeg)` POSTs the JPEG as the multipart body.
* `delete_person(uid)` removes one worker.
* `list_persons()` iterates the group via startFind/doFind/stopFind so we
  can show the operator what's enrolled on each camera.

Notes
-----
* The camera does its own feature extraction. We never send embeddings;
  we send the raw JPEG and let the WizMind chip do the work.
* `Similarity` in the FaceRecognition event is 0–100. The group-binding
  threshold (`similary` in the API, sic — Dahua's typo) gates whether a
  match is reported at all.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from itips.camera.dahua_http import DahuaCameraEndpoint

logger = logging.getLogger(__name__)

DEFAULT_GROUP_NAME = "ITIPS_Workers"
DEFAULT_SIMILARITY = 80  # 0–100, matches Dahua "similary"


@dataclass(frozen=True)
class Person:
    uid: str
    group_id: str
    name: str
    sex: Optional[str] = None
    has_image: bool = False


class DahuaFaceDB:
    """Thin client for one camera's faceRecognitionServer.cgi.

    Methods raise `DahuaFaceDBError` on protocol failures. Network errors
    propagate as `requests.RequestException` so the caller can decide
    whether to retry or skip the camera.
    """

    def __init__(self, endpoint: DahuaCameraEndpoint, *, timeout: float = 8.0) -> None:
        self._endpoint = endpoint
        self._timeout = timeout

    # ─── group lifecycle ──────────────────────────────────────────────

    def list_groups(self) -> list[dict]:
        """Return every face group on the camera."""
        r = self._endpoint.get(
            "/cgi-bin/faceRecognitionServer.cgi",
            params={"action": "findGroup"},
            timeout=self._timeout,
        )
        r.raise_for_status()
        return _parse_group_list(r.text)

    def find_group_id(self, group_name: str) -> Optional[str]:
        for group in self.list_groups():
            if group.get("groupName") == group_name:
                return group.get("groupID")
        return None

    def create_group(self, name: str, *, detail: str = "") -> str:
        r = self._endpoint.get(
            "/cgi-bin/faceRecognitionServer.cgi",
            params={"action": "createGroup", "groupName": name, "groupDetail": detail},
            timeout=self._timeout,
        )
        r.raise_for_status()
        m = re.search(r"groupID=([^\s]+)", r.text)
        if not m:
            raise DahuaFaceDBError(f"createGroup response missing groupID: {r.text!r}")
        return m.group(1).strip()

    def ensure_workers_group(self, name: str = DEFAULT_GROUP_NAME) -> str:
        """Idempotent. Returns the groupID for the named workers group."""
        existing = self.find_group_id(name)
        if existing:
            return existing
        return self.create_group(name, detail="ITIPS personnel allowlist")

    def delete_group(self, group_id: str) -> None:
        r = self._endpoint.get(
            "/cgi-bin/faceRecognitionServer.cgi",
            params={"action": "deleteGroup", "groupID": group_id},
            timeout=self._timeout,
        )
        r.raise_for_status()

    def bind_group_to_channel(
        self,
        group_id: str,
        *,
        channel: int = 1,
        similarity: int = DEFAULT_SIMILARITY,
    ) -> None:
        """Deploy a group to a video channel. Overwrites prior dispositions."""
        r = self._endpoint.get(
            "/cgi-bin/faceRecognitionServer.cgi",
            params={
                "action": "setGroup",
                "channel": channel,
                "list[0].groupID": group_id,
                "list[0].similary": int(similarity),
            },
            timeout=self._timeout,
        )
        r.raise_for_status()

    # ─── person CRUD ──────────────────────────────────────────────────

    def add_person(
        self,
        *,
        group_id: str,
        name: str,
        jpeg: bytes,
        sex: Optional[str] = None,
        birthday: Optional[str] = None,
        cert_type: Optional[str] = None,
        cert_id: Optional[str] = None,
    ) -> str:
        """Upload a worker face. Returns the new UID."""
        params: dict[str, str] = {
            "action": "addPerson",
            "groupID": group_id,
            "name": name,
        }
        if sex:
            params["sex"] = sex
        if birthday:
            params["birthday"] = birthday
        if cert_type:
            params["certificateType"] = cert_type
        if cert_id:
            params["id"] = cert_id
        r = self._endpoint.post(
            "/cgi-bin/faceRecognitionServer.cgi",
            params=params,
            data=jpeg,
            headers={"Content-Type": "image/jpeg", "Content-Length": str(len(jpeg))},
            timeout=self._timeout,
        )
        r.raise_for_status()
        m = re.search(r"uid=([^\s]+)", r.text)
        if not m:
            raise DahuaFaceDBError(f"addPerson response missing uid: {r.text!r}")
        return m.group(1).strip()

    def delete_person(self, *, group_id: str, uid: str) -> None:
        r = self._endpoint.get(
            "/cgi-bin/faceRecognitionServer.cgi",
            params={"action": "deletePerson", "uid": uid, "groupID": group_id},
            timeout=self._timeout,
        )
        r.raise_for_status()

    def list_persons(self, *, group_id: str, batch: int = 50) -> list[Person]:
        """Page through every person in a group.

        Uses startFind → doFind → stopFind. We deliberately swallow the
        multipart JPEG sections from doFind because the dashboard pulls
        thumbnails on demand via a separate endpoint, and parsing them
        here would double the per-request payload.
        """
        token = self._start_find(group_id)
        if token is None:
            return []
        out: list[Person] = []
        try:
            index = 0
            while True:
                got = self._do_find_one(token, index)
                if got is None:
                    break
                out.append(got)
                index += 1
                if len(out) >= batch * 100:  # hard ceiling
                    break
        finally:
            self._stop_find(token)
        return out

    # ─── internals ────────────────────────────────────────────────────

    def _start_find(self, group_id: str) -> Optional[str]:
        r = self._endpoint.get(
            "/cgi-bin/faceRecognitionServer.cgi",
            params={"action": "startFind", "condition.GroupID[0]": group_id},
            timeout=self._timeout,
        )
        if r.status_code != 200:
            return None
        m = re.search(r"token=([^\s]+)", r.text)
        return m.group(1).strip() if m else None

    def _stop_find(self, token: str) -> None:
        try:
            self._endpoint.get(
                "/cgi-bin/faceRecognitionServer.cgi",
                params={"action": "stopFind", "token": token},
                timeout=self._timeout,
            )
        except Exception:  # token expires automatically anyway
            pass

    def _do_find_one(self, token: str, index: int) -> Optional[Person]:
        r = self._endpoint.get(
            "/cgi-bin/faceRecognitionServer.cgi",
            params={"action": "doFind", "token": token, "index": index},
            timeout=self._timeout,
            stream=True,
        )
        if r.status_code != 200:
            return None
        text = r.text  # multipart; the text/plain part is enough for us
        if "person.UID" not in text:
            return None
        return Person(
            uid=_grab(text, "person.UID") or "",
            group_id=_grab(text, "person.GroupID") or "",
            name=_grab(text, "person.Name") or "",
            sex=_grab(text, "person.Sex"),
            has_image="Content-Type: image/jpeg" in text,
        )


class DahuaFaceDBError(RuntimeError):
    """Protocol-level failure (malformed response, unexpected body)."""


# ─── parsing helpers ──────────────────────────────────────────────────


def _parse_group_list(body: str) -> list[dict]:
    """Parse `GroupList[N].field=value` lines into list-of-dicts."""
    groups: dict[int, dict] = {}
    pattern = re.compile(r"GroupList\[(\d+)\]\.([A-Za-z]+)=(.*)")
    for line in body.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        idx, field, value = int(m.group(1)), m.group(2), m.group(3).strip()
        groups.setdefault(idx, {})[field] = value
    return [groups[k] for k in sorted(groups.keys())]


def _grab(text: str, key: str) -> Optional[str]:
    """Pull a `key=value` line out of a Dahua text block."""
    m = re.search(rf"^{re.escape(key)}=(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else None
