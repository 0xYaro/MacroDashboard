"""`macro-migrate build`: read the vault, write out/ and VERIFICATION.md."""
from __future__ import annotations

import sys

from . import config, emit, model, report, verify


def build() -> int:
    b = model.build_all()
    checks, calibration = verify.all_checks(b)
    files = emit.emit(b)
    (config.MIGRATION / "VERIFICATION.md").write_text(
        report.render(b, checks, calibration, files), encoding="utf-8")

    failed = [c for c in checks if not c.passed]
    for f in files:
        print("wrote %s" % f.relative_to(config.MIGRATION))
    print("wrote VERIFICATION.md")
    print()
    print("calibration: %s" % calibration["line"])
    print("oracles: %d checks, %d failed" % (len(checks), len(failed)))
    for c in failed:
        print("  FAIL %s | %s | %s" % (c.key, c.subject, c.detail))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "build"
    if cmd != "build":
        print(__doc__)
        return 2
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
