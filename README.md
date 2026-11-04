# 🎬 Editor IA

Envie seus cortes de vídeo. A IA "assiste" o material e monta a edição como um editor profissional faria,
e o FFmpeg renderiza o MP4 final.

## Como funciona

1. **Análise** (`editor/analyze.py`): metadados, frames-chave, mudanças de cena, silêncios e transcrição
   com tempo por palavra (Whisper local).
2. **Plano de edição** (`editor/planner.py`): o Claude (`claude-opus-5-5`) recebe frames + transcrição + o seu
   briefing e devolve um plano estruturado (`editor/plan.py`): quais trechos usar e em que ordem, cortes em
   limites de palavra, velocidade, transições, zoom/movimento, enquadramento, textos na tela, look de cor,
   estilo de legenda e volume da trilha.
3. **Render** (`editor/render.py`): reenquadra para 9:16 / 16:9 / 1:1 / 4:5 (preencher ou fundo desfocado),
   aplica cor, punch-in/pan, legendas (normal ou com a palavra falada destacada), textos com fade, transições
   (`xfade`), trilha com *ducking* sob a fala e normalização de áudio para redes (-14 LUFS).
4. **Revisão**: peça ajustes em português ("deixe mais curto", "comece pelo aplauso") ou edite o JSON do plano
   e renderize de novo. Cada versão do plano fica em `projetos/<id>/history/`.

## Instalação

Requer Python 3.10+ e FFmpeg (com libass, que já vem nas versões comuns).

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # chave em https://platform.claude.com
```

## Publicar online (usar no celular de qualquer lugar)

O app precisa rodar num servidor (o FFmpeg e a IA não rodam no navegador). O `Dockerfile` já está pronto.

**Railway (mais simples):**
1. Suba esta pasta para um repositório no GitHub.
2. Em https://railway.com → *New Project* → *Deploy from GitHub repo* → escolha o repositório
   (o Railway detecta o `Dockerfile` sozinho).
3. Em *Variables*, crie:
   - `ANTHROPIC_API_KEY` = sua chave da API do Claude
   - `APP_PASSWORD` = uma senha forte (protege o app e seus créditos)
4. No serviço → *Attach volume* → caminho de montagem `/data`
   (sem isso, os vídeos somem a cada atualização).
5. Em *Settings → Networking → Generate Domain*. Abra o endereço no celular, entre com a senha e,
   no menu do navegador, use **"Adicionar à tela de início"**: vira um ícone de app.

Recomendado: pelo menos 2 GB de RAM e 2 vCPUs (a transcrição e a renderização usam bastante CPU).

**Alternativa: servidor próprio (VPS)** com Docker instalado:
```bash
docker build -t editor-ia .
docker run -d --restart unless-stopped -p 80:8000 -v /srv/editor-ia:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... -e APP_PASSWORD=sua-senha editor-ia
```
(para HTTPS, coloque um proxy como Caddy na frente.)

Variáveis opcionais: `MAX_PARALLEL_JOBS` (padrão 1), `WHISPER_MODEL` (`tiny`/`base`/`small`/`medium`).

## Uso local: interface web

```bash
APP_PASSWORD=minhasenha uvicorn app:app --reload
```
Abra http://localhost:8000, escolha os cortes, escreva o briefing e toque em **Editar com IA**.
Sem `APP_PASSWORD` o app abre sem senha: use assim só no seu computador.

## Uso: linha de comando

```bash
python -m editor.cli cortes/*.mp4 --brief "Reels animado do evento, terminar com CTA de inscrição" \
    --aspect 9:16 --seconds 40 --music trilha.mp3 -o projetos/formatura
python -m editor.cli -o projetos/formatura --revise "tire o trecho escuro e coloque o nome da palestrante"
python -m editor.cli -o projetos/formatura --render-only     # depois de editar plan.json à mão
```

## Observações

- A primeira transcrição baixa o modelo Whisper (`small`, ~500 MB). Sem ele, o app funciona sem legendas.
- Vídeos HDR de iPhone são convertidos automaticamente para cores normais (SDR).
- Envie vídeos grandes pelo Wi-Fi; a tela mostra o progresso do envio.
- Custo: cada plano é uma chamada ao Claude com ~60 frames em baixa resolução + transcrição.
- Próximos passos possíveis: B-roll sobreposto à fala, cortes no ritmo da música (detecção de batida),
  remoção automática de "éé/ãã", modelos de marca (logo, fontes, cores), fila de jobs e armazenamento em nuvem.
