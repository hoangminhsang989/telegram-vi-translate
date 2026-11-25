from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from translator_proxy.config import DEFAULT_USER_CONFIG_PATH, load_config
from translator_proxy.server import create_http_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram PC Vietnamese translation proxy")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.json")
    parser.add_argument("--log-file", type=Path, default=None, help="Optional log file path")
    parser.add_argument("--host", default=None, help="Override listen host")
    parser.add_argument("--port", type=int, default=None, help="Override listen port")
    args = parser.parse_args()

    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    if args.log_file is not None:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

    cfg = load_config(args.config)
    if args.host:
        cfg.host = args.host
    if args.port is not None:
        cfg.port = args.port

    server = create_http_server(cfg, config_path=args.config or DEFAULT_USER_CONFIG_PATH)
    server.pretranslator.start()  # type: ignore[attr-defined]

    def _stop_handler(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _stop_handler)

    logging.info(
        "translator_proxy listening on http://%s:%s (provider=%s, target=%s)",
        cfg.host,
        server.server_port,
        cfg.provider,
        cfg.target_lang,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.pretranslator.stop()  # type: ignore[attr-defined]
        server.shutdown()
        server.server_close()
        server.translator.cache.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
