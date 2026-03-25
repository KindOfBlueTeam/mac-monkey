import sys
from pathlib import Path

# Ensure the project root is importable when pytest is run from various cwd's
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mm_main  # noqa: E402


def test_backcompat_web_flag_sets_mode():
    parser = mm_main.build_parser()
    args = parser.parse_args(["--web"])
    assert args.mode == "web"


def test_backcompat_cli_flag_sets_mode():
    parser = mm_main.build_parser()
    args = parser.parse_args(["--cli"])
    assert args.mode == "cli"


def test_backcompat_test_flag_sets_mode():
    parser = mm_main.build_parser()
    args = parser.parse_args(["--test"])
    assert args.mode == "test"


def test_mode_flag_still_works():
    parser = mm_main.build_parser()
    args = parser.parse_args(["--mode", "cli"])
    assert args.mode == "cli"
