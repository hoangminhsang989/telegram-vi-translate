from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_TEMPLATE = "http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q"
OPTIONS_FILENAME = "experimental_options.json"
BACKUP_FILENAME = "experimental_options.json.vi_backup"
KEY_NAME = "translate-url-template"


def merge_experimental_options(tdata_dir: Path, template_url: str = DEFAULT_TEMPLATE) -> Path:
    """Safely add/update translate-url-template in Telegram tdata/experimental_options.json."""
    tdata_dir = Path(tdata_dir)
    tdata_dir.mkdir(parents=True, exist_ok=True)
    opt_path = tdata_dir / OPTIONS_FILENAME
    backup_path = tdata_dir / BACKUP_FILENAME

    data: dict = {}
    if opt_path.exists():
        raw = opt_path.read_text(encoding="utf-8")
        if not backup_path.exists():
            backup_path.write_text(raw, encoding="utf-8")
        if raw.strip():
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected JSON object in {opt_path}")
            data = parsed
    elif not backup_path.exists():
        # Sentinel empty marker records that no original file existed before install.
        backup_path.write_text("__ABSENT__", encoding="utf-8")

    data[KEY_NAME] = template_url
    opt_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return opt_path


def restore_experimental_options(tdata_dir: Path) -> None:
    """Restore tdata/experimental_options.json to its pre-install state."""
    tdata_dir = Path(tdata_dir)
    opt_path = tdata_dir / OPTIONS_FILENAME
    backup_path = tdata_dir / BACKUP_FILENAME

    if backup_path.exists():
        marker = backup_path.read_text(encoding="utf-8")
        if marker == "__ABSENT__":
            if opt_path.exists():
                opt_path.unlink()
        else:
            opt_path.write_text(marker, encoding="utf-8")
        backup_path.unlink()
        return

    if opt_path.exists():
        raw = opt_path.read_text(encoding="utf-8")
        if raw.strip():
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and KEY_NAME in parsed:
                parsed.pop(KEY_NAME, None)
                if parsed:
                    opt_path.write_text(
                        json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                else:
                    opt_path.unlink()


def main() -> None:
    default_tdata = Path.home() / "AppData" / "Roaming" / "Telegram Desktop" / "tdata"
    parser = argparse.ArgumentParser(description="Manage Telegram experimental_options.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="Write translate-url-template")
    group.add_argument("--uninstall", action="store_true", help="Restore prior options")
    parser.add_argument("--tdata", type=Path, default=default_tdata, help="Path to Telegram tdata")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="translate-url-template value")
    args = parser.parse_args()

    if args.install:
        out = merge_experimental_options(args.tdata, args.template)
        print(f"Updated {out}")
    else:
        restore_experimental_options(args.tdata)
        print(f"Restored {args.tdata / OPTIONS_FILENAME}")


if __name__ == "__main__":
    main()
