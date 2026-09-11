"""
Production entry point: Waitress WSGI server + rotating file log.

Why Waitress: pure-Python, runs natively on Windows Server (Gunicorn does
not), uses a handful of threads and ~30 MB of RAM, and - important for this
channel - writes NO access log by default, so client IPs never touch the disk.

Usage:
    python serve.py

The Windows service / scheduled task scripts in deploy/windows call exactly
this file. On Linux, deploy/linux/canal-compliance.service does the same.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from waitress import serve

from app import BASE_DIR, CONFIG, app, configuration_errors


def configure_logging() -> None:
    """Send our (content-free) operational log to ./logs/app.log and stdout.

    Rotation keeps at most ~6 MB on disk. Log lines contain only timestamps,
    the status of each delivery and the category - by design, nothing that
    could identify the reporter is ever passed to a logger (see app.py).
    """
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)

    # On Windows a redirected stdout defaults to the legacy code page (cp1252),
    # which turns "Ética" into mojibake in service.log. Force UTF-8 and never
    # crash on an unencodable character.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    # Waitress only logs startup info and internal errors; keep it quiet.
    logging.getLogger("waitress").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    log = logging.getLogger("canal_compliance")
    errors = configuration_errors()
    if errors:
        for error in errors:
            log.critical("Configuração inválida: %s", error)
        raise SystemExit(1)

    log.info(
        "Canal de Ética & Compliance iniciando em %s:%s (threads=%s, esquema_publico=%s, smtp=%s:%s/%s, dry_run=%s)",
        CONFIG["HOST"],
        CONFIG["PORT"],
        CONFIG["THREADS"],
        CONFIG["URL_SCHEME"],
        CONFIG["SMTP_SERVER"],
        CONFIG["SMTP_PORT"],
        CONFIG["SMTP_SECURITY"],
        CONFIG["DRY_RUN"],
    )
    if CONFIG["DRY_RUN"]:
        log.warning(
            "DRY_RUN=true: NENHUM relato será enviado por e-mail; o conteúdo será descartado. "
            "Use apenas em testes; em produção defina DRY_RUN=false no .env."
        )
    if not CONFIG["SMTP_PASSWORD"] and not CONFIG["DRY_RUN"]:
        log.warning(
            "SMTP_PASSWORD está vazio: o envio só funcionará se o servidor SMTP aceitar "
            "relay sem autenticação (ex.: relay interno na porta 25)."
        )
    serve(
        app,
        host=CONFIG["HOST"],
        port=CONFIG["PORT"],
        threads=CONFIG["THREADS"],
        url_scheme=CONFIG["URL_SCHEME"],
        ident="",
        max_request_body_size=64 * 1024,
        max_request_header_size=16 * 1024,
        channel_timeout=30,
        clear_untrusted_proxy_headers=True,
        log_untrusted_proxy_headers=False,
    )


if __name__ == "__main__":
    main()
