# Canal de Ética & Compliance (OEA)

Aplicação web interna, minimalista e ultraleve, para **relatos éticos e denúncias** do
Programa **OEA (Operador Econômico Autorizado)**. O colaborador acessa
`https://srvapp01`, preenche o formulário e o conteúdo é enviado **direto por e-mail**
para `compliance@scientificdental.com`. Não há banco de dados nem persistência do formulário.

| Item | Valor |
|---|---|
| Backend | Python 3.9+ · Flask · Waitress |
| Frontend | HTML5 + CSS + JS próprios (sem CDN, sem framework) |
| Banco de dados | Nenhum |
| Endpoint público | HTTPS 443 via IIS/ARR |
| Backend | `127.0.0.1:8980` (não exposto na rede) |
| Remetente | chamados@scientificdental.com |
| Destinatário | compliance@scientificdental.com |

---

## 1. O que a aplicação garante (e como)

| Garantia | Como é implementada |
|---|---|
| Nenhum access log com IP | Waitress não gera log de acesso, o logger do Flask é silenciado e o `web.config` desativa o log HTTP do site no IIS. |
| Nenhum cookie / sessão | O código nunca usa `session`; o Flask só emite cookie quando isso acontece. O fetch do navegador usa `credentials: "omit"`. |
| Nenhum User-Agent armazenado | Nenhum cabeçalho da requisição é lido para gravação. O único cabeçalho consultado é `Origin`, comparado em memória e descartado (proteção contra envio a partir de outro site). |
| Nenhum conteúdo de relato em disco | Sem banco ou arquivo temporário. Nem o modo `DRY_RUN` grava corpo, nome ou categoria. O e-mail entregue é o único registro do conteúdo. |
| Nenhum rastreio externo | CSS, JS e logo são servidos localmente; a política CSP (`default-src 'self'`) impede o navegador de buscar qualquer recurso fora do servidor. |
| Transporte protegido | HTTPS obrigatório no endpoint público; o backend escuta somente no loopback. SMTP usa STARTTLS ou TLS por padrão. |
| Anti-abuso sem identificar | Limite **global** de envios por janela de tempo (conta envios, não pessoas) e campo honeypot invisível contra robôs. |

> Texto exibido no topo do portal:
> *"Canal confidencial de relatos e denúncias — Programa de Conformidade OEA. Você pode enviar sem se identificar. O conteúdo do formulário não é salvo neste servidor: ele é encaminhado diretamente ao Comitê de Compliance."*

Se o campo de identificação for preenchido, o relato deixa de ser anônimo e a interface informa isso explicitamente. O tratamento pelo Comitê continua confidencial.

---

## 2. Mapa de arquivos

```
EnvioAnonimo/
├── app.py                      # Aplicação Flask: rotas, validação, montagem e envio do e-mail, CLI
├── serve.py                    # Ponto de entrada de PRODUÇÃO (Waitress + log rotativo em logs/)
├── requirements.txt            # flask, waitress, tzdata
├── .env.example                # Modelo de configuração -> copie para .env
├── templates/
│   ├── index.html              # Formulário (logo, aviso institucional, campos, contador)
│   └── resultado.html          # Página de retorno usada apenas se o navegador estiver sem JavaScript
├── static/
│   ├── style.css               # Visual corporativo (sem dependências externas)
│   ├── app.js                  # Contador regressivo, envio via fetch, estado "Enviando relato seguro..."
│   └── logo.png                # Logo otimizada para a página, derivada de Preto.png
├── assets/brand/Preto.png      # Arte original da marca, preservada sem alteração
├── deploy/
│   ├── windows/
│   │   ├── setup.ps1                   # Cria venv, instala dependências e cria .env
│   │   ├── iis/web.config               # Proxy HTTPS sem access log para o backend local
│   │   ├── start.bat                   # Executa em primeiro plano (testes / diagnóstico)
│   │   ├── install_service_nssm.ps1    # Instala como Serviço do Windows via NSSM (recomendado)
│   │   └── install_task_scheduler.ps1  # Alternativa sem NSSM: tarefa LocalService no boot
│   └── linux/
│       └── canal-compliance.service    # Unidade systemd para VM Linux
├── tests/test_app.py           # Testes automatizados (sem rede; SMTP simulado)
└── logs/                       # app.log / service.log (criados em execução; sem dados de relatos)
```

---

## 3. Instalação na VM Windows (passo a passo)

### 3.1 Pré-requisitos
- Windows Server 2016+ ou Windows 10/11.
- **Python 3.9 ou superior** instalado ([python.org](https://www.python.org/downloads/windows/)). Marque *"Add python.exe to PATH"* no instalador.
- Acesso de saída da VM ao servidor SMTP (porta 587 para Office 365).
- Registro DNS interno `srvapp01` apontando para a VM (ou use o IP).
- Certificado TLS para `srvapp01`, emitido por uma CA confiável nos computadores internos.
- Para o caminho recomendado: IIS, [URL Rewrite](https://www.iis.net/downloads/microsoft/url-rewrite)
  e [Application Request Routing (ARR)](https://www.iis.net/downloads/microsoft/application-request-routing).

### 3.2 Copiar a pasta
Copie a pasta `EnvioAnonimo` para a VM, por exemplo em `C:\Apps\EnvioAnonimo`.

### 3.3 Executar o setup
Abra o **PowerShell como Administrador** dentro da pasta e rode:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\setup.ps1
```

O script cria o `venv`, instala as versões auditadas em `requirements.txt` e cria o `.env`
a partir do `.env.example`. No desenho recomendado, a porta 8980 fica no loopback e não recebe
regra de firewall. A opção `-Firewall` existe apenas para diagnóstico HTTP temporário.

Instalação manual equivalente, se preferir:

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

### 3.4 Configurar o `.env`
Abra `.env` e preencha ao menos a senha:

```ini
SMTP_SERVER=smtp.office365.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USER=chamados@scientificdental.com
SMTP_PASSWORD=********
MAIL_TO=compliance@scientificdental.com
PORT=8980
HOST=127.0.0.1
URL_SCHEME=https
REQUIRE_HTTPS=true
PUBLIC_ORIGIN=https://srvapp01
```

Todas as variáveis estão comentadas no `.env.example`. Variáveis de ambiente do sistema
têm prioridade sobre o arquivo, o que permite guardar a senha fora do disco se a política
da empresa exigir.

### 3.5 Testar o SMTP antes de publicar

```powershell
venv\Scripts\python.exe app.py --check-config     # mostra a configuração (senha mascarada)
venv\Scripts\python.exe app.py --test-email       # envia um e-mail de teste para MAIL_TO
```

Se o e-mail de teste chegar em `compliance@`, o canal está pronto.

### 3.6 Rodar manualmente (primeiro teste)

```bat
deploy\windows\start.bat
```

Na própria VM, confirme `http://127.0.0.1:8980/health`. O formulário para usuários só deve
ser testado pelo endpoint final `https://srvapp01`, depois da configuração do proxy. `Ctrl+C` encerra.

---

## 4. Manter em segundo plano na VM Windows

### 4.1 Publicar somente por HTTPS no IIS

O Waitress não termina TLS. Em produção ele deve continuar restrito a `127.0.0.1:8980`,
atrás do IIS. Isso protege o texto do relato contra leitura ou alteração durante o trânsito
na rede interna.

1. No IIS, instale URL Rewrite e ARR. Em **Application Request Routing Cache → Server Proxy
   Settings**, marque **Enable proxy**.
2. Crie um site cujo diretório físico seja `C:\Apps\EnvioAnonimo\deploy\windows\iis` (ajuste
   o caminho se necessário).
3. Adicione somente um binding `https`, porta 443, host `srvapp01`, usando o certificado da CA
   corporativa. Não adicione binding HTTP para esse site.
4. O [`web.config`](deploy/windows/iis/web.config) encaminha tudo ao backend local e combina
   `selectiveLogging=LogSuccessful` com `dontLog=true`, desativando o access log do site para
   respostas de sucesso e de erro. Mantenha também **Failed Request
   Tracing** desabilitado para o site.
5. No Firewall, permita entrada TCP 443 apenas nos perfis e redes corporativos. Não libere 8980.
6. Confirme `https://srvapp01/health` e depois envie um relato de teste.

Se o DNS público, host ou porta HTTPS forem diferentes, atualize `PUBLIC_ORIGIN` no `.env`.
Não use certificado autoassinado sem distribuir sua CA como confiável aos navegadores.

### 4.2 Opção A — Serviço do Windows com NSSM (recomendada)

**Por quê:** um serviço sobe no boot antes de qualquer login, reinicia sozinho se o processo
cair e aparece em `services.msc` como qualquer outro serviço corporativo. Scripts Python não
conseguem se registrar como serviço nativamente, e o NSSM faz exatamente essa ponte.

1. Baixe o NSSM em <https://nssm.cc/download> e copie `win64\nssm.exe` para
   `deploy\windows\tools\nssm.exe`.
2. No PowerShell **como Administrador**:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1
```

O serviço `CanalCompliance` é criado com a conta limitada `LocalService`, início automático,
reinício em falha (5 s) e saída em `logs\service.log` com rotação de 1 MB. O script concede
leitura da aplicação e escrita somente em `logs/`; o `.env` fica legível apenas por
Administradores, SYSTEM e pelo serviço.

Gerenciar:

```powershell
Get-Service CanalCompliance
Restart-Service CanalCompliance
Stop-Service CanalCompliance
powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1 -Uninstall
```

### 4.3 Opção B — Agendador de Tarefas (sem binário externo)

Quando a política da empresa não permite executáveis de terceiros:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install_task_scheduler.ps1
```

Cria a tarefa `CanalCompliance` rodando como `LocalService`, disparada no boot, sem limite de
tempo, com até 10 reinícios automáticos em caso de erro, e a inicia imediatamente.

```powershell
Get-ScheduledTask CanalCompliance
Stop-ScheduledTask -TaskName CanalCompliance
Start-ScheduledTask -TaskName CanalCompliance
powershell -ExecutionPolicy Bypass -File deploy\windows\install_task_scheduler.ps1 -Uninstall
```

Limitação: o Agendador não captura a saída do processo; use `logs\app.log`.

### 4.4 Trocar a porta
Altere `PORT` no `.env`, atualize o destino no `deploy/windows/iis/web.config` e reinicie o
serviço. Mantenha a nova porta presa a `127.0.0.1`, sem regra de entrada no firewall.

---

## 5. VM Linux (alternativa)

A unidade `deploy/linux/canal-compliance.service` contém no cabeçalho o passo a passo
completo. Resumo:

```bash
sudo mkdir -p /opt/canal-compliance && sudo cp -r . /opt/canal-compliance
cd /opt/canal-compliance
sudo python3 -m venv venv && sudo venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env
sudo useradd --system --no-create-home --shell /usr/sbin/nologin compliance
sudo chown -R compliance:compliance /opt/canal-compliance && sudo chmod 600 .env
sudo cp deploy/linux/canal-compliance.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now canal-compliance
```

Também no Linux, mantenha `HOST=127.0.0.1` e termine HTTPS em nginx, Apache ou Caddy com
access log desabilitado. Publique apenas a porta 443; não abra 8980 no firewall.

---

## 6. Logotipo e textos

- **Logo:** a arte fornecida em `Preto.png` foi preservada integralmente em
  `assets/brand/Preto.png`. A versão `static/logo.png` apenas remove a grande margem
  transparente e acrescenta respiro uniforme para renderizar com nitidez no cabeçalho.
- **Nome da empresa:** `COMPANY_NAME` no `.env`.
- **Categorias:** tupla `CATEGORIES` em `app.py` (a validação do servidor usa a mesma lista,
  portanto altere só ali).
- **Limite de caracteres:** `MAX_MESSAGE_CHARS` / `MIN_MESSAGE_CHARS` no `.env`.
- **Textos da página e do e-mail:** `templates/index.html` e `build_body()` em `app.py`.

---

## 7. Formato do e-mail recebido pelo Compliance

**Assunto:** `[CANAL DE ÉTICA & COMPLIANCE - OEA] Novo Relato Registrado - <Categoria>`

Relato identificado:

```text
==================================================
RELATO DE COMPLIANCE / OEA
==================================================
Identificação do Relator: Nome digitado
Categoria: Fraude / Desvio
Data e Hora do Registro: 11/09/2026 às 14:05:09
==================================================

DESCRIÇÃO DO RELATO:
Texto completo...
```

Relato anônimo:

```text
==================================================
RELATO DE COMPLIANCE / OEA (ANÔNIMO)
==================================================
Identificação do Relator: NÃO INFORMADO (RELATO 100% ANÔNIMO)
Categoria: Fraude / Desvio
Data e Hora do Registro: 11/09/2026 às 14:05:09
==================================================

DESCRIÇÃO DO RELATO:
Texto completo...
```

A data/hora usa o fuso `TZ_NAME` (padrão `America/Sao_Paulo`), independentemente do fuso da VM.

---

## 8. Testes automatizados

```powershell
venv\Scripts\python.exe -m unittest discover -s tests -v
```

Cobrem: layout exato dos dois corpos de e-mail, assunto por categoria, validações
(categoria inválida, mensagem curta/longa), honeypot, bloqueio de origem cruzada, limite
global, falha de SMTP reportada ao usuário, ausência de cookies, cabeçalhos de segurança e
a sequência STARTTLS → login → envio. Nenhum teste acessa a rede.

Para testar o fluxo sem enviar e-mail, defina `DRY_RUN=true` no `.env`. O e-mail é descartado;
nome, categoria e mensagem **não** são escritos no log.

---

## 9. Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `--test-email` falha com `SMTPAuthenticationError` (535) | Senha errada; ou **"SMTP autenticado"** desabilitado para a caixa no Exchange Online; ou MFA sem senha de aplicativo | No admin do Microsoft 365: usuário → Email → *Gerenciar aplicativos de email* → marcar **SMTP autenticado**. Se houver MFA, use senha de aplicativo ou uma conta sem MFA. |
| Autenticação recusada mesmo com senha correta e SMTP autenticado ligado | A Microsoft está **aposentando a autenticação básica (usuário/senha) no SMTP AUTH** do Exchange Online: ela fica **desabilitada por padrão no fim de dezembro de 2026** (o administrador ainda poderá reativá-la) e a data da remoção definitiva será anunciada no 2º semestre de 2027. Fonte: [comunicado atualizado do Exchange Team](https://techcommunity.microsoft.com/blog/exchange/updated-exchange-online-smtp-auth-basic-authentication-deprecation-timeline/4489835). | Curto prazo: pedir ao admin do M365 para manter SMTP AUTH básico habilitado no tenant e na caixa `chamados@`. Alternativas duradouras: (1) relay interno sem credenciais; (2) High Volume Email; (3) OAuth2/Microsoft Graph. |
| `--test-email` falha com `TimeoutError` / `ConnectionRefusedError` | Firewall de saída bloqueando a porta 587, ou proxy corporativo | Liberar saída TCP 587 da VM para `smtp.office365.com`; testar com `Test-NetConnection smtp.office365.com -Port 587`. |
| `https://srvapp01` não abre, mas `/health` funciona localmente | Binding/certificado do IIS, ARR, DNS ou firewall 443 | Conferir a seção 4.1, o certificado e `Resolve-DnsName srvapp01`. Não exponha 8980. |
| Resposta “somente por uma conexão HTTPS segura” | `URL_SCHEME` ou proxy configurado incorretamente | Em produção, mantenha `URL_SCHEME=https`, `REQUIRE_HTTPS=true` e acesse pelo binding HTTPS. |
| Serviço não inicia | `.env` ausente/inválido ou venv não criado | Ver `logs\service.log` (NSSM) ou `logs\app.log`; rodar `start.bat` para ver o erro na tela. |
| E-mail chega com fuso errado | `tzdata` não instalado ou `TZ_NAME` inválido | `venv\Scripts\python.exe -m pip install tzdata`; conferir `TZ_NAME`. |
| Muitos envios seguidos retornam "aguarde alguns minutos" | Limite global anti-flood | Ajustar `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW` no `.env`. |

---

## 10. Segurança operacional (recomendações)

- Restrinja o acesso ao `.env` (contém a senha SMTP) aos administradores e à identidade do serviço.
- Mantenha o endpoint HTTPS acessível **apenas na rede interna** e a porta 8980 apenas no loopback.
- Não habilite logs de acesso, Failed Request Tracing ou inspeção que persista corpo de requisição no proxy.
- Revise e atualize as versões fixadas em `requirements.txt` em ciclo mensal, sempre executando os testes e uma auditoria de dependências.
- Use uma caixa `compliance@` com acesso restrito ao Comitê, pois o e-mail é o único registro do relato.
