"""
Automated tests for the Canal de Ética & Compliance.

Run from the project root:
    venv\\Scripts\\python.exe -m unittest discover -s tests -v      (Windows)
    venv/bin/python -m unittest discover -s tests -v                (Linux)

No network is used: `send_email` / `smtplib.SMTP` are mocked, so the suite
verifies formatting, validation, privacy headers and the SMTP call sequence
without ever contacting a mail server.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as appmod

VALID = {
    "nome": "",
    "categoria": "Fraude / Desvio",
    "mensagem": "Relato de teste com tamanho suficiente.",
}


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = dict(appmod.CONFIG)
        appmod.CONFIG.update(
            DRY_RUN=False, REQUIRE_HTTPS=False, URL_SCHEME="http", PUBLIC_ORIGIN=""
        )
        appmod.rate_limiter.reset()
        self.client = appmod.app.test_client()

    def tearDown(self):
        appmod.CONFIG.clear()
        appmod.CONFIG.update(self._saved)
        appmod.rate_limiter.reset()


class TestPages(Base):
    def test_index_renders_institutional_text_and_fields(self):
        r = self.client.get("/")
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Canal confidencial de relatos e denúncias", html)
        self.assertIn('name="nome"', html)
        self.assertIn('name="categoria"', html)
        self.assertIn('name="mensagem"', html)
        for category in appmod.CATEGORIES:
            self.assertIn(category, html)
        self.assertIn("Deixe em branco para enviar de forma anônima", html)
        self.assertIn("Se você preencher este campo, o relato será identificado", html)
        self.assertIn("static/logo.png", html)

    def test_no_cookie_and_privacy_headers(self):
        r = self.client.get("/")
        self.assertNotIn("Set-Cookie", r.headers)
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(r.headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(r.headers["Cross-Origin-Resource-Policy"], "same-origin")

    def test_hsts_only_on_https(self):
        self.assertNotIn("Strict-Transport-Security", self.client.get("/").headers)
        r = self.client.get("/", base_url="https://localhost")
        self.assertEqual(r.headers["Strict-Transport-Security"], "max-age=31536000")

    def test_no_external_assets_in_page(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("https://", html.replace("http://www.w3.org", ""))
        self.assertNotIn("cdn.", html)

    def test_static_assets_served(self):
        for path in (
            "/static/style.css",
            "/static/app.js",
            "/static/logo.png",
            "/static/favicon.svg",
        ):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            r.close()  # file-backed response: close it to avoid ResourceWarning

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"status": "ok"})

    def test_https_requirement_fails_closed_but_keeps_health_local(self):
        appmod.CONFIG["REQUIRE_HTTPS"] = True
        r = self.client.get("/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("HTTPS", r.get_data(as_text=True))
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(
            self.client.get("/", base_url="https://localhost").status_code, 200
        )


class TestBodyFormat(unittest.TestCase):
    WHEN = datetime(2026, 9, 11, 14, 5, 9, tzinfo=timezone.utc)

    def test_anonymous_layout_exact(self):
        body = appmod.build_body("", "Outros", "Texto do relato.", self.WHEN)
        expected = (
            "==================================================\n"
            "RELATO DE COMPLIANCE / OEA (ANÔNIMO)\n"
            "==================================================\n"
            "Identificação do Relator: NÃO INFORMADO (RELATO 100% ANÔNIMO)\n"
            "Categoria: Outros\n"
            "Data e Hora do Registro: 11/09/2026 às 14:05:09\n"
            "==================================================\n"
            "\n"
            "DESCRIÇÃO DO RELATO:\n"
            "Texto do relato.\n"
        )
        self.assertEqual(body, expected)

    def test_identified_layout_exact(self):
        body = appmod.build_body(
            "Maria Souza", "Conduta Ética / Assédio", "Texto.", self.WHEN
        )
        expected = (
            "==================================================\n"
            "RELATO DE COMPLIANCE / OEA\n"
            "==================================================\n"
            "Identificação do Relator: Maria Souza\n"
            "Categoria: Conduta Ética / Assédio\n"
            "Data e Hora do Registro: 11/09/2026 às 14:05:09\n"
            "==================================================\n"
            "\n"
            "DESCRIÇÃO DO RELATO:\n"
            "Texto.\n"
        )
        self.assertEqual(body, expected)

    def test_subject_includes_category(self):
        self.assertEqual(
            appmod.build_subject("Fraude / Desvio"),
            "[CANAL DE ÉTICA & COMPLIANCE - OEA] Novo Relato Registrado - Fraude / Desvio",
        )

    def test_name_sanitised(self):
        self.assertEqual(appmod.clean_name("  João\r\nda\tSilva\x00 "), "João da Silva")
        self.assertEqual(
            len(appmod.clean_name("x" * 500)), appmod.CONFIG["MAX_NAME_CHARS"]
        )

    def test_message_keeps_newlines_drops_controls(self):
        self.assertEqual(
            appmod.clean_message("linha1\r\nlinha2\x07\rlinha3  "),
            "linha1\nlinha2\nlinha3",
        )


class TestEnvio(Base):
    @mock.patch("app.send_email")
    def test_anonymous_submission(self, send):
        r = self.client.post("/enviar", json=VALID)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("garantia de anonimato", r.get_json()["message"])
        subject, body = send.call_args.args
        self.assertIn("Fraude / Desvio", subject)
        self.assertIn("(ANÔNIMO)", body)
        self.assertIn("NÃO INFORMADO (RELATO 100% ANÔNIMO)", body)
        self.assertIn("DESCRIÇÃO DO RELATO:\n" + VALID["mensagem"], body)

    @mock.patch("app.send_email")
    def test_accented_category_and_text_survive_roundtrip(self, send):
        payload = {
            "nome": "José Antônio",
            "categoria": "Segurança da Cadeia Logística (OEA)",
            "mensagem": "Lacre do contêiner violado no pátio B — não há registro na guarita.",
        }
        r = self.client.post("/enviar", json=payload)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        subject, body = send.call_args.args
        self.assertIn("Segurança da Cadeia Logística (OEA)", subject)
        self.assertIn("Identificação do Relator: José Antônio", body)
        self.assertIn(payload["mensagem"], body)

    @mock.patch("app.send_email")
    def test_identified_submission(self, send):
        r = self.client.post("/enviar", json={**VALID, "nome": "Carlos Lima"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("garantia de anonimato", r.get_json()["message"])
        self.assertIn("confidencialidade", r.get_json()["message"])
        _, body = send.call_args.args
        self.assertIn("Identificação do Relator: Carlos Lima", body)
        self.assertNotIn("ANÔNIMO", body)

    @mock.patch("app.send_email")
    def test_form_post_without_javascript_renders_html(self, send):
        r = self.client.post("/enviar", data=VALID)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.content_type)
        self.assertIn("garantia de anonimato", r.get_data(as_text=True))
        send.assert_called_once()

    @mock.patch("app.send_email")
    def test_invalid_category_rejected(self, send):
        r = self.client.post("/enviar", json={**VALID, "categoria": "Inexistente"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])
        send.assert_not_called()

    @mock.patch("app.send_email")
    def test_short_message_rejected(self, send):
        r = self.client.post("/enviar", json={**VALID, "mensagem": "curto"})
        self.assertEqual(r.status_code, 400)
        send.assert_not_called()

    @mock.patch("app.send_email")
    def test_too_long_message_rejected(self, send):
        r = self.client.post(
            "/enviar",
            json={**VALID, "mensagem": "x" * (appmod.CONFIG["MAX_MESSAGE_CHARS"] + 1)},
        )
        self.assertEqual(r.status_code, 400)
        send.assert_not_called()

    @mock.patch("app.send_email")
    def test_honeypot_silently_discards(self, send):
        r = self.client.post(
            "/enviar", json={**VALID, appmod.HONEYPOT_FIELD: "http://spam"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])  # bot sees "success"
        send.assert_not_called()

    @mock.patch("app.send_email")
    def test_cross_origin_post_rejected(self, send):
        r = self.client.post(
            "/enviar", json=VALID, headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(r.status_code, 403)
        send.assert_not_called()

    @mock.patch("app.send_email")
    def test_configured_public_origin_is_used_behind_proxy(self, send):
        appmod.CONFIG["PUBLIC_ORIGIN"] = "https://canal.interno"
        accepted = self.client.post(
            "/enviar",
            json=VALID,
            base_url="https://backend.local",
            headers={"Origin": "https://canal.interno"},
        )
        rejected = self.client.post(
            "/enviar",
            json=VALID,
            base_url="https://backend.local",
            headers={"Origin": "https://backend.local"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(send.call_count, 1)

    @mock.patch("app.send_email")
    def test_non_object_json_rejected(self, send):
        r = self.client.post("/enviar", json=["não", "é", "objeto"])
        self.assertEqual(r.status_code, 400)
        send.assert_not_called()

    @mock.patch("app.send_email")
    def test_non_text_field_rejected(self, send):
        r = self.client.post(
            "/enviar", json={**VALID, "mensagem": {"texto": "inválido"}}
        )
        self.assertEqual(r.status_code, 400)
        send.assert_not_called()

    @mock.patch("app.send_email")
    def test_same_origin_post_accepted(self, send):
        r = self.client.post(
            "/enviar", json=VALID, headers={"Origin": "http://localhost"}
        )
        self.assertEqual(r.status_code, 200)
        send.assert_called_once()

    @mock.patch("app.send_email")
    def test_global_rate_limit(self, send):
        appmod.CONFIG["RATE_LIMIT_MAX"] = 2
        self.assertEqual(self.client.post("/enviar", json=VALID).status_code, 200)
        self.assertEqual(self.client.post("/enviar", json=VALID).status_code, 200)
        r = self.client.post("/enviar", json=VALID)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(send.call_count, 2)

    @mock.patch("app.send_email", side_effect=OSError("connection refused"))
    def test_smtp_failure_reported_to_user(self, send):
        r = self.client.post("/enviar", json=VALID)
        self.assertEqual(r.status_code, 502)
        self.assertFalse(r.get_json()["ok"])
        self.assertIn("e-mail", r.get_json()["error"])

    @mock.patch("app.send_email")
    def test_response_has_no_cookie(self, send):
        r = self.client.post("/enviar", json=VALID)
        self.assertNotIn("Set-Cookie", r.headers)


class TestSmtpDelivery(Base):
    @mock.patch("app.smtplib.SMTP")
    def test_starttls_login_send_sequence(self, SMTP):
        appmod.CONFIG.update(
            SMTP_SECURITY="starttls",
            SMTP_USER="user@x",
            SMTP_PASSWORD="secret",
            MAIL_FROM="from@x",
            MAIL_TO="to@x",
        )
        appmod.send_email("Assunto", "Corpo")
        conn = SMTP.return_value
        conn.starttls.assert_called_once()
        conn.login.assert_called_once_with("user@x", "secret")
        conn.send_message.assert_called_once()
        msg = conn.send_message.call_args.args[0]
        self.assertEqual(msg["From"], "from@x")
        self.assertEqual(msg["To"], "to@x")
        self.assertEqual(msg["Subject"], "Assunto")
        self.assertIsNotNone(msg["Date"])
        self.assertIsNotNone(msg["Message-ID"])
        self.assertEqual(msg.get_content().strip(), "Corpo")

    @mock.patch("app.smtplib.SMTP_SSL")
    def test_ssl_mode_uses_smtp_ssl(self, SMTP_SSL):
        appmod.CONFIG.update(SMTP_SECURITY="ssl", SMTP_PORT=465)
        appmod.send_email("s", "b")
        SMTP_SSL.assert_called_once()
        SMTP_SSL.return_value.starttls.assert_not_called()

    @mock.patch("app.smtplib.SMTP")
    def test_relay_without_credentials_skips_login(self, SMTP):
        appmod.CONFIG.update(SMTP_SECURITY="none", SMTP_USER="", SMTP_PASSWORD="")
        appmod.send_email("s", "b")
        SMTP.return_value.login.assert_not_called()
        SMTP.return_value.starttls.assert_not_called()
        SMTP.return_value.send_message.assert_called_once()

    @mock.patch("app.smtplib.SMTP")
    def test_dry_run_never_connects(self, SMTP):
        appmod.CONFIG["DRY_RUN"] = True
        with self.assertLogs("canal_compliance", level="INFO") as captured:
            appmod.send_email("assunto-secreto", "corpo-secreto")
        SMTP.assert_not_called()
        logs = "\n".join(captured.output)
        self.assertNotIn("assunto-secreto", logs)
        self.assertNotIn("corpo-secreto", logs)

    def test_authentication_without_tls_is_rejected(self):
        appmod.CONFIG.update(
            SMTP_SECURITY="none", SMTP_USER="user@x", SMTP_PASSWORD="secret"
        )
        with self.assertRaises(ValueError):
            appmod.send_email("s", "b")


class TestConfiguration(Base):
    def test_default_test_configuration_is_valid(self):
        appmod.CONFIG.update(
            SMTP_USER="", SMTP_PASSWORD="", SMTP_SECURITY="none", MAIL_FROM="from@x"
        )
        self.assertEqual(appmod.configuration_errors(), [])

    def test_https_requires_https_origin_and_scheme(self):
        appmod.CONFIG.update(
            REQUIRE_HTTPS=True, URL_SCHEME="http", PUBLIC_ORIGIN="http://srvapp01"
        )
        errors = " ".join(appmod.configuration_errors())
        self.assertIn("URL_SCHEME=https", errors)
        self.assertIn("PUBLIC_ORIGIN", errors)

    def test_invalid_timezone_is_rejected(self):
        appmod.CONFIG["TZ_NAME"] = "Fuso/Inexistente"
        self.assertTrue(
            any("TZ_NAME" in error for error in appmod.configuration_errors())
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
