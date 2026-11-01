"""PyInstaller entry point."""
import multiprocessing

from localforge.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
