"""Absolute-path entry point, independent of the caller's working directory."""

from proteus_mcp.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
