"""Backward-compatible local entry point for CubeBot."""

from cubebot.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
