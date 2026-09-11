# Deploy do Canal de Ética & Compliance na VM `srvapp01`

Objetivo: deixar o canal disponível para os colaboradores em
**`http://srvapp01:8980`**, rodando como serviço do Windows, sem atrapalhar os
sistemas que já existem na VM.

Tempo estimado: 20 a 30 minutos.

---

## 0. Situação atual da VM (levantada em 11/09/2026)

Portas que já respondem em `srvapp01` (192.168.0.187):

| Porta | Quem está usando | Como foi identificado |
|------:|------------------|-----------------------|
| 80 | IIS (página padrão do Windows) | `Server: Microsoft-IIS/10.0` |
| 5432 | PostgreSQL | banco `helpdesk` |
| 8000 | Gerenciado Contábil | `server: uvicorn`, redireciona para `/login` |
| 8081 | HelpDesk (frontend Vite + API) | responde na rede como `192.168.0.187:8081` |
| **8980** | **livre** | nada respondeu na sondagem |

**Por isso a porta escolhida é a 8980.** Ela está livre, não colide com nenhum
dos dois sistemas em produção e é a porta padrão que a aplicação já usa, o que
evita ter que ajustar scripts.

> Antes de começar, confirme na própria VM que ela continua livre:
> ```powershell
> Get-NetTCPConnection -LocalPort 8980 -State Listen
> ```
> Se não retornar nada, está livre. Se retornar algo, escolha outra porta
> (8981, 8990, 9080...) e use ela em todos os passos daqui para frente.

---

## 1. Pré-requisitos na VM

1. **Python 3.9 ou superior instalado.** Confira no PowerShell da VM:
   ```powershell
   python --version
   ```
   Se não existir, baixe em <https://www.python.org/downloads/windows/> e
   marque **"Add python.exe to PATH"** durante a instalação.

2. **Acesso administrador na VM** (você já está usando "Administrator:
   Windows PowerShell", então isso está resolvido).

3. **Saída para o servidor SMTP.** Teste na VM:
   ```powershell
   Test-NetConnection smtp.office365.com -Port 587
   ```
   Precisa mostrar `TcpTestSucceeded : True`.

---

## 2. Passo 1 — Copiar a pasta do seu PC para a VM

No **PowerShell do seu PC** (não da VM):

```powershell
robocopy "C:\Users\GABRIEL.CARDOSO\Documents\ERP\EnvioAnonimo" "\\srvapp01\c$\Apps\CanalCompliance" /E /XD venv logs .git __pycache__ /XF .env
```

**Por que excluir essas pastas:**

- `venv` — o ambiente virtual guarda caminhos absolutos do PC de origem.
  Copiado para outra máquina ele quebra. Vamos recriar na VM no passo 2.
- `logs` — são registros operacionais locais, não fazem parte da aplicação.
- `.git` e `__pycache__` — só ocupam espaço na VM.
- `.env` — é o arquivo de segredos. Cada ambiente tem o seu; vamos criar o da
  VM no passo 4.

**Se o `\\srvapp01\c$` não funcionar** (bloqueio de compartilhamento
administrativo), use a área de transferência do RDP: selecione a pasta no seu
PC, `Ctrl+C`, e cole dentro da VM em `C:\Apps\CanalCompliance`. Depois apague
lá dentro as pastas `venv` e `logs` se elas tiverem vindo junto.

---

## 3. Passo 2 — Criar o `.env` no perfil correto (passo mais importante)

A aplicação tem dois perfis de configuração, e escolher o errado aqui é o erro
mais provável de todo o deploy.

| Arquivo modelo | Para quando | O que faz |
|---|---|---|
| `.env.example` | publicação com HTTPS atrás do IIS | escuta só em `127.0.0.1` e **recusa** qualquer acesso que não seja HTTPS |
| **`.env.intranet.example`** | **o nosso caso: `http://srvapp01:8980`** | escuta na rede e aceita HTTP |

Se você copiar o modelo errado, a aplicação responde **HTTP 400 em todas as
páginas** ("Este canal aceita relatos somente por uma conexão HTTPS segura"),
ou nem sobe, com a mensagem `Com REQUIRE_HTTPS=true, HOST deve apontar para o
loopback atrás do proxy TLS`. Isso é proposital: a aplicação falha fechada em
vez de servir conteúdo sensível em texto claro por engano.

Na VM:

```powershell
cd C:\Apps\CanalCompliance
Copy-Item .env.intranet.example .env
notepad .env
```

No Bloco de Notas, preencha **apenas a senha** e salve:

```ini
SMTP_PASSWORD=<senha da caixa chamados@scientificdental.com>
```

Confira que estas cinco linhas estão exatamente assim (já vêm prontas no
modelo):

```ini
HOST=0.0.0.0          # aceita conexões da rede interna
PORT=8980             # a porta do navegador
URL_SCHEME=http
REQUIRE_HTTPS=false   # permite HTTP na rede interna
PUBLIC_ORIGIN=        # vazio de propósito, ver abaixo
DRY_RUN=false         # false = envia e-mail de verdade
```

**Por que `PUBLIC_ORIGIN` fica vazio:** a aplicação valida a origem do envio
para impedir que outro site poste relatos no seu canal. Com o campo vazio, ela
compara com o próprio endereço que o navegador usou, então funciona tanto em
`http://srvapp01:8980` quanto em `http://192.168.0.187:8980`. Se você
preencher com um dos dois, o outro passa a ser recusado com erro 403.

> **Ordem importa.** Crie o `.env` agora, antes do próximo passo. O
> `setup.ps1` gera um `.env` automaticamente a partir do `.env.example` (o
> perfil HTTPS) quando não encontra nenhum. Criando o seu primeiro, ele
> apenas informa `.env ja existe - mantido` e não mexe na sua configuração.

---

## 4. Passo 3 — Preparar o ambiente Python na VM

No **PowerShell da VM, como administrador**:

```powershell
cd C:\Apps\CanalCompliance
powershell -ExecutionPolicy Bypass -File deploy\windows\setup.ps1
```

O script cria o `venv` e instala as três dependências (Flask, Waitress,
tzdata). Ele deve imprimir `.env ja existe - mantido`. Se em vez disso
imprimir `ATENCAO: .env criado a partir de .env.example`, o passo 2 não foi
feito: volte e refaça, senão a aplicação sobe no perfil HTTPS e recusa todos
os acessos.

O parâmetro `-Firewall` fica para o passo 6, junto com a publicação na rede.

---

## 5. Passo 4 — Testar o SMTP antes de publicar

```powershell
cd C:\Apps\CanalCompliance
venv\Scripts\python.exe app.py --check-config
venv\Scripts\python.exe app.py --test-email
```

O primeiro comando mostra a configuração efetiva com a senha mascarada. Use-o
para conferir se `HOST`, `PORT` e `REQUIRE_HTTPS` ficaram como esperado.

O segundo envia um e-mail de teste para `compliance@scientificdental.com`.
**Só siga adiante depois que esse e-mail chegar.** Se falhar, veja a seção 12.

---

## 6. Passo 5 — Primeiro teste, com a janela aberta

```powershell
cd C:\Apps\CanalCompliance
deploy\windows\start.bat
```

Deixe a janela aberta e faça três verificações, nesta ordem:

1. **No navegador da própria VM:** `http://localhost:8980`
   A página do canal deve abrir.
2. **No navegador da VM, pelo nome:** `http://srvapp01:8980`
   Prova que o `HOST=0.0.0.0` está valendo.
3. **No navegador do seu PC:** `http://srvapp01:8980`
   Se aqui falhar e os dois anteriores funcionarem, o problema é o firewall,
   resolvido no passo 6.

Envie um relato de teste pela página e confirme que o e-mail chegou em
`compliance@`. Depois encerre com `Ctrl+C`.

---

## 7. Passo 6 — Liberar a porta no firewall do Windows

```powershell
cd C:\Apps\CanalCompliance
powershell -ExecutionPolicy Bypass -File deploy\windows\setup.ps1 -Firewall
```

O script lê a porta do `.env` e cria a regra de entrada nos perfis Domínio e
Privado. **Ele não libera no perfil Público de propósito**, para o canal não
ficar exposto caso a VM seja ligada em uma rede não confiável.

Conferir:
```powershell
Get-NetFirewallRule -DisplayName "Canal Compliance (TCP 8980)"
```

---

## 8. Passo 7 — Instalar como serviço do Windows

Sem isso, o canal morre quando você fizer logoff da VM. É exatamente o que
acontece hoje com o HelpDesk e o Gerenciado Contábil, que estão rodando em
janelas interativas do PowerShell.

### Opção A — NSSM (recomendada)

O NSSM é o utilitário padrão para transformar um programa qualquer em serviço
do Windows, com reinício automático e captura de log. Scripts Python não
conseguem se registrar como serviço sozinhos.

1. Baixe <https://nssm.cc/download>, abra o zip e copie **`win64\nssm.exe`**
   para `C:\Apps\CanalCompliance\deploy\windows\tools\nssm.exe`.

2. No PowerShell da VM como administrador:
   ```powershell
   cd C:\Apps\CanalCompliance
   powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1
   ```

O script cria o serviço `CanalCompliance` com início automático no boot,
reinício em 5 segundos se o processo cair, log rotativo em
`logs\service.log`, e o executa com a conta **LocalService**, que tem
privilégios mínimos. Ele também ajusta as permissões para que apenas
administradores e o serviço leiam o `.env` com a senha.

### Opção B — Agendador de Tarefas (sem baixar nada)

Se a política da empresa não permitir executáveis de terceiros:

```powershell
cd C:\Apps\CanalCompliance
powershell -ExecutionPolicy Bypass -File deploy\windows\install_task_scheduler.ps1
```

Cria a tarefa `CanalCompliance` rodando como LocalService, disparada no boot,
sem limite de tempo. A diferença é que o Agendador não captura a saída do
processo, então você depende de `logs\app.log`.

---

## 9. Passo 8 — Verificação final

Rode esta sequência na VM e confira cada linha:

```powershell
# 1. o serviço está rodando?
Get-Service CanalCompliance

# 2. a porta está escutando na rede (0.0.0.0, não só 127.0.0.1)?
Get-NetTCPConnection -LocalPort 8980 -State Listen | Select-Object LocalAddress, LocalPort

# 3. a aplicação responde?
Invoke-RestMethod http://localhost:8980/health

# 4. os outros sistemas continuam de pé?
Get-NetTCPConnection -LocalPort 8000,8081 -State Listen | Select-Object LocalPort
```

Esperado: serviço `Running`, `LocalAddress` igual a `0.0.0.0`, `/health`
retornando `status: ok`, e as portas 8000 e 8081 ainda listadas.

Por fim, do **seu PC**, abra `http://srvapp01:8980` e envie um relato real de
teste. Confirme a chegada em `compliance@` e o canal está publicado.

---

## 10. Operação do dia a dia

| O que fazer | Comando (na VM) |
|---|---|
| Ver status | `Get-Service CanalCompliance` |
| Reiniciar | `Restart-Service CanalCompliance` |
| Parar | `Stop-Service CanalCompliance` |
| Ver log da aplicação | `Get-Content C:\Apps\CanalCompliance\logs\app.log -Tail 30` |
| Ver log do serviço | `Get-Content C:\Apps\CanalCompliance\logs\service.log -Tail 30` |
| Trocar a porta | edite `PORT` no `.env`, rode `setup.ps1 -Firewall`, depois `Restart-Service CanalCompliance` |

**Atualizar a aplicação** depois de mudar o código no seu PC:

```powershell
# no seu PC
robocopy "C:\Users\GABRIEL.CARDOSO\Documents\ERP\EnvioAnonimo" "\\srvapp01\c$\Apps\CanalCompliance" /E /XD venv logs .git __pycache__ /XF .env
# na VM
Restart-Service CanalCompliance
```

O `/XF .env` garante que a cópia nunca sobrescreva a configuração da VM.

---

## 11. Desfazer tudo (rollback)

```powershell
cd C:\Apps\CanalCompliance
powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1 -Uninstall
Remove-NetFirewallRule -DisplayName "Canal Compliance (TCP 8980)"
```

Depois, se quiser, apague a pasta `C:\Apps\CanalCompliance`. Nenhum outro
sistema da VM é afetado: a aplicação não usa banco de dados, não escreve fora
da própria pasta e não altera o IIS.

---

## 12. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| Todas as páginas retornam 400 "somente por uma conexão HTTPS segura" | `.env` veio do modelo errado (`.env.example`) | `Copy-Item .env.intranet.example .env`, preencha a senha e reinicie o serviço |
| Serviço não inicia, log diz "Com REQUIRE_HTTPS=true, HOST deve apontar para o loopback" | mesma causa acima | idem |
| Abre em `localhost:8980` na VM, mas não do seu PC | firewall | passo 6 |
| Não abre nem na VM | serviço parado ou porta ocupada | `Get-Service CanalCompliance` e `Get-NetTCPConnection -LocalPort 8980 -State Listen` |
| `srvapp01` não resolve no navegador | DNS | teste `ping srvapp01`; se falhar, use `http://192.168.0.187:8980` |
| Erro 403 "Origem da requisição não permitida" ao enviar | `PUBLIC_ORIGIN` preenchido com um endereço diferente do usado no navegador | deixe `PUBLIC_ORIGIN=` vazio e reinicie |
| `--test-email` falha com erro de autenticação | senha errada, ou "SMTP autenticado" desligado na caixa, ou MFA | no admin do M365: usuário → Email → Gerenciar aplicativos de email → marcar **SMTP autenticado**. Com MFA, use senha de aplicativo |
| `--test-email` falha com timeout | saída 587 bloqueada | `Test-NetConnection smtp.office365.com -Port 587` |
| Relato não chega, mas a página diz sucesso | `DRY_RUN=true` no `.env` | troque para `false` e reinicie |

---

## 13. Observações de infraestrutura (fora do escopo deste deploy)

Três pontos notados durante o levantamento da VM, para você avaliar depois:

1. **HelpDesk e Gerenciado Contábil rodam em janelas interativas do
   PowerShell.** Eles caem quando a sessão RDP é encerrada ou a VM reinicia. O
   mesmo `install_service_nssm.ps1` usado aqui serve para os dois, mudando o
   executável e a pasta.

2. **A porta 5432 do PostgreSQL responde para toda a rede interna.** Um banco
   normalmente só precisa aceitar conexões da própria máquina. Restringir para
   `127.0.0.1` ou criar uma regra de firewall reduz a superfície de ataque sem
   afetar as aplicações que rodam na mesma VM.

3. **O IIS já está instalado e ativo na porta 80.** Se um dia o canal precisar
   de HTTPS com certificado da empresa, dá para publicá-lo pelo IIS como proxy
   reverso e então trocar o `.env` para o perfil `.env.example`. A aplicação
   não precisa de nenhuma alteração de código.

Um detalhe de segurança: a senha do Gerenciado Contábil aparece em texto claro
na janela do PowerShell. Vale evitar deixar `.env` aberto na tela durante
compartilhamento ou captura.
