# EZ Minecraft Server Dashboard — Instruções de Implementação para uma AI

## 1. Objetivo

Implementar uma renovação visual do dashboard do **EZ Minecraft Server** e acrescentar uma secção de métricas de CPU/RAM em tempo real. Este documento serve como especificação de implementação para uma AI de programação. O código de referência original está incluído no fim; reutiliza-o e adapta-o ao repositório real, em vez de reescrever tudo sem necessidade.

## 2. Instruções obrigatórias para a AI

1. **Inspeciona primeiro o repositório**: identifica o backend, o frontend, o `index.html`, `dashboard.css`, `dashboard.js`, os endpoints existentes, o método de autenticação e a forma como o servidor Minecraft é iniciado/parado.
2. **Não assumas uma stack ou API que não esteja confirmada.** Os exemplos de backend neste documento são alternativas ilustrativas, não prova de que o projeto usa Node.js/Express ou Flask.
3. **Preserva a funcionalidade existente.** Antes de alterar código, mapeia os IDs HTML, eventos, formulários, endpoints e payloads consumidos por `dashboard.js`.
4. **Mantém os IDs existentes e a estrutura dos formulários**, salvo se a análise do código provar que uma alteração é necessária. Em particular, não renomeies os IDs apresentados na secção 4.
5. Reutiliza o código de referência abaixo como base. Integra-o com o código atual; não substituas ficheiros completos sem comparar as diferenças.
6. Implementa em alterações pequenas e coerentes. Não deixes botões decorativos sem funcionalidade nem simules métricas reais.
7. Valida o HTML, JavaScript e CSS; testa as ações de controlo do servidor, autenticação, pesquisa/instalação de mods, backups e consola que já existam.
8. Se uma funcionalidade depender do backend, implementa ou adapta o endpoint correspondente. Se não for possível obter dados, mostra um estado explícito de indisponibilidade em vez de inventar valores.
9. Não introduzas dependências npm ou bibliotecas de gráficos sem necessidade. O gráfico de referência usa Canvas nativo.
10. Não exponhas credenciais, tokens, consola administrativa ou endpoints sensíveis sem autenticação e autorização.

## 3. Requisitos visuais do dashboard

### 3.1 Estrutura e navegação
- Criar um layout com sidebar e área principal.
- A sidebar inclui a marca “EZ Minecraft”, navegação por âncoras e controlos de sessão.
- Organizar as funções em cartões (`.card`) com títulos e hierarquia visual clara.
- Tornar o layout responsivo: sidebar horizontal/compacta em ecrãs mais pequenos, formulários adaptáveis e cartões em coluna no telemóvel.
- As ligações da navegação devem apontar para secções que existam.

### 3.2 Linguagem visual
- Tema escuro, fundo com gradientes discretos, painéis translúcidos e contornos subtis.
- Tipografia de interface Inter ou fontes de sistema; JetBrains Mono ou fonte monoespaçada para consola e métricas.
- Estados visuais consistentes para sucesso, aviso, erro, servidor online, parado e a iniciar.
- Botões com variantes primária, secundária, perigosa e discreta, incluindo estados hover/focus.
- Formulários com espaçamento consistente, foco visível e controlos legíveis.
- Consola com aspeto de terminal, texto monoespaçado, quebra de linhas e scroll.
- Garantir contraste, navegação por teclado e foco visível. Não depender apenas da cor para comunicar estado.

### 3.3 Secções funcionais a preservar
- Visão geral e estado do servidor.
- Controlos de iniciar, parar, reiniciar e criar backup.
- Configuração de loader, versão, RAM e porta.
- Definições avançadas: MOTD, máximo de jogadores, modo de jogo, dificuldade, tipo de mundo, view distance, simulation distance, seed e opções booleanas.
- Envio de comandos para a consola.
- Acesso remoto PlayIt: configurar, iniciar/parar túnel e mostrar estado.
- Mods/plugins: pesquisar no Modrinth, atualizar lista, carregar JARs, instalar seleção e listar instalados.
- Administração de jogadores e mundo: OP, De-OP, kick, ban, dia/noite, limpar clima, chuva e save-all.
- Listagem e restauro de backups.
- Saída de consola/logs em tempo real, conforme suportado pelo backend.
- Sessão e logout.

## 4. Compatibilidade: IDs HTML que devem ser preservados

Antes de editar o frontend, verifica que os seguintes IDs continuam presentes e associados aos mesmos controlos:

- Autenticação/sessão: `legacy-auth-hidden`, `session-status`, `logout-button`
- Estado: `overview`, `server-dot`, `status-panel`
- Controlo: `start-button`, `stop-button`, `restart-button`, `backup-button`
- Configuração: `setup`, `setup-form`, `loader-select`, `version-select`, `ram-input`, `port-input`, `create-button`
- Definições avançadas: `advanced`, `motd-input`, `maxplayers-input`, `gamemode-input`, `difficulty-input`, `leveltype-input`, `viewdist-input`, `simdist-input`, `seed-input`, `online-mode`, `pvp-mode`, `spawn-monsters`, `allow-flight`, `nether-mode`, `whitelist-mode`, `command-blocks`
- Comandos: `command-form`, `command-input`
- Acesso remoto: `remote`, `playit-config-button`, `playit-start-button`, `playit-stop-button`, `playit-status-panel`
- Mods: `mods`, `mod-search-query`, `mod-search-button`, `mod-refresh-button`, `mod-upload-input`, `mod-upload-button`, `mod-search-results`, `mod-install-button`, `mod-installed-list`
- Administração: `admin-player`, `admin-op`, `admin-deop`, `admin-kick`, `admin-ban`, `admin-day`, `admin-night`, `admin-clear`, `admin-rain`, `admin-save`
- Backups: `backups`, `backup-list`, `backup-restore-button`
- Consola: `console`, `console-output`

Os IDs de métricas novos são: `metrics`, `metrics-badge`, `cpu-value`, `cpu-sub`, `cpu-min`, `cpu-max`, `cpu-avg`, `ram-value`, `ram-sub`, `ram-min`, `ram-max`, `ram-avg`, `metrics-endpoint`, `metrics-last`, `metrics-toggle`, `cpu-chart`, `ram-chart`.

**Importante:** o código original contém um formulário de autenticação legado escondido e valores de exemplo `admin/admin`. Não transformes esses valores em credenciais funcionais nem os uses como mecanismo de autenticação. Descobre como a autenticação real funciona e elimina credenciais hardcoded se forem desnecessárias.

## 5. Requisitos para métricas CPU/RAM

### 5.1 Interface
- Adicionar uma secção “Recursos do sistema” imediatamente após a visão geral.
- Apresentar dois gráficos de linha/área: CPU (%) e RAM (utilização).
- Mostrar valor atual, mínimo, máximo e média da janela de amostras.
- Mostrar endpoint usado, hora da última atualização e botão Pausar/Retomar.
- Usar Canvas nativo, sem dependência de biblioteca externa.
- Apresentar estados distintos: à espera de dados, ligado, pausado, endpoint inexistente e erro de pedido.
- Em ecrãs estreitos, empilhar os cartões de CPU e RAM.

### 5.2 Recolha e interpretação de dados
- Intervalo de polling de referência: **2 segundos**.
- Histórico de referência: **60 amostras**, aproximadamente 2 minutos.
- O código de referência tenta os endpoints `/api/stats`, `/api/metrics`, `/api/system`, `/api/status` e `/metrics`. Antes de os usar, confirma os endpoints existentes. Não varras endpoints sensíveis ou arbitrários.
- A resposta deve ser JSON e fornecer CPU e memória usada; para calcular percentagem de RAM, também deve fornecer memória total.
- Formato recomendado e explícito para o backend:

```json
{
  "cpu": 12.5,
  "ram": 123456789,
  "ramTotal": 456789012,
  "uptime": 12345
}
```

- `cpu` é percentagem entre 0 e 100; `ram` e `ramTotal` são bytes. Documenta estas unidades e valida valores antes de desenhar.
- As métricas devem refletir o **host/container onde corre o backend**, não necessariamente apenas o processo Java do Minecraft. Se o objetivo for medir o processo do servidor Minecraft, recolhe métricas específicas desse processo e identifica-as na interface.
- Em Docker/Kubernetes, confirma limites de CPU e memória do container. Métricas do host e métricas do container não são equivalentes.
- Se não existir endpoint compatível, implementa-o no backend real ou deixa a interface em estado “sem endpoint”; não inventes dados.
- Evita criar vários timers ou pedidos concorrentes. Trata falhas de rede, HTTP não-2xx e JSON inválido; permite retomar a recolha depois de uma falha.
- Redesenha o Canvas quando o tamanho do ecrã muda. Usa `devicePixelRatio` corretamente e evita distorção do gráfico.
- Atualiza a UI sem usar HTML inseguro para conteúdo vindo do servidor. Prefere `textContent` em vez de `innerHTML` quando o valor é dinâmico.

### 5.3 Segurança e desempenho
- Protege endpoints de métricas se revelarem informação operacional sensível.
- Não exponhas métricas administrativas a utilizadores não autenticados.
- Evita polling demasiado frequente e limita o histórico em memória.
- Não uses o endpoint Prometheus `/metrics` como se fosse JSON: por norma, esse endpoint devolve texto no formato Prometheus. Só o suportes com um parser adequado.
- Se houver proxy reverso, valida autenticação, CORS e política de acesso. Não abras portas adicionais desnecessárias.

## 6. Plano de implementação para a AI

1. **Auditoria:** inspecionar a estrutura do projeto e identificar frontend, backend, autenticação e APIs existentes.
2. **Mapa de compatibilidade:** associar cada botão/formulário ao handler JavaScript e endpoint respetivo; registar todos os IDs antes da alteração.
3. **Renovação visual:** integrar o HTML/CSS de referência preservando os handlers e a estrutura funcional.
4. **Métricas no frontend:** adicionar a secção de métricas, estilos e módulo JavaScript, de preferência num ficheiro dedicado se a arquitetura o permitir.
5. **Métricas no backend:** só depois de confirmar a stack, implementar ou adaptar um endpoint JSON consistente.
6. **Integração:** confirmar autenticação, unidades, limites de recursos e origem das métricas.
7. **Testes:** testar ecrã largo e móvel, estado sem endpoint, erro HTTP, JSON inválido, pausa/retoma, atualização dos gráficos e todos os controlos anteriores.
8. **Revisão final:** apresentar os ficheiros alterados, decisões tomadas, endpoints usados, testes efetuados e limitações ainda existentes.

## 7. Critérios de aceitação

- [ ] O dashboard apresenta a sidebar e os cartões com tema escuro coerente.
- [ ] A navegação por âncoras funciona.
- [ ] Os IDs existentes e os handlers continuam compatíveis.
- [ ] Os controlos do servidor continuam a chamar as operações reais do backend.
- [ ] A configuração e as definições avançadas continuam funcionais.
- [ ] A consola, PlayIt, mods/plugins, administração e backups continuam funcionais.
- [ ] A secção de métricas mostra valores reais, ou um estado de indisponibilidade claro.
- [ ] CPU e RAM têm gráficos com até 60 amostras, atualizados a cada 2 segundos.
- [ ] Pausar/retomar funciona sem criar intervalos duplicados.
- [ ] A interface é responsiva e utilizável por teclado.
- [ ] Não há credenciais hardcoded nem dados fictícios apresentados como reais.
- [ ] Não foram introduzidas dependências desnecessárias.
- [ ] Foram executados testes adequados à stack real do projeto.

## 8. Instrução final para a AI

Trabalha diretamente sobre o repositório existente. Usa o código abaixo como referência de implementação, mas adapta-o à arquitetura real. **Não afirmes que uma funcionalidade está concluída sem a implementares e testares.** Se encontrares diferenças entre este documento e o código do projeto, preserva o comportamento real já existente e documenta a decisão.

---

# Apêndice A — Código de referência original

O conteúdo abaixo foi fornecido como base visual e funcional. Usa-o como referência para reutilização de código. Antes de copiar, verifica os IDs, os handlers e as APIs do projeto. Alguns trechos são exemplos e podem precisar de correções de integração.



## Fonte original fornecida


Melhorar interface dashboard
aa
Resumo MZ Minecraft Server
Melhorar interface dashboard
Dashboard Minecraft — Interface Renovada
Reescrevi o HTML/CSS com um visual bem mais moderno, mantendo todos os IDs e a estrutura que o teu dashboard.js espera (não vais precisar de mudar o JavaScript). As melhorias principais:

Layout em grelha com sidebar de estado e cards organizados

Tipografia com fonte do sistema e hierarquia mais clara

Tema escuro mais sofisticado (gradiente suave, glassmorphism subtil)

Barras de estado coloridas (running / stopped / erro)

Botões com ícones e estados hover/active

Formulários com melhor espaçamento e focus visível

Consola com aspeto de terminal real

Responsivo para telemóvel

index.html
html
<!doctype html>
<html lang="pt">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>EZ Minecraft Server — Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
      rel="stylesheet"
    />
    <link rel="stylesheet" href="/dashboard.css" />
  </head>
  <body>
    <div class="app">
      <!-- Sidebar -->
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">⛏</div>
          <div>
            <div class="brand-title">EZ Minecraft</div>
            <div class="brand-sub">Server Dashboard</div>
          </div>
        </div>

        <nav class="nav">
          <a href="#overview" class="nav-link active">Visão geral</a>
          <a href="#setup" class="nav-link">Configuração</a>
          <a href="#advanced" class="nav-link">Avançado</a>
          <a href="#mods" class="nav-link">Mods / Plugins</a>
          <a href="#remote" class="nav-link">Acesso remoto</a>
          <a href="#backups" class="nav-link">Backups</a>
          <a href="#console" class="nav-link">Consola</a>
        </nav>

        <div class="sidebar-footer">
          <div class="status-pill" id="session-status">Sessão iniciada</div>
          <button id="logout-button" class="btn btn-ghost" type="button">Terminar sessão</button>
        </div>
      </aside>

      <!-- Main -->
      <main class="main">
        <!-- Legacy form hidden -->
        <form id="legacy-auth-hidden" style="display:none;">
          <input name="username" type="text" value="admin" />
          <input name="password" type="password" value="admin" />
        </form>

        <!-- Hero / status -->
        <section id="overview" class="hero card">
          <div class="hero-text">
            <h1>Bem-vindo de volta 👋</h1>
            <p class="muted">Gere o teu servidor Minecraft sem complicações.</p>
          </div>
          <div class="hero-status">
            <div class="status-dot" id="server-dot"></div>
            <div>
              <div class="status-label">Estado</div>
              <div id="status-panel" class="status-value">A carregar...</div>
            </div>
          </div>
        </section>

        <!-- Controls -->
        <section class="card">
          <div class="card-header">
            <h2>Controlo do servidor</h2>
          </div>
          <div class="btn-grid">
            <button id="start-button" class="btn btn-primary">
              <span class="icon">▶</span> Iniciar
            </button>
            <button id="stop-button" class="btn btn-danger">
              <span class="icon">■</span> Parar
            </button>
            <button id="restart-button" class="btn btn-secondary">
              <span class="icon">↻</span> Reiniciar
            </button>
            <button id="backup-button" class="btn btn-secondary">
              <span class="icon">💾</span> Backup
            </button>
          </div>
        </section>

        <!-- Setup -->
        <section id="setup" class="card">
          <div class="card-header">
            <h2>Configuração do servidor</h2>
            <span class="badge">Loader & versão</span>
          </div>
          <form id="setup-form" class="form-grid">
            <div class="field">
              <label for="loader-select">Loader</label>
              <select id="loader-select" name="loader">
                <option value="vanilla">Vanilla</option>
                <option value="paper">Paper</option>
                <option value="fabric">Fabric</option>
                <option value="forge">Forge</option>
                <option value="neoforge">NeoForge</option>
              </select>
            </div>
            <div class="field">
              <label for="version-select">Versão</label>
              <select id="version-select" name="version"></select>
            </div>
            <div class="field">
              <label for="ram-input">RAM (MB)</label>
              <input id="ram-input" name="ram_mb" type="number" min="512" step="256" value="2048" />
            </div>
            <div class="field">
              <label for="port-input">Porta</label>
              <input id="port-input" name="server_port" type="number" min="1024" max="65535" value="25565" />
            </div>
            <div class="field field-actions">
              <button type="submit" class="btn btn-primary">Guardar</button>
              <button id="create-button" type="button" class="btn btn-secondary">Criar servidor</button>
            </div>
          </form>
        </section>

        <!-- Advanced -->
        <section id="advanced" class="card">
          <div class="card-header">
            <h2>Definições avançadas</h2>
          </div>

          <div class="form-grid">
            <div class="field">
              <label for="motd-input">MOTD</label>
              <input id="motd-input" value="EZ Minecraft Server" />
            </div>
            <div class="field">
              <label for="maxplayers-input">Máx. jogadores</label>
              <input id="maxplayers-input" type="number" min="1" max="1000" value="10" />
            </div>
            <div class="field">
              <label for="gamemode-input">Modo de jogo</label>
              <select id="gamemode-input">
                <option value="survival">Survival</option>
                <option value="creative">Creative</option>
                <option value="adventure">Adventure</option>
                <option value="spectator">Spectator</option>
              </select>
            </div>
            <div class="field">
              <label for="difficulty-input">Dificuldade</label>
              <select id="difficulty-input">
                <option value="peaceful">Peaceful</option>
                <option value="easy">Easy</option>
                <option value="normal">Normal</option>
                <option value="hard">Hard</option>
              </select>
            </div>
            <div class="field">
              <label for="leveltype-input">Tipo de mundo</label>
              <select id="leveltype-input">
                <option value="normal">Normal</option>
                <option value="flat">Flat</option>
                <option value="largebiomes">Large Biomes</option>
                <option value="amplified">Amplified</option>
              </select>
            </div>
            <div class="field">
              <label for="viewdist-input">View distance</label>
              <input id="viewdist-input" type="number" min="3" max="32" value="10" />
            </div>
            <div class="field">
              <label for="simdist-input">Simulation distance</label>
              <input id="simdist-input" type="number" min="3" max="32" value="10" />
            </div>
            <div class="field">
              <label for="seed-input">Seed</label>
              <input id="seed-input" placeholder="vazio = aleatório" />
            </div>
          </div>

          <div class="toggle-grid">
            <label class="toggle"><input id="online-mode" type="checkbox" checked /><span></span>Online mode</label>
            <label class="toggle"><input id="pvp-mode" type="checkbox" checked /><span></span>PvP</label>
            <label class="toggle"><input id="spawn-monsters" type="checkbox" checked /><span></span>Monstros</label>
            <label class="toggle"><input id="allow-flight" type="checkbox" /><span></span>Permitir voo</label>
            <label class="toggle"><input id="nether-mode" type="checkbox" checked /><span></span>Nether</label>
            <label class="toggle"><input id="whitelist-mode" type="checkbox" /><span></span>Whitelist</label>
            <label class="toggle"><input id="command-blocks" type="checkbox" checked /><span></span>Command blocks</label>
          </div>
        </section>

        <!-- Console command -->
        <section class="card">
          <div class="card-header">
            <h2>Comando de consola</h2>
          </div>
          <form id="command-form" class="inline-form">
            <input id="command-input" name="command" placeholder="say hello" />
            <button type="submit" class="btn btn-primary">Enviar</button>
          </form>
        </section>

        <!-- Remote access -->
        <section id="remote" class="card">
          <div class="card-header">
            <h2>Acesso remoto (PlayIt)</h2>
            <span class="badge">Túnel</span>
          </div>
          <div class="btn-grid">
            <button id="playit-config-button" class="btn btn-secondary" type="button">Configurar</button>
            <button id="playit-start-button" class="btn btn-primary" type="button">Iniciar túnel</button>
            <button id="playit-stop-button" class="btn btn-danger" type="button">Parar túnel</button>
          </div>
          <div class="status-line" id="playit-status-panel">PlayIt não configurado.</div>
        </section>

        <!-- Mods -->
        <section id="mods" class="card">
          <div class="card-header">
            <h2>Mods / Plugins</h2>
            <span class="badge">Modrinth</span>
          </div>

          <div class="inline-form">
            <input id="mod-search-query" placeholder="Procurar no Modrinth..." />
            <button id="mod-search-button" class="btn btn-secondary" type="button">Procurar</button>
            <button id="mod-refresh-button" class="btn btn-ghost" type="button">Atualizar</button>
          </div>

          <div class="inline-form">
            <input id="mod-upload-input" type="file" multiple accept=".jar,application/java-archive" />
            <button id="mod-upload-button" class="btn btn-secondary" type="button">Enviar JARs</button>
          </div>

          <div class="split">
            <div class="field">
              <label>Resultados</label>
              <select id="mod-search-results" class="listbox" size="6"></select>
              <button id="mod-install-button" class="btn btn-primary" type="button">Instalar selecionado</button>
            </div>
            <div class="field">
              <label>Instalados</label>
              <select id="mod-installed-list" class="listbox" size="6"></select>
            </div>
          </div>
        </section>

        <!-- Admin -->
        <section class="card">
          <div class="card-header">
            <h2>Comandos de administração</h2>
          </div>

          <div class="inline-form">
            <input id="admin-player" placeholder="Nome do jogador" />
            <button id="admin-op" class="btn btn-secondary" type="button">OP</button>
            <button id="admin-deop" class="btn btn-secondary" type="button">De-OP</button>
            <button id="admin-kick" class="btn btn-secondary" type="button">Kick</button>
            <button id="admin-ban" class="btn btn-danger" type="button">Ban</button>
          </div>

          <div class="inline-form">
            <button id="admin-day" class="btn btn-ghost" type="button">☀ Dia</button>
            <button id="admin-night" class="btn btn-ghost" type="button">🌙 Noite</button>
            <button id="admin-clear" class="btn btn-ghost" type="button">☁ Limpar</button>
            <button id="admin-rain" class="btn btn-ghost" type="button">🌧 Chuva</button>
            <button id="admin-save" class="btn btn-ghost" type="button">💾 Save all</button>
          </div>
        </section>

        <!-- Backups -->
        <section id="backups" class="card">
          <div class="card-header">
            <h2>Backups</h2>
          </div>
          <div class="split">
            <div class="field">
              <label>Backups disponíveis</label>
              <select id="backup-list" class="listbox" size="5"></select>
            </div>
            <div class="field field-actions">
              <button id="backup-restore-button" class="btn btn-secondary" type="button">Restaurar selecionado</button>
            </div>
          </div>
        </section>

        <!-- Console -->
        <section id="console" class="card">
          <div class="card-header">
            <h2>Consola</h2>
            <span class="badge live">● live</span>
          </div>
          <pre id="console-output">A aguardar logs...</pre>
        </section>
      </main>
    </div>

    <script>
      const __LEGACY_CONSOLE_OUTPUT = "join('\\n')";
    </script>
    <script src="/dashboard.js"></script>
  </body>
</html>
dashboard.css
css
:root {
  color-scheme: dark;
  --bg: #070b17;
  --bg-2: #0b1224;
  --panel: rgba(17, 24, 39, 0.72);
  --panel-solid: #0f172a;
  --panel-border: rgba(148, 163, 184, 0.14);
  --text: #e8eefc;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --accent-2: #818cf8;
  --success: #34d399;
  --warning: #fbbf24;
  --danger: #f87171;
  --radius: 14px;
  --shadow: 0 10px 30px rgba(2, 8, 23, 0.45);
}

* { box-sizing: border-box; }

html, body { height: 100%; }

body {
  margin: 0;
  font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
  background: radial-gradient(1200px 600px at 20% -10%, rgba(56, 189, 248, 0.12), transparent 60%),
              radial-gradient(900px 500px at 110% 10%, rgba(129, 140, 248, 0.12), transparent 60%),
              linear-gradient(180deg, var(--bg) 0%, var(--bg-2) 100%);
  color: var(--text);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}

/* ---------- Layout ---------- */
.app {
  display: grid;
  grid-template-columns: 260px 1fr;
  min-height: 100vh;
}

.sidebar {
  position: sticky;
  top: 0;
  align-self: start;
  height: 100vh;
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  border-right: 1px solid var(--panel-border);
  background: rgba(10, 15, 30, 0.65);
  backdrop-filter: blur(10px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--panel-border);
}

.brand-logo {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #0ea5e9, #6366f1);
  font-size: 1.2rem;
  box-shadow: 0 6px 20px rgba(14, 165, 233, 0.35);
}

.brand-title { font-weight: 700; font-size: 1rem; }
.brand-sub { font-size: 0.75rem; color: var(--muted); }

.nav { display: flex; flex-direction: column; gap: 0.25rem; }

.nav-link {
  padding: 0.6rem 0.75rem;
  border-radius: 10px;
  color: var(--muted);
  text-decoration: none;
  font-size: 0.9rem;
  transition: all 0.15s ease;
}

.nav-link:hover { background: rgba(56, 189, 248, 0.08); color: var(--text); }
.nav-link.active {
  background: linear-gradient(90deg, rgba(56, 189, 248, 0.18), transparent);
  color: var(--text);
  border-left: 3px solid var(--accent);
}

.sidebar-footer {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.status-pill {
  font-size: 0.8rem;
  padding: 0.5rem 0.7rem;
  border-radius: 999px;
  background: rgba(52, 211, 153, 0.12);
  color: var(--success);
  border: 1px solid rgba(52, 211, 153, 0.3);
  text-align: center;
}

.main {
  padding: 1.5rem 2rem 4rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}

/* ---------- Cards ---------- */
.card {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  padding: 1.25rem 1.35rem;
  box-shadow: var(--shadow);
  backdrop-filter: blur(8px);
  transition: border-color 0.2s ease;
  scroll-margin-top: 1rem;
}

.card:hover { border-color: rgba(148, 163, 184, 0.28); }

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  gap: 0.75rem;
}

.card h1 { font-size: 1.5rem; margin: 0 0 0.25rem; }
.card h2 { font-size: 1.05rem; margin: 0; font-weight: 600; }
.muted { color: var(--muted); margin: 0; font-size: 0.9rem; }

.badge {
  font-size: 0.7rem;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: rgba(56, 189, 248, 0.12);
  color: var(--accent);
  border: 1px solid rgba(56, 189, 248, 0.25);
  letter-spacing: 0.03em;
  text-transform: uppercase;
  font-weight: 600;
}

.badge.live { color: var(--success); border-color: rgba(52, 211, 153, 0.3); background: rgba(52, 211, 153, 0.1); }

/* ---------- Hero ---------- */
.hero {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.hero-status {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.65rem 1rem;
  background: rgba(2, 8, 23, 0.6);
  border: 1px solid var(--panel-border);
  border-radius: 12px;
}

.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--muted);
  box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.6);
}

.status-dot.online {
  background: var(--success);
  animation: pulse 2s infinite;
}

.status-dot.offline { background: var(--danger); }
.status-dot.starting { background: var(--warning); animation: pulse 1s infinite; }

@keyframes pulse {
  0%   { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55); }
  70%  { box-shadow: 0 0 0 10px rgba(52, 211, 153, 0); }
  100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
}

.status-label { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.status-value { font-weight: 600; font-size: 0.95rem; }

.status-line {
  margin-top: 0.75rem;
  padding: 0.65rem 0.85rem;
  background: rgba(2, 8, 23, 0.6);
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  font-size: 0.88rem;
  color: var(--muted);
}

/* ---------- Forms ---------- */
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.85rem;
  align-items: end;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.field-actions {
  flex-direction: row;
  align-items: flex-end;
  gap: 0.5rem;
  flex-wrap: wrap;
}

label {
  font-size: 0.78rem;
  color: var(--muted);
  font-weight: 500;
  letter-spacing: 0.02em;
}

input, select {
  width: 100%;
  background: rgba(2, 8, 23, 0.7);
  border: 1px solid var(--panel-border);
  color: var(--text);
  padding: 0.65rem 0.8rem;
  border-radius: 10px;
  font: inherit;
  font-size: 0.9rem;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
}

input:hover, select:hover { border-color: rgba(148, 163, 184, 0.35); }

input:focus, select:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.18);
  background: rgba(2, 8, 23, 0.95);
}

input[type="file"] {
  padding: 0.5rem;
  cursor: pointer;
  font-size: 0.85rem;
}

select.listbox {
  background: rgba(2, 8, 23, 0.7);
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  padding: 0.4rem;
  font-family: "JetBrains Mono", monospace;
  font-size: 0.82rem;
}

select.listbox option { padding: 0.35rem 0.5rem; }

/* ---------- Buttons ---------- */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  border-radius: 10px;
  padding: 0.7rem 1rem;
  font-weight: 600;
  font-size: 0.88rem;
  cursor: pointer;
  border: 1px solid transparent;
  transition: transform 0.08s ease, filter 0.15s ease, box-shadow 0.15s ease;
  white-space: nowrap;
  min-height: 42px;
  font-family: inherit;
  color: var(--text);
}

.btn:hover { filter: brightness(1.1); }
.btn:active { transform: translateY(1px); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.btn-primary {
  background: linear-gradient(180deg, #0ea5e9, #0284c7);
  box-shadow: 0 6px 18px rgba(14, 165, 233, 0.35);
}

.btn-secondary {
  background: linear-gradient(180deg, #334155, #1e293b);
  border-color: var(--panel-border);
}

.btn-danger {
  background: linear-gradient(180deg, #ef4444, #b91c1c);
  box-shadow: 0 6px 18px rgba(239, 68, 68, 0.3);
}

.btn-ghost {
  background: transparent;
  border: 1px solid var(--panel-border);
  color: var(--muted);
}

.btn-ghost:hover { color: var(--text); border-color: var(--accent); }

.btn .icon { font-size: 0.9rem; }

.btn-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.65rem;
}

/* ---------- Inline forms ---------- */
.inline-form {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 0.75rem;
}

.inline-form input { flex: 1; min-width: 200px; }

/* ---------- Toggles ---------- */
.toggle-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.6rem;
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--panel-border);
}

.toggle {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.7rem;
  background: rgba(2, 8, 23, 0.4);
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  cursor: pointer;
  font-size: 0.85rem;
  color: var(--text);
  transition: border-color 0.15s ease;
  user-select: none;
}

.toggle:hover { border-color: rgba(148, 163, 184, 0.35); }

.toggle input { display: none; }

.toggle span {
  width: 34px;
  height: 20px;
  background: #1e293b;
  border-radius: 999px;
  position: relative;
  transition: background 0.2s ease;
  flex-shrink: 0;
}

.toggle span::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  background: #94a3b8;
  border-radius: 50%;
  transition: transform 0.2s ease, background 0.2s ease;
}

.toggle input:checked + span { background: rgba(56, 189, 248, 0.35); }
.toggle input:checked + span::after {
  transform: translateX(14px);
  background: var(--accent);
}

/* ---------- Split ---------- */
.split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  align-items: start;
}

/* ---------- Console ---------- */
pre#console-output {
  margin: 0;
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-size: 0.82rem;
  line-height: 1.55;
  color: #cbd5e1;
  background: #020817;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  padding: 1rem;
  min-height: 220px;
  max-height: 380px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

pre#console-output::-webkit-scrollbar { width: 8px; }
pre#console-output::-webkit-scrollbar-thumb {
  background: rgba(148, 163, 184, 0.3);
  border-radius: 4px;
}

/* ---------- Status panel ---------- */
#status-panel { display: none; }

/* ---------- Responsive ---------- */
@media (max-width: 960px) {
  .app { grid-template-columns: 1fr; }
  .sidebar {
    position: relative;
    height: auto;
    flex-direction: row;
    align-items: center;
    flex-wrap: wrap;
    border-right: none;
    border-bottom: 1px solid var(--panel-border);
  }
  .nav {
    flex-direction: row;
    flex-wrap: wrap;
    width: 100%;
    overflow-x: auto;
  }
  .nav-link { white-space: nowrap; }
  .sidebar-footer { margin-top: 0; flex-direction: row; align-items: center; width: 100%; }
  .main { padding: 1rem; }
  .split { grid-template-columns: 1fr; }
  .hero { flex-direction: column; align-items: flex-start; }
}

@media (max-width: 600px) {
  .form-grid { grid-template-columns: 1fr; }
  .btn-grid { grid-template-columns: 1fr 1fr; }
  .card { padding: 1rem; }
  .card-header { flex-direction: column; align-items: flex-start; }
  .inline-form input { min-width: 0; }
}
O que mudou (resumo)
Antes	Agora
Cards todos iguais, sem hierarquia	Sidebar + main com navegação por secções
Sem estado visual do servidor	Ponto pulsante + hero de status
Botões azuis genéricos	Variantes primary / secondary / danger / ghost
Checkboxes nativos	Toggles deslizantes animados
Listas <select> cinzentas	.listbox com fonte mono e fundo profundo
Consola sem personalidade	Estilo terminal real com JetBrains Mono
Sem responsividade	Grid que colapsa em mobile
Importante: mantive todos os ids originais (setup-form, loader-select, status-panel, console-output, mod-search-results, etc.) e a estrutura dos <form> — o teu dashboard.js continua a funcionar sem alterações.

Se quiseres, posso ainda:

Adicionar dark/light mode toggle

Substituir a lista de mods por cards com ícones e versões

Adicionar gráfico de uso de CPU/RAM (se o backend expuser esses dados)

Adicionar gráfico de uso de CPU/RAM (se o backend expuser esses dados)

Isto!
Gráfico de CPU/RAM no Dashboard
Vou adicionar um painel de métricas em tempo real com gráficos animados de CPU e RAM, usando Canvas puro (sem dependências externas — mantém o teu projeto leve e sem npm install).

O código deteta automaticamente se o backend expõe métricas em endpoints comuns (/api/stats, /api/metrics, /metrics, etc.). Se nenhum existir, mostra um estado de "sem dados" em vez de partir.

1. Novo bloco HTML (colar dentro de <main>, logo após o <section id="overview">)
html
<!-- Métricas em tempo real -->
<section id="metrics" class="card">
  <div class="card-header">
    <h2>Recursos do sistema</h2>
    <span class="badge live" id="metrics-badge">● live</span>
  </div>

  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-head">
        <div>
          <div class="metric-label">CPU</div>
          <div class="metric-value" id="cpu-value">—%</div>
        </div>
        <div class="metric-sub" id="cpu-sub">a aguardar…</div>
      </div>
      <canvas id="cpu-chart" height="120"></canvas>
      <div class="metric-foot">
        <span>min <strong id="cpu-min">—</strong></span>
        <span>max <strong id="cpu-max">—</strong></span>
        <span>média <strong id="cpu-avg">—</strong></span>
      </div>
    </div>

    <div class="metric-card">
      <div class="metric-head">
        <div>
          <div class="metric-label">RAM</div>
          <div class="metric-value" id="ram-value">—</div>
        </div>
        <div class="metric-sub" id="ram-sub">a aguardar…</div>
      </div>
      <canvas id="ram-chart" height="120"></canvas>
      <div class="metric-foot">
        <span>min <strong id="ram-min">—</strong></span>
        <span>max <strong id="ram-max">—</strong></span>
        <span>média <strong id="ram-avg">—</strong></span>
      </div>
    </div>
  </div>

  <div class="metrics-meta">
    <span id="metrics-endpoint">endpoint: <code>—</code></span>
    <span id="metrics-last">última atualização: —</span>
    <button id="metrics-toggle" class="btn btn-ghost" type="button">Pausar</button>
  </div>
</section>
2. CSS adicional (acrescentar ao final do dashboard.css)
css
/* ---------- Metrics ---------- */
.metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.metric-card {
  background: rgba(2, 8, 23, 0.55);
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  padding: 0.9rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.metric-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.75rem;
}

.metric-label {
  font-size: 0.72rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
}

.metric-value {
  font-size: 1.5rem;
  font-weight: 700;
  font-family: "JetBrains Mono", monospace;
  color: var(--text);
  line-height: 1.1;
  margin-top: 0.15rem;
}

.metric-sub {
  font-size: 0.78rem;
  color: var(--muted);
  text-align: right;
}

.metric-card canvas {
  width: 100%;
  height: 120px;
  display: block;
  border-radius: 8px;
  background: rgba(2, 8, 23, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.08);
}

.metric-foot {
  display: flex;
  justify-content: space-between;
  font-size: 0.72rem;
  color: var(--muted);
  font-family: "JetBrains Mono", monospace;
}

.metric-foot strong { color: var(--text); font-weight: 600; }

.metrics-meta {
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--panel-border);
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1.5rem;
  align-items: center;
  font-size: 0.78rem;
  color: var(--muted);
}

.metrics-meta code {
  background: rgba(2, 8, 23, 0.7);
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  padding: 0.1rem 0.4rem;
  font-family: "JetBrains Mono", monospace;
  font-size: 0.75rem;
  color: var(--accent);
}

.metrics-meta .btn { margin-left: auto; padding: 0.4rem 0.85rem; min-height: 34px; font-size: 0.8rem; }

@media (max-width: 720px) {
  .metrics-grid { grid-template-columns: 1fr; }
}
3. JavaScript do gráfico
Adiciona isto ao teu dashboard.js no final, ou cria um ficheiro novo metrics.js e inclui-o antes do <script src="/dashboard.js">... mas o mais simples é colar no fim de dashboard.js:

js
/* =========================================================
 *  Real-time CPU / RAM metrics
 * ========================================================= */

(() => {
  const MAX_POINTS = 60;
  const POLL_INTERVAL = 2000;

  // Endpoints conhecidos — tentamos por ordem até um responder
  const ENDPOINTS = [
    "/api/stats",
    "/api/metrics",
    "/api/system",
    "/api/status",
    "/metrics",
  ];

  const $ = (id) => document.getElementById(id);

  const el = {
    cpuValue: $("cpu-value"),
    cpuSub:   $("cpu-sub"),
    cpuMin:   $("cpu-min"),
    cpuMax:   $("cpu-max"),
    cpuAvg:   $("cpu-avg"),
    ramValue: $("ram-value"),
    ramSub:   $("ram-sub"),
    ramMin:   $("ram-min"),
    ramMax:   $("ram-max"),
    ramAvg:   $("ram-avg"),
    endpoint: $("metrics-endpoint"),
    last:     $("metrics-last"),
    toggle:   $("metrics-toggle"),
    badge:    $("metrics-badge"),
  };

  if (!el.cpuValue) return; // secção não existe — sai silenciosamente

  const state = {
    endpoint: null,
    paused: false,
    timer: null,
    cpu: [],
    ram: [],
    last: null,
    lastErr: null,
  };

  // -------- Canvas setup --------
  function setupCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width  = rect.width  * dpr;
    canvas.height = rect.height * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    return { ctx, w: rect.width, h: rect.height };
  }

  const cpuCanvas = $("cpu-chart");
  const ramCanvas = $("ram-chart");

  function drawChart(canvas, data, color, maxValue) {
    const { ctx, w, h } = setupCanvas(canvas);
    ctx.clearRect(0, 0, w, h);

    const pad = 8;
    const chartW = w - pad * 2;
    const chartH = h - pad * 2;

    // grid
    ctx.strokeStyle = "rgba(148, 163, 184, 0.10)";
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = pad + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(pad, y);
      ctx.lineTo(w - pad, y);
      ctx.stroke();
    }

    if (data.length < 2) {
      // placeholder
      ctx.fillStyle = "rgba(148, 163, 184, 0.4)";
      ctx.font = '12px "JetBrains Mono", monospace';
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("a aguardar dados…", w / 2, h / 2);
      return;
    }

    const step = chartW / (MAX_POINTS - 1);
    const startX = pad + chartW - (data.length - 1) * step;

    const toY = (v) => pad + chartH - (Math.min(v, maxValue) / maxValue) * chartH;

    // area
    const grad = ctx.createLinearGradient(0, pad, 0, h - pad);
    grad.addColorStop(0, color.areaTop);
    grad.addColorStop(1, color.areaBottom);

    ctx.beginPath();
    data.forEach((v, i) => {
      const x = startX + i * step;
      const y = toY(v);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    // fechar
    const lastX = startX + (data.length - 1) * step;
    ctx.lineTo(lastX, h - pad);
    ctx.lineTo(startX, h - pad);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // linha
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = startX + i * step;
      const y = toY(v);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color.line;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();

    // último ponto
    const lastY = toY(data[data.length - 1]);
    ctx.beginPath();
    ctx.arc(lastX, lastY, 3, 0, Math.PI * 2);
    ctx.fillStyle = color.line;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(lastX, lastY, 6, 0, Math.PI * 2);
    ctx.strokeStyle = color.line;
    ctx.globalAlpha = 0.3;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  const CPU_STYLE = { line: "#38bdf8", areaTop: "rgba(56,189,248,0.35)", areaBottom: "rgba(56,189,248,0)" };
  const RAM_STYLE = { line: "#818cf8", areaTop: "rgba(129,140,248,0.35)", areaBottom: "rgba(129,140,248,0)" };

  // -------- Helpers --------
  const fmtPct = (n) => (n == null ? "—" : `${n.toFixed(1)}%`);
  const fmtBytes = (b) => {
    if (b == null) return "—";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return `${b.toFixed(b < 10 ? 1 : 0)} ${u[i]}`;
  };

  function stat(arr) {
    if (!arr.length) return { min: null, max: null, avg: null };
    let min = arr[0], max = arr[0], sum = 0;
    for (const v of arr) { if (v < min) min = v; if (v > max) max = v; sum += v; }
    return { min, max, avg: sum / arr.length };
  }

  function applyStats() {
    const cs = stat(state.cpu);
    el.cpuMin.textContent = cs.min == null ? "—" : `${cs.min.toFixed(1)}%`;
    el.cpuMax.textContent = cs.max == null ? "—" : `${cs.max.toFixed(1)}%`;
    el.cpuAvg.textContent = cs.avg == null ? "—" : `${cs.avg.toFixed(1)}%`;

    const rs = stat(state.ram);
    const rPct = state.last && state.last.ramTotal
      ? (v) => `${((v / state.last.ramTotal) * 100).toFixed(1)}%`
      : (v) => fmtBytes(v);
    el.ramMin.textContent = rs.min == null ? "—" : rPct(rs.min);
    el.ramMax.textContent = rs.max == null ? "—" : rPct(rs.max);
    el.ramAvg.textContent = rs.avg == null ? "—" : rPct(rs.avg);
  }

  // -------- Parsing --------
  // Aceita várias formas comuns de resposta:
  //  { cpu: 12.5, ram: 123456789, ramTotal: 456789012 }
  //  { cpu: { usage: 12.5 }, memory: { used: 123, total: 456 } }
  //  { cpuPercent: 12.5, memUsed: 123, memTotal: 456 }
  //  { data: { ... } }  (envelopa)
  function parseSample(raw) {
    if (!raw || typeof raw !== "object") return null;
    if (raw.data && typeof raw.data === "object") raw = raw.data;

    const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

    let cpu =
      num(raw.cpu) ??
      num(raw.cpuUsage) ??
      num(raw.cpuPercent) ??
      num(raw.cpu_percent) ??
      (raw.cpu && typeof raw.cpu === "object"
        ? num(raw.cpu.usage) ?? num(raw.cpu.percent)
        : null);

    const memUsed =
      num(raw.ram) ??
      num(raw.memUsed) ??
      num(raw.memoryUsed) ??
      num(raw.usedMemory) ??
      (raw.memory && typeof raw.memory === "object"
        ? num(raw.memory.used) ?? num(raw.memory.usedBytes)
        : null);

    const memTotal =
      num(raw.ramTotal) ??
      num(raw.memTotal) ??
      num(raw.memoryTotal) ??
      num(raw.totalMemory) ??
      (raw.memory && typeof raw.memory === "object"
        ? num(raw.memory.total) ?? num(raw.memory.totalBytes)
        : null);

    return { cpu, memUsed, memTotal };
  }

  // -------- Fetch --------
  async function tryFetch(endpoint) {
    const res = await fetch(endpoint, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("text/html")) throw new Error("not json");
    return res.json();
  }

  async function findEndpoint() {
    for (const ep of ENDPOINTS) {
      try {
        const data = await tryFetch(ep);
        const sample = parseSample(data);
        if (sample && (sample.cpu != null || sample.memUsed != null)) {
          return { endpoint: ep, data };
        }
      } catch (_) { /* continua */ }
    }
    return null;
  }

  function render() {
    if (state.cpu.length) {
      drawChart(cpuCanvas, state.cpu, CPU_STYLE, 100);
    } else {
      drawChart(cpuCanvas, [], CPU_STYLE, 100);
    }
    const ramMax = state.last && state.last.memTotal ? state.last.memTotal : 1;
    drawChart(ramCanvas, state.ram, RAM_STYLE, ramMax);

    applyStats();

    el.endpoint.innerHTML = state.endpoint
      ? `endpoint: <code>${state.endpoint}</code>`
      : `endpoint: <code>não encontrado</code>`;

    el.last.textContent = state.last
      ? `última atualização: ${new Date().toLocaleTimeString()}`
      : "última atualização: —";
  }

  function pushSample(sample) {
    if (sample.cpu != null) {
      state.cpu.push(sample.cpu);
      if (state.cpu.length > MAX_POINTS) state.cpu.shift();
      el.cpuValue.textContent = fmtPct(sample.cpu);
      el.cpuSub.textContent = "uso atual";
    }
    if (sample.memUsed != null) {
      state.ram.push(sample.memUsed);
      if (state.ram.length > MAX_POINTS) state.ram.shift();
      const total = sample.memTotal ?? state.last?.memTotal;
      el.ramValue.textContent = total
        ? `${fmtBytes(sample.memUsed)} / ${fmtBytes(total)}`
        : fmtBytes(sample.memUsed);
      el.ramSub.textContent = total
        ? `${((sample.memUsed / total) * 100).toFixed(1)}% utilizado`
        : "uso atual";
    }
    state.last = { ...sample, memTotal: sample.memTotal ?? state.last?.memTotal ?? null };
    state.lastErr = null;
    render();
  }

  async function poll() {
    if (state.paused) return;
    try {
      if (!state.endpoint) {
        const found = await findEndpoint();
        if (!found) {
          el.cpuSub.textContent = "sem endpoint";
          el.ramSub.textContent = "sem endpoint";
          el.badge.textContent = "● offline";
          el.badge.style.color = "var(--danger)";
          el.badge.style.borderColor = "rgba(248,113,113,0.3)";
          el.badge.style.background = "rgba(248,113,113,0.1)";
          return;
        }
        state.endpoint = found.endpoint;
        el.badge.textContent = "● live";
        el.badge.style.color = "";
        el.badge.style.borderColor = "";
        el.badge.style.background = "";
        const s = parseSample(found.data);
        if (s) pushSample(s);
      } else {
        const data = await tryFetch(state.endpoint);
        const s = parseSample(data);
        if (s) pushSample(s);
      }
    } catch (err) {
      state.lastErr = err;
      el.cpuSub.textContent = "erro";
      el.ramSub.textContent = "erro";
    }
  }

  // -------- Controls --------
  el.toggle?.addEventListener("click", () => {
    state.paused = !state.paused;
    el.toggle.textContent = state.paused ? "Retomar" : "Pausar";
    el.badge.textContent = state.paused ? "● pausado" : "● live";
    if (!state.paused) poll();
  });

  // Re-desenhar ao redimensionar
  let resizeT;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(render, 150);
  });

  // -------- Boot --------
  state.timer = setInterval(poll, POLL_INTERVAL);
  poll();
})();
4. (Opcional) Endpoint no backend
Se o teu backend ainda não expõe métricas, aqui está um exemplo mínimo em Node.js + Express:

js
const os = require("os");
const { promisify } = require("util");

// CPU: precisa de amostras em intervalos
let lastCpu = os.cpus().map(c => ({ ...c.times }));
function cpuPercent() {
  const now = os.cpus().map(c => ({ ...c.times }));
  let idle = 0, total = 0;
  for (let i = 0; i < now.length; i++) {
    const a = lastCpu[i], b = now[i];
    const idleDelta = b.idle - a.idle;
    const totalDelta =
      (b.user - a.user) + (b.nice - a.nice) + (b.sys - a.sys) +
      (b.irq - a.irq) + (b.idle - a.idle);
    idle += idleDelta;
    total += totalDelta;
  }
  lastCpu = now;
  return total === 0 ? 0 : (1 - idle / total) * 100;
}

// Simples: usa a média entre chamadas
let cpuCache = 0;
setInterval(() => { cpuCache = cpuPercent(); }, 1000);

app.get("/api/stats", (req, res) => {
  const total = os.totalmem();
  const used = total - os.freemem();
  res.json({
    cpu: Math.round(cpuCache * 10) / 10,
    ram: used,
    ramTotal: total,
    uptime: os.uptime(),
  });
});
Em Python (Flask) seria algo como:

python
import psutil
from flask import jsonify

@app.route("/api/stats")
def stats():
    mem = psutil.virtual_memory()
    return jsonify({
        "cpu": psutil.cpu_percent(interval=None),
        "ram": mem.used,
        "ramTotal": mem.total,
    })
Como funciona (resumo)
Auto-discovery — testa uma lista de endpoints comuns (/api/stats, /api/metrics, /metrics…) até um responder com JSON válido.

Parser flexível — aceita várias formas de resposta (cpu, cpuPercent, cpu.usage, memory.used, memUsed…), por isso deves conseguir ligar sem mexer no backend se ele já expõe algo.

Canvas puro — desenha área + linha com gradiente, sem Chart.js. Leve e responsivo.

Polling 2s com até 60 pontos (≈ 2 minutos de histórico).

Pausar / Retomar para inspecionar o gráfico.

Auto-resize — redesenha ao mudar o tamanho da janela.

Estados de erro — se o endpoint não existir, o badge fica vermelho e mostra "sem endpoint" em vez de rebentar.

O que vais ver
text
┌─────────────────────────────────────────────────┐
│ Recursos do sistema                  ● live     │
├──────────────────────┬──────────────────────────┤
│ CPU                  │ RAM                      │
│ 12.4%                │ 1.2 GB / 4.0 GB          │
│  uso atual           │  30.1% utilizado         │
│ ╭───╮                │ ╭────╮                   │
│ │   ╰──╮  ╭──╮       │ │    ╰──╮  ╭──╮          │
│ ─╯     ╰──╯  ╰──     │ ─╯      ╰──╯  ╰──        │
│ min 3.1  max 42.7  média 15.2%                  │
├──────────────────────┴──────────────────────────┤
│ endpoint: /api/stats   última: 14:32:07  [Pausar]│
└─────────────────────────────────────────────────┘
Se o teu backend devolver dados num formato diferente, diz-me o JSON exato que devolve que eu ajusto o parseSample — é só uma função.


