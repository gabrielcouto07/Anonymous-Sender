/*
 * Canal de Ética & Compliance — front-end behaviour.
 *
 * Progressive enhancement: the form works with plain HTML POST when this
 * script does not run; when it does, we submit via fetch() as JSON so the
 * page never reloads, show a loading state and clear the fields on success.
 *
 * Privacy notes: fetch() is called with credentials "omit" (no cookies are
 * ever sent) and referrerPolicy "no-referrer". Nothing is stored in
 * localStorage/sessionStorage; a page refresh leaves no trace of the draft.
 */
(function () {
  "use strict";

  var form = document.getElementById("form-relato");
  if (!form) { return; }

  var textarea = document.getElementById("mensagem");
  var select = document.getElementById("categoria");
  var nameInput = document.getElementById("nome");
  var counter = document.getElementById("contador");
  var status = document.getElementById("status");
  var button = document.getElementById("btn-enviar");

  var MAX = parseInt(counter.getAttribute("data-max"), 10) || 10000;
  var MIN = parseInt(textarea.getAttribute("minlength"), 10) || 0;
  var HONEYPOT = form.getAttribute("data-honeypot");
  var LABEL_IDLE = "Enviar relato";
  var LABEL_BUSY = "Enviando relato seguro...";

  function formatNumber(n) {
    try { return n.toLocaleString("pt-BR"); } catch (e) { return String(n); }
  }

  /* Countdown of remaining characters, highlighted in the last 10 %. */
  function updateCounter() {
    var remaining = Math.max(0, MAX - textarea.value.length);
    counter.textContent = formatNumber(remaining) + (remaining === 1 ? " caractere restante" : " caracteres restantes");
    counter.classList.toggle("counter--warn", remaining <= MAX * 0.1);
  }

  function showStatus(kind, text) {
    status.hidden = false;
    status.className = "status status--" + kind;
    status.setAttribute("role", kind === "error" ? "alert" : "status");
    status.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
    status.textContent = text;
    status.focus();
  }

  function hideStatus() {
    status.hidden = true;
    status.textContent = "";
  }

  function setLoading(on) {
    button.disabled = on;
    button.classList.toggle("btn--loading", on);
    button.textContent = on ? LABEL_BUSY : LABEL_IDLE;
    nameInput.disabled = on;
    select.disabled = on;
    textarea.disabled = on;
  }

  textarea.addEventListener("input", updateCounter);
  updateCounter();

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (button.disabled) { return; } /* guard against double submission */
    hideStatus();

    var categoria = select.value;
    var mensagem = textarea.value.trim();

    if (!categoria) {
      showStatus("error", "Selecione a categoria do relato.");
      select.focus();
      return;
    }
    if (mensagem.length < MIN) {
      showStatus("error", "Descreva o relato com pelo menos " + MIN + " caracteres.");
      textarea.focus();
      return;
    }

    var payload = { nome: nameInput.value, categoria: categoria, mensagem: mensagem };
    var hp = HONEYPOT ? form.elements[HONEYPOT] : null;
    if (hp) { payload[HONEYPOT] = hp.value; }

    setLoading(true);

    fetch(form.getAttribute("action"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload),
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer"
    })
      .then(function (response) {
        return response.json()
          .catch(function () { return null; })
          .then(function (data) { return { ok: response.ok, data: data }; });
      })
      .then(function (result) {
        if (result.ok && result.data && result.data.ok) {
          form.reset();
          updateCounter();
          showStatus("success", result.data.message);
        } else {
          var msg = (result.data && result.data.error) ||
            "Não foi possível enviar o relato. Tente novamente em alguns minutos.";
          showStatus("error", msg);
        }
      })
      .catch(function () {
        showStatus("error", "Falha de comunicação com o servidor. Verifique a conexão com a rede e tente novamente.");
      })
      .then(function () { setLoading(false); });
  });
})();
