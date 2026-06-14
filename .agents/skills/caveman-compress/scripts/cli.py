#!/usr/bin/env python3
"""
Caveman Compress CLI

Usage:
    caveman <filepath>
"""

import sys

# Force UTF-8 on stdout/stderr before any code can print. Windows consoles
# default to cp1252 and crash on the ❌ glyphs in error/validation branches,
# masking the real error and leaving the user with a half-compressed file.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.compress import compress_file
    from scripts.detect import detect_file_type, should_compress
else:
    from .compress import compress_file
    from .detect import detect_file_type, should_compress


def print_usage():
    print("Usage: caveman <filepath>")
    print("       python -m scripts <filepath>")


def main():
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        print_usage()
        sys.exit(0)

    if len(sys.argv) != 2:
        print_usage()
        sys.exit(1)

    filepath = Path(sys.argv[1])

    # Check file exists
    if not filepath.exists():
        print(f"❌ File not found: {filepath}")
        sys.exit(1)

    if not filepath.is_file():
        print(f"❌ Not a file: {filepath}")
        sys.exit(1)

    filepath = filepath.resolve()

    # Detect file type
    file_type = detect_file_type(filepath)

    print(f"Detected: {file_type}")

    # Check if compressible
    if not should_compress(filepath):
        print("Skipping: file is not natural language (code/config)")
        sys.exit(0)

    print("Starting caveman compression...\n")

    try:
        success = compress_file(filepath)

        if success:
            print("\nCompression completed successfully")
            print(f"Compressed: {filepath}")
            sys.exit(0)
        else:
            print("\n❌ Compression failed after retries")
            sys.exit(2)

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
