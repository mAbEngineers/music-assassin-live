"""PyInstaller entry point — `python -m assassin_live` has no importable script
file for PyInstaller to target, so this thin wrapper gives it one."""

from assassin_live.__main__ import main

if __name__ == "__main__":
    main()
