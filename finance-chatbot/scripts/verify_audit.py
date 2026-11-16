"""Check an audit log's hash chains: python scripts/verify_audit.py audit.jsonl

Exit code 0 if every chain is intact, 1 at the first deleted, reordered or changed record.
Set FINBOT_AUDIT_KEY to the key the log was written with (if any).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.auditlog import verify


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[0])
        return 2
    with open(sys.argv[1]) as f:
        ok, msg = verify(f, os.environ.get("FINBOT_AUDIT_KEY"))
    print(("OK: " if ok else "TAMPERED: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
