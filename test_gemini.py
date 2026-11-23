"""Legacy CLI diagnostic entry point — delegates to test_claude.py."""

import sys
from test_claude import main

if __name__ == "__main__":
    print("[Note: BEN has migrated from Gemini to Anthropic Claude. Running test_claude.py...]\n")
    sys.exit(main() or 0)
