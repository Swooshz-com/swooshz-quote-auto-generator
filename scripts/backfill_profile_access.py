#!/usr/bin/env python3
"""Map profile ownership in an OFFLINE JSON inventory; never connect to a DB.

Dry run writes nothing. Apply creates new output and rollback files without
overwriting the input. Mappings use workspace/profile IDs, never company names.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp.profile_access import access_state, content_profile


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class OfflineBackfill:
    def __init__(self, snapshot):
        self.snapshot = copy.deepcopy(snapshot)
        if not isinstance(snapshot, dict) or not snapshot.get("workspace_id") or not isinstance(snapshot.get("profiles"), list):
            raise ValueError("An offline workspace inventory is required.")
        ids = [row.get("id") for row in snapshot["profiles"]]
        if not all(isinstance(value, str) and value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Inventory IDs must be unique.")

    def plan(self, manifest):
        if manifest.get("workspace_id") != self.snapshot["workspace_id"]:
            raise ValueError("Workspace mismatch.")
        assignments = manifest.get("assignments", [])
        mappings = {row["profile_id"]: row["access"] for row in assignments}
        if len(mappings) != len(assignments):
            raise ValueError("Duplicate assignment.")
        ids = {row["id"] for row in self.snapshot["profiles"]}
        if set(mappings) - ids:
            raise ValueError("Unknown profile ID.")
        output = copy.deepcopy(self.snapshot)
        listing, changes = [], []
        for row in output["profiles"]:
            state = access_state(row)
            if row.get("_sqag_deleted") is True:
                if row["id"] in mappings:
                    raise ValueError("Deleted profiles cannot be assigned.")
                continue
            access = mappings.get(row["id"])
            if state == "invalid":
                raise ValueError("Malformed access metadata requires separate recovery.")
            if access is None and state == "legacy":
                raise ValueError("Unresolved profile owner; cutover is blocked.")
            if access is not None and access_state({"_access": access}) == "invalid":
                raise ValueError("Invalid assignment.")
            proposed = access if access is not None else row["_access"]
            listing.append({"profile_id": row["id"], "before": state,
                            "owner_user_id": proposed["owner_user_id"], "visibility": proposed["visibility"],
                            "narrows_workspace_visibility": proposed["visibility"] == "user" and state in {"legacy", "shared"}})
            if row.get("_access") == proposed:
                continue
            before = copy.deepcopy(row)
            row["_access"] = copy.deepcopy(proposed)
            row["_revision"] = row.get("_revision", 0) + 1
            if digest(content_profile(before)) != digest(content_profile(row)):
                raise ValueError("Quotation content changed.")
            changes.append({"profile_id": row["id"],
                            "before": {key: copy.deepcopy(before[key]) for key in ("_access", "_revision") if key in before},
                            "after_sha256": digest(row)})
        receipt = {"workspace_id": self.snapshot["workspace_id"], "changes": changes}
        return output, receipt, listing

    def restore(self, receipt):
        if receipt.get("workspace_id") != self.snapshot["workspace_id"]:
            raise ValueError("Workspace mismatch.")
        output = copy.deepcopy(self.snapshot)
        rows = {row["id"]: row for row in output["profiles"]}
        for change in receipt["changes"]:
            row = rows.get(change["profile_id"])
            if row is None or digest(row) != change["after_sha256"]:
                raise ValueError("Intervening changes block rollback.")
            before = change["before"]
            if not isinstance(before, dict) or set(before) - {"_access", "_revision"}:
                raise ValueError("Rollback may restore only access metadata.")
            for key in ("_access", "_revision"):
                row.pop(key, None)
            row.update(copy.deepcopy(before))
        return output


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, help="Offline JSON snapshot, never a database URL")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--manifest")
    action.add_argument("--rollback", help="Rollback receipt from an earlier offline apply")
    parser.add_argument("--apply", action="store_true", help="Write new offline output files")
    parser.add_argument("--output")
    parser.add_argument("--receipt")
    args = parser.parse_args(argv)
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    store = OfflineBackfill(inventory)
    if args.manifest:
        output, receipt, listing = store.plan(json.loads(Path(args.manifest).read_text(encoding="utf-8")))
    else:
        receipt = json.loads(Path(args.rollback).read_text(encoding="utf-8"))
        output = store.restore(receipt)
        listing = [{"profile_id": row["profile_id"], "action": "restore"} for row in receipt["changes"]]
    print(json.dumps({"dry_run": not args.apply, "workspace_id": inventory["workspace_id"], "profiles": listing}, sort_keys=True))
    if args.apply:
        if not args.output or (args.manifest and not args.receipt):
            parser.error("Apply requires a new --output and, for a mapping, a new --receipt.")
        paths = [Path(args.output)] + ([Path(args.receipt)] if args.manifest else [])
        if len({path.resolve() for path in paths}) != len(paths) or any(path.exists() for path in paths):
            parser.error("Output paths must be distinct and must not exist.")
        # Preserve recovery evidence first. A failed output write leaves input intact.
        if args.manifest:
            write_new(args.receipt, receipt)
        write_new(args.output, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
