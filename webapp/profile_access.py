"""Profile authority comes only from persisted metadata and trusted session identity.

Unmapped legacy rows retain workspace reads and admin-only writes until an
explicit offline mapping. Malformed metadata fails closed for every role.
"""
from dataclasses import dataclass
import copy
import hashlib
import json

RESERVED = frozenset({"_access", "_revision", "permissions"})


def content_profile(profile):
    return {key: copy.deepcopy(value) for key, value in profile.items() if key not in RESERVED}


def access_state(profile):
    if not isinstance(profile, dict) or profile.get("_sqag_deleted") is True:
        return "invalid"
    if "_access" not in profile:
        return "legacy"
    value = profile["_access"]
    if (not isinstance(value, dict) or set(value) != {"version", "owner_user_id", "visibility"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["visibility"], str) or value["visibility"] not in {"user", "shared"}
            or not (value["owner_user_id"] is None or isinstance(value["owner_user_id"], str))):
        return "invalid"
    owner = value["owner_user_id"]
    if owner is not None and (not owner.strip() or owner != owner.strip()):
        return "invalid"
    if value["visibility"] == "user" and not owner:
        return "invalid"
    return value["visibility"]


def historical_profile_authority(manifest, *, run_id, job_id, workspace_id):
    """Reconstruct only profile authority from server-verified run evidence.

    The storage caller verifies the evidence envelope checksum, run/version
    linkage and persisted proof. This never authorises a live profile read.
    """
    if (not isinstance(manifest, dict) or manifest.get("generation_run_id") != run_id
            or manifest.get("workspace_id") != workspace_id or manifest.get("job_id") != job_id):
        return {}
    profile = manifest.get("profile")
    layout = manifest.get("layout_template")
    rules = manifest.get("layout_rules")
    if not all(isinstance(value, dict) for value in (profile, layout, rules)):
        return {}
    snapshot = profile.get("snapshot")
    if not isinstance(snapshot, dict) or not isinstance(rules.get("snapshot"), dict):
        return {}
    def checksum(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    profile_digest = checksum(content_profile(snapshot))
    rules_digest = checksum(rules["snapshot"])
    if profile_digest != profile.get("sha256") or rules_digest != rules.get("sha256"):
        return {}
    source = snapshot.get("source", "company")
    identity = snapshot.get("id")
    if not isinstance(source, str) or source not in {"company", "profile"} or not isinstance(identity, str) or not identity:
        return {}
    return {"id": f"{source}:{identity}", "source": source, "digest": profile_digest,
            "layout_digest": layout.get("sha256"), "layout_rules_digest": rules_digest}


@dataclass(frozen=True)
class ProfilePolicy:
    user_id: str
    role: str

    @property
    def admin(self):
        return self.role in {"admin", "management"}

    @property
    def can_create(self):
        return bool(self.user_id) and self.role in {"operator", "admin", "management"}

    def can_read(self, profile):
        state = access_state(profile)
        if state == "invalid" or self.role not in {"viewer", "operator", "admin", "management"}:
            return False
        if state == "legacy":
            return True
        if not self.user_id:
            return False
        return state == "shared" or self.admin or profile["_access"]["owner_user_id"] == self.user_id

    def can_update(self, profile):
        state = access_state(profile)
        return self.can_read(profile) and (self.admin or (
            self.can_create and state == "user" and profile["_access"]["owner_user_id"] == self.user_id))

    def capabilities(self, profile):
        return {"canRead": self.can_read(profile), "canUpdate": self.can_update(profile),
                "canDelete": self.can_update(profile), "canExport": self.can_read(profile)}

    def new_access(self):
        if not self.can_create:
            raise PermissionError("Profile operation is unavailable.")
        return {"version": 1, "owner_user_id": self.user_id, "visibility": "user"}
