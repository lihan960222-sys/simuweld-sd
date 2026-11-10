from pathlib import Path
import sys

from runtime_modules import install

if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        install(Path(sys.executable).parent)
    from sfmarc_workbench.launcher import main
    main()
