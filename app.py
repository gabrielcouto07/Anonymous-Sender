"""
Canal de Denúncias e Relatos Éticos (Compliance)
=======================================================

Ultra-light internal web application that receives an ethics / compliance
report from an employee and forwards it *directly* to the Compliance mailbox
via authenticated SMTP.

Privacy-by-design decisions (why the code looks the way it does):

* NO database and NO file storage of reports. The e-mail *is* the record; the
  application server keeps nothing on disk that could identify the reporter.
* NO session, NO cookies. Flask only emits a cookie when `session` is used,
  and we never use it.
* NO access log. Waitress (production server) does not write access logs by
  default, and the Flask dev server's request logger is silenced below. Our own
  log lines never contain the reporter's name, message, category, anonymity
  choice, IP or User-Agent.
* NO external assets. CSS/JS/logo are served from ./static so the browser
  never contacts a CDN, and a strict Content-Security-Policy is applied.
* Abuse protection that does NOT identify users: a *global* sliding-window
  rate limit (counts submissions, not people) and a honeypot field for bots.

Configuration comes from environment variables or a `.env` file next to this
script (see `.env.example`).

Command-line helpers:
    python app.py --check-config   # print effective configuration (password masked)
    python app.py --test-email     # send a test e-mail using the configured SMTP
    python app.py                  # run the Flask DEV server (use serve.py in production)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import smtplib
import ssl
import sys
import threading
import time
from collections import deque
from collections.abc import Mapping
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE, '#' comments, optional quotes).

    Implemented in a few lines instead of adding `python-dotenv`, keeping the
    runtime dependency set small. Existing environment variables always win
    over the file, which is the conventional behaviour.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")

_TRUTHY = {"1", "true", "yes", "on", "sim"}


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in _TRUTHY
    return cast(raw)


CONFIG = {
    # HTTP
    "HOST": _env("HOST", "127.0.0.1"),
    "PORT": _env("PORT", 8980, int),
    "THREADS": _env("THREADS", 4, int),
    # Set URL_SCHEME=https only when Waitress is reachable exclusively through
    # a TLS reverse proxy. PUBLIC_ORIGIN makes Origin validation independent of
    # the proxy's internal Host header.
    "URL_SCHEME": _env("URL_SCHEME", "http").strip().lower(),
    "REQUIRE_HTTPS": _env("REQUIRE_HTTPS", False, bool),
    "PUBLIC_ORIGIN": _env("PUBLIC_ORIGIN", "").strip(),
    # SMTP
    "SMTP_SERVER": _env("SMTP_SERVER", "smtp.office365.com"),
    "SMTP_PORT": _env("SMTP_PORT", 587, int),
    "SMTP_SECURITY": _env("SMTP_SECURITY", "starttls")
    .strip()
    .lower(),  # starttls | ssl | none
    "SMTP_USER": _env("SMTP_USER", "chamados@scientificdental.com"),
    "SMTP_PASSWORD": _env("SMTP_PASSWORD", ""),
    "SMTP_TIMEOUT": _env("SMTP_TIMEOUT", 30, int),
    "MAIL_FROM": _env("MAIL_FROM", _env("SMTP_USER", "chamados@scientificdental.com")),
    "MAIL_TO": _env("MAIL_TO", "compliance@scientificdental.com"),
    "SUBJECT_PREFIX": _env(
        "SUBJECT_PREFIX", "[CANAL DE ÉTICA & COMPLIANCE] Novo Relato Registrado"
    ),
    # Behaviour
    "TZ_NAME": _env("TZ_NAME", "America/Sao_Paulo"),
    "COMPANY_NAME": _env("COMPANY_NAME", "Scientific Dental"),
    "MAX_MESSAGE_CHARS": _env("MAX_MESSAGE_CHARS", 10000, int),
    "MIN_MESSAGE_CHARS": _env("MIN_MESSAGE_CHARS", 20, int),
    "MAX_NAME_CHARS": _env("MAX_NAME_CHARS", 120, int),
    # Global (non-identifying) abuse protection: at most RATE_LIMIT_MAX
    # submissions per RATE_LIMIT_WINDOW seconds across the whole application.
    "RATE_LIMIT_MAX": _env("RATE_LIMIT_MAX", 30, int),
    "RATE_LIMIT_WINDOW": _env("RATE_LIMIT_WINDOW", 600, int),
    # DRY_RUN=true discards the e-mail instead of sending it (setup tests).
    # The body is deliberately never logged.
    "DRY_RUN": _env("DRY_RUN", False, bool),
}

CATEGORIES = (
    "Conduta Ética / Assédio",
    "Segurança da Cadeia Logística",
    "Fraude / Desvio",
    "Outros",
)

# Hidden honeypot field. Real users never see it; simple bots fill every input
# they find. Deliberately *not* called "website"/"url"/"email" so that browser
# autofill never touches it.
HONEYPOT_FIELD = "contato_alt"

SUCCESS_TEXT_ANONYMOUS = (
    "Seu relato foi encaminhado com sucesso ao Comitê de Compliance "
    "com garantia de anonimato."
)
SUCCESS_TEXT_IDENTIFIED = (
    "Seu relato identificado foi encaminhado com sucesso ao Comitê de Compliance "
    "e será tratado com confidencialidade."
)
SUCCESS_TEXT_NEUTRAL = "Seu relato foi encaminhado com sucesso ao Comitê de Compliance."

log = logging.getLogger("canal_compliance")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def now_local() -> datetime:
    """Current time in the configured time zone, falling back to server local time.

    Why zoneinfo: a Windows VM may run in UTC while Compliance expects
    Brasília time in the e-mail. `tzdata` (pure Python) supplies the IANA
    database on Windows, where the OS does not ship one.
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(CONFIG["TZ_NAME"]))
    except Exception:  # noqa: BLE001 - any failure -> local time is acceptable
        return datetime.now().astimezone()


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_name(value: str | None) -> str:
    """Single line, no control characters, whitespace collapsed, length-capped."""
    value = (value or "").replace("\r", " ").replace("\n", " ")
    value = _CONTROL_CHARS.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[: CONFIG["MAX_NAME_CHARS"]]


def clean_message(value: str | None) -> str:
    """Normalise line endings and strip control characters (keeps newline and tab)."""
    value = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _CONTROL_CHARS.sub("", value)
    return value.strip()


def fmt_int(value: int) -> str:
    """10000 -> '10.000' (pt-BR thousands separator)."""
    return f"{value:,}".replace(",", ".")


def build_subject(category: str) -> str:
    return f"{CONFIG['SUBJECT_PREFIX']} - {category}"


def build_body(name: str, category: str, message: str, when: datetime) -> str:
    """Plain-text body in the exact layout required by the Compliance team."""
    sep = "=" * 50
    stamp = when.strftime("%d/%m/%Y às %H:%M:%S")
    if name:
        title = "RELATO DE COMPLIANCE"
        who = name
    else:
        title = "RELATO DE COMPLIANCE (ANÔNIMO)"
        who = "NÃO INFORMADO (RELATO 100% ANÔNIMO)"
    return (
        f"{sep}\n"
        f"{title}\n"
        f"{sep}\n"
        f"Identificação do Relator: {who}\n"
        f"Categoria: {category}\n"
        f"Data e Hora do Registro: {stamp}\n"
        f"{sep}\n"
        f"\n"
        f"DESCRIÇÃO DO RELATO:\n"
        f"{message}\n"
    )


def send_email(subject: str, body: str) -> None:
    """Deliver one plain-text e-mail through the configured SMTP account.

    Only server-side information is ever placed in headers (From/To/Date/
    Message-ID). Nothing from the HTTP request reaches the SMTP layer except
    the sanitised body text, so header injection is impossible by construction.
    """
    msg = EmailMessage()
    msg["From"] = CONFIG["MAIL_FROM"]
    msg["To"] = CONFIG["MAIL_TO"]
    msg["Subject"] = subject
    msg["Date"] = format_datetime(now_local())
    domain = CONFIG["MAIL_FROM"].rsplit("@", 1)[-1] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(body, charset="utf-8")

    if CONFIG["DRY_RUN"]:
        log.info(
            "DRY_RUN ativo - e-mail não enviado; conteúdo descartado sem gravação."
        )
        return

    server, port, timeout = (
        CONFIG["SMTP_SERVER"],
        CONFIG["SMTP_PORT"],
        CONFIG["SMTP_TIMEOUT"],
    )
    security = CONFIG["SMTP_SECURITY"]
    context = ssl.create_default_context()

    if security not in {"starttls", "ssl", "none"}:
        raise ValueError(f"SMTP_SECURITY inválido: {security!r}")
    if security == "none" and CONFIG["SMTP_PASSWORD"]:
        raise ValueError("Autenticação SMTP sem TLS não é permitida")

    if security == "ssl":
        smtp = smtplib.SMTP_SSL(server, port, timeout=timeout, context=context)
    else:
        smtp = smtplib.SMTP(server, port, timeout=timeout)

    with smtp:
        smtp.ehlo()
        if security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if CONFIG["SMTP_USER"] and CONFIG["SMTP_PASSWORD"]:
            smtp.login(CONFIG["SMTP_USER"], CONFIG["SMTP_PASSWORD"])
        smtp.send_message(msg)


class GlobalRateLimiter:
    """Sliding-window limiter that counts *submissions*, never *who* submitted.

    A per-IP limiter would force us to hold IP addresses in memory, which
    contradicts the anonymity promise. A global cap is enough to protect the
    SMTP account from a runaway script and costs a few bytes of RAM.
    """

    def __init__(self) -> None:
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        limit, window = CONFIG["RATE_LIMIT_MAX"], CONFIG["RATE_LIMIT_WINDOW"]
        now = time.monotonic()
        with self._lock:
            while self._events and now - self._events[0] > window:
                self._events.popleft()
            if len(self._events) >= limit:
                return False
            self._events.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = GlobalRateLimiter()


def _normalise_origin(value: str) -> str:
    """Return a comparable HTTP(S) origin, or an empty string if malformed."""
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    except ValueError:
        return ""


def same_origin(req) -> bool:
    """Reject cross-site POSTs using the Origin header (read, compared, discarded).

    Without cookies there is no session to bind a CSRF token to, so the
    browser-supplied Origin header is the lightweight alternative. Requests
    without an Origin (old clients, curl during setup) are allowed.
    """
    origin = req.headers.get("Origin")
    if not origin:
        return True
    expected = CONFIG["PUBLIC_ORIGIN"] or f"{req.scheme}://{req.host}"
    return bool(_normalise_origin(origin)) and _normalise_origin(
        origin
    ) == _normalise_origin(expected)


def configuration_errors() -> list[str]:
    """Validate settings that would otherwise fail silently or weaken transport."""
    errors: list[str] = []
    if CONFIG["SMTP_SECURITY"] not in {"starttls", "ssl", "none"}:
        errors.append("SMTP_SECURITY deve ser starttls, ssl ou none.")
    if CONFIG["URL_SCHEME"] not in {"http", "https"}:
        errors.append("URL_SCHEME deve ser http ou https.")
    if CONFIG["REQUIRE_HTTPS"] and CONFIG["URL_SCHEME"] != "https":
        errors.append("REQUIRE_HTTPS=true exige URL_SCHEME=https.")
    if CONFIG["REQUIRE_HTTPS"] and not CONFIG["PUBLIC_ORIGIN"]:
        errors.append("REQUIRE_HTTPS=true exige PUBLIC_ORIGIN explícita.")
    if CONFIG["REQUIRE_HTTPS"] and CONFIG["HOST"].lower() not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        errors.append(
            "Com REQUIRE_HTTPS=true, HOST deve apontar para o loopback atrás do proxy TLS."
        )
    if CONFIG["PUBLIC_ORIGIN"] and not _normalise_origin(CONFIG["PUBLIC_ORIGIN"]):
        errors.append(
            "PUBLIC_ORIGIN deve conter apenas uma origem HTTP(S), sem caminho."
        )
    if (
        CONFIG["REQUIRE_HTTPS"]
        and CONFIG["PUBLIC_ORIGIN"]
        and not CONFIG["PUBLIC_ORIGIN"].lower().startswith("https://")
    ):
        errors.append("REQUIRE_HTTPS=true exige PUBLIC_ORIGIN com https://.")
    for key in (
        "PORT",
        "THREADS",
        "SMTP_PORT",
        "SMTP_TIMEOUT",
        "MAX_MESSAGE_CHARS",
        "MIN_MESSAGE_CHARS",
        "MAX_NAME_CHARS",
        "RATE_LIMIT_MAX",
        "RATE_LIMIT_WINDOW",
    ):
        if CONFIG[key] <= 0:
            errors.append(f"{key} deve ser maior que zero.")
    if CONFIG["PORT"] > 65535 or CONFIG["SMTP_PORT"] > 65535:
        errors.append("PORT e SMTP_PORT devem estar entre 1 e 65535.")
    if CONFIG["MIN_MESSAGE_CHARS"] > CONFIG["MAX_MESSAGE_CHARS"]:
        errors.append("MIN_MESSAGE_CHARS não pode ser maior que MAX_MESSAGE_CHARS.")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(CONFIG["TZ_NAME"])
    except Exception:  # noqa: BLE001 - report every invalid/missing timezone source
        errors.append(
            "TZ_NAME deve identificar um fuso IANA válido, como America/Sao_Paulo."
        )
    if not CONFIG["MAIL_FROM"]:
        errors.append("MAIL_FROM não pode ficar vazio.")
    if not CONFIG["MAIL_TO"]:
        errors.append("MAIL_TO não pode ficar vazio.")
    if not CONFIG["SMTP_SERVER"]:
        errors.append("SMTP_SERVER não pode ficar vazio.")
    for key in ("SMTP_USER", "MAIL_FROM", "MAIL_TO", "SUBJECT_PREFIX"):
        if "\r" in CONFIG[key] or "\n" in CONFIG[key]:
            errors.append(f"{key} não pode conter quebra de linha.")
    if not CONFIG["DRY_RUN"] and bool(CONFIG["SMTP_USER"]) != bool(
        CONFIG["SMTP_PASSWORD"]
    ):
        errors.append(
            "SMTP_USER e SMTP_PASSWORD devem ser preenchidos juntos, ou ambos ficar vazios para relay."
        )
    if CONFIG["SMTP_SECURITY"] == "none" and CONFIG["SMTP_PASSWORD"]:
        errors.append(
            "Credenciais SMTP não podem ser enviadas com SMTP_SECURITY=none; use TLS ou relay sem senha."
        )
    return errors


# --------------------------------------------------------------------------- #
# Flask application
# --------------------------------------------------------------------------- #


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        template_folder=str(BASE_DIR / "templates"),
    )
    app.config["MAX_CONTENT_LENGTH"] = (
        64 * 1024
    )  # generous for 10k chars, tiny for abuse
    app.json.ensure_ascii = False

    # The Flask/Werkzeug dev server logs every request *with the client IP*.
    # Silence it so that even development runs leave no such trace.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.after_request
    def harden(response):
        # A strict CSP is possible because all CSS/JS live in ./static (no inline code).
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        # Never let a browser or proxy cache a page of this channel.
        response.headers["Cache-Control"] = "no-store"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def find_logo() -> str:
        """Return the logo file name in ./static, or '' when none is installed."""
        return "logo.png" if (BASE_DIR / "static" / "logo.png").is_file() else ""

    @app.get("/")
    def index():
        logo_file = find_logo()
        return render_template(
            "index.html",
            categories=CATEGORIES,
            max_chars=CONFIG["MAX_MESSAGE_CHARS"],
            max_chars_fmt=fmt_int(CONFIG["MAX_MESSAGE_CHARS"]),
            min_chars=CONFIG["MIN_MESSAGE_CHARS"],
            max_name=CONFIG["MAX_NAME_CHARS"],
            company=CONFIG["COMPANY_NAME"],
            logo_file=logo_file,
            honeypot=HONEYPOT_FIELD,
        )

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    def respond(ok: bool, text: str, status: int):
        """JSON for the fetch() front-end, HTML page when JavaScript is disabled."""
        wants_json = request.is_json or "application/json" in request.headers.get(
            "Accept", ""
        )
        if wants_json:
            payload = (
                {"ok": True, "message": text} if ok else {"ok": False, "error": text}
            )
            return jsonify(payload), status
        return render_template(
            "resultado.html",
            ok=ok,
            text=text,
            company=CONFIG["COMPANY_NAME"],
            logo_file=find_logo(),
        ), status

    @app.before_request
    def enforce_https():
        # /health remains available to a local reverse-proxy probe. All pages
        # carrying report content fail closed if production TLS is miswired.
        if (
            CONFIG["REQUIRE_HTTPS"]
            and not request.is_secure
            and request.path != "/health"
        ):
            return respond(
                False,
                "Este canal aceita relatos somente por uma conexão HTTPS segura.",
                400,
            )

    @app.post("/enviar")
    def enviar():
        if not same_origin(request):
            return respond(False, "Origem da requisição não permitida.", 403)

        data = request.get_json(silent=True) if request.is_json else request.form
        data = data or {}
        if not isinstance(data, Mapping):
            return respond(False, "Formato de envio inválido.", 400)

        field_names = ("nome", "categoria", "mensagem", HONEYPOT_FIELD)
        if any(
            key in data and not isinstance(data.get(key), str) for key in field_names
        ):
            return respond(False, "Os campos do relato devem conter apenas texto.", 400)

        # Honeypot: bots fill it, humans never see it. Answer with the normal
        # success text so the bot learns nothing, but send nothing.
        if data.get(HONEYPOT_FIELD, "").strip():
            return respond(True, SUCCESS_TEXT_NEUTRAL, 200)

        name = clean_name(data.get("nome", ""))
        category = data.get("categoria", "").strip()
        message = clean_message(data.get("mensagem", ""))

        if category not in CATEGORIES:
            return respond(False, "Selecione uma categoria válida para o relato.", 400)
        if len(message) < CONFIG["MIN_MESSAGE_CHARS"]:
            return respond(
                False,
                f"Descreva o relato com pelo menos {CONFIG['MIN_MESSAGE_CHARS']} caracteres.",
                400,
            )
        if len(message) > CONFIG["MAX_MESSAGE_CHARS"]:
            return respond(
                False,
                f"O relato excede o limite de {fmt_int(CONFIG['MAX_MESSAGE_CHARS'])} caracteres.",
                400,
            )
        if not rate_limiter.allow():
            log.warning(
                "Limite global de envios atingido; envio recusado temporariamente."
            )
            return respond(
                False,
                "O canal recebeu muitos relatos em pouco tempo. Aguarde alguns minutos e tente novamente.",
                429,
            )

        when = now_local()
        try:
            send_email(
                build_subject(category), build_body(name, category, message, when)
            )
        except smtplib.SMTPAuthenticationError:
            log.error(
                "Falha SMTP: autenticação recusada (verifique SMTP_USER/SMTP_PASSWORD e se "
                "'SMTP autenticado' está habilitado para a caixa remetente)."
            )
            return respond(
                False,
                "Não foi possível encaminhar o relato agora (falha de autenticação no servidor "
                "de e-mail). Tente novamente mais tarde ou procure o RH/Compliance.",
                502,
            )
        except (smtplib.SMTPException, OSError) as exc:
            # OSError covers DNS, connection-refused and timeout errors. Log the *type* only.
            log.error("Falha SMTP: %s", type(exc).__name__)
            return respond(
                False,
                "Não foi possível encaminhar o relato agora (servidor de e-mail indisponível). "
                "Tente novamente em alguns minutos.",
                502,
            )

        # No per-submission success log: even category/timestamp metadata can
        # become identifying when only one person is using the channel.
        success_text = SUCCESS_TEXT_IDENTIFIED if name else SUCCESS_TEXT_ANONYMOUS
        return respond(True, success_text, 200)

    return app


app = create_app()


# --------------------------------------------------------------------------- #
# Command-line helpers
# --------------------------------------------------------------------------- #


def print_config() -> None:
    for key, value in CONFIG.items():
        if key == "SMTP_PASSWORD":
            value = "********" if value else "(vazio)"
        print(f"{key:18} = {value}")


def send_test_email() -> int:
    body = build_body(
        "TESTE DE CONFIGURAÇÃO (gerado pelo servidor)",
        "Outros",
        "Este é um e-mail de teste do Canal de Ética & Compliance. "
        "Se você o recebeu, o SMTP está configurado corretamente.",
        now_local(),
    )
    try:
        send_email(build_subject("Outros") + " [TESTE]", body)
    except Exception as exc:  # noqa: BLE001 - CLI: show everything to the operator
        print(f"FALHA ao enviar e-mail de teste: {type(exc).__name__}: {exc}")
        return 1
    print(
        f"E-mail de teste enviado para {CONFIG['MAIL_TO']} via "
        f"{CONFIG['SMTP_SERVER']}:{CONFIG['SMTP_PORT']} ({CONFIG['SMTP_SECURITY']})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canal de Ética & Compliance")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Mostra a configuração efetiva e sai.",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Envia um e-mail de teste com a configuração SMTP atual.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    errors = configuration_errors()

    if args.check_config:
        print_config()
        if errors:
            print("\nCONFIGURAÇÃO INVÁLIDA:")
            for error in errors:
                print(f"- {error}")
        return 1 if errors else 0
    if errors:
        for error in errors:
            log.error("Configuração inválida: %s", error)
        return 1
    if args.test_email:
        return send_test_email()

    print("AVISO: servidor de DESENVOLVIMENTO. Em produção use:  python serve.py")
    app.run(host=CONFIG["HOST"], port=CONFIG["PORT"], debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
