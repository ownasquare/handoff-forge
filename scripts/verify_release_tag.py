"""Verify that a GitHub release tag matches the package's single version source."""

from __future__ import annotations

import sys

from handoff_forge import __version__


def validate_release_tag(tag: str) -> None:
    """Raise when ``tag`` is not the exact ``v<package version>`` release tag."""

    expected = f"v{__version__}"
    if tag != expected:
        raise ValueError(f"release tag must be {expected}, got {tag or '<empty>'}")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: verify_release_tag.py v<version>", file=sys.stderr)
        return 2
    try:
        validate_release_tag(arguments[0])
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"release tag verified: {arguments[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
