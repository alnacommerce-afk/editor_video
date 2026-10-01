# Editor automático de vídeo

Você envia um vídeo bruto e recebe a versão pronta para postar: sem espaços mortos, sem gaguejos, sem
hesitações ("hum", "haaaa", "ééé"), sem as tentativas que o autor refez logo em seguida, com leve aceleração
quando a fala é lenta, enquadrada em 9:16 com o rosto centralizado e com o áudio normalizado para redes sociais.

Tudo roda **no seu computador**: gratuito, open-source, sem API paga e sem enviar o vídeo para nenhum servidor.

## Como usar

1. **Instalar (uma vez):** dê dois cliques em `instalar.bat`. Ele instala Python 3.12, FFmpeg e as bibliotecas.
2. **Abrir o editor:** dê dois cliques em `iniciar.bat`. O navegador abre em `http://127.0.0.1:8765`.
3. **Enviar o vídeo:** arraste o arquivo para "Envie seu vídeo" (ou clique e escolha).
4. Acompanhe as etapas e, no fim, veja o preview e clique em **Baixar vídeo**.

O vídeo final fica em `saida/<nome>_editado.mp4`, com a legenda em `saida/<nome>_editado.srt`.

### Pela linha de comando

```bash
.venv\Scripts\python.exe editar.py "C:\caminho\video.mp4"
```

- `--simular`: só analisa e gera `cortes.json`, sem renderizar (rápido, bom para testar ajustes).
- `--config meus_ajustes.json`: sobrescreve só as chaves que você quiser de `editor.config.json`.

A transcrição fica em cache: depois de mudar a configuração, reprocessar o mesmo vídeo não transcreve de novo.

## Como o pipeline funciona

```
vídeo → análise (ffprobe) → extração do áudio → transcrição (faster-whisper, por palavra)
      → análise das pausas (energia do áudio + palavras) → hesitações/repetições → cortes
      → enquadramento 9:16 (OpenCV) → render por trecho (FFmpeg) → áudio tratado → MP4 final
```

| Módulo (`editor/`)    | O que faz |
|-----------------------|-----------|
| `video_input`         | Lê duração, resolução, FPS, rotação e áudio do arquivo |
| `audio_extraction`    | Extrai o áudio em WAV 16 kHz mono |
| `transcription`       | Transcreve com faster-whisper, com timestamps por palavra → `transcricao.json` |
| `pause_detection`     | Mede a energia do áudio a cada 10 ms, calcula o limiar de silêncio e acha as pausas reais |
| `cut_detection`       | Classifica as pausas por faixa e junta todos os cortes → `cortes.json` |
| `disfluencias`        | Lê a fala palavra por palavra: recomeços, gaguejos, palavras abandonadas, muletas |
| `ritmo`               | Mede a velocidade da fala (sílabas/s) e decide a aceleração (1,0× / 1,1× / 1,2×) |
| `smart_reframing`     | Detecta o rosto e calcula o recorte 9:16 de cada trecho → `enquadramento.json` |
| `audio_processing`    | Passa-alta, redução de ruído moderada, loudness em 2 passadas, compensação de atraso |
| `render`              | Renderiza os trechos em paralelo, une tudo, gera o MP4 e confere o resultado |
| `output`              | Legenda `.srt` (com os tempos do vídeo editado), resumo e arquivos de auditoria |
| `pipeline`            | Orquestra as etapas acima |
| `server` + `web/`     | Interface web local (só biblioteca padrão do Python) |

### Decisões que evitam cortes ruins

- **Onde cortar vem do áudio, não só do Whisper.** O Whisper erra os tempos das palavras em ±100–200 ms
  (e costuma esticar a última palavra da frase), então o corte só acontece onde o áudio está realmente
  em silêncio. A parte central de cada palavra é protegida e nunca vira silêncio.
- **Margens:** ficam 120 ms de silêncio antes e depois de cada fala (configurável).
- **Respirações** entre duas pausas são preservadas.
- **Recomeço** ("a conversão de clique para lá... conversão de clique pela impressão"): quando o autor volta e
  fala de novo começando pelas mesmas 2 palavras, e a nova fala repete pelo menos 60% do trecho anterior,
  a primeira tentativa sai e a versão refeita fica (e é protegida contra outros cortes).
  Não conta como recomeço: frase completa com ponto final seguida de outra parecida ("Eu quero que você saiba.
  Eu quero que você entenda."), nem continuação depois de "e"/"ou" ("quanto investiu e quanto faturou").
- **Gaguejo** ("eu eu vou", "vai vai clicar", "pa- para") e **palavra abandonada** ("e na... nessa semana") saem.
  Repetições de ênfase ("muito muito", "não não") ficam (lista `repeticoes_permitidas`).
- **Muletas** ("hum", "ahn", "haaaa", "ééé") saem em qualquer posição. Sons de voz sem palavra nenhuma
  (que o Whisper nem escreveu) também saem. "É muito importante..." nunca é tocada: "é" como verbo está
  na lista de palavras ambíguas, que só saem quando isoladas por pausas.
- **Ponto de corte:** os tempos do Whisper erram até ~0,4 s, então cada emenda vai para o vale de silêncio real
  mais próximo entre duas palavras — não sobra pedaço de sílaba.
- **Aceleração leve:** a velocidade de fala é medida em sílabas por segundo (sem contar pausas). Abaixo de 5,8 → 1,1×,
  abaixo de 5,0 → 1,2×. A voz mantém o tom (sem efeito "esquilo") e o vídeo inteiro usa a mesma velocidade.
- **Sincronia:** os trechos são alinhados à grade de frames e renderizados com FPS constante, então vídeo e
  áudio têm exatamente a mesma duração em cada trecho. O áudio intermediário é PCM (emenda perfeita) e o atraso
  do filtro de ruído é medido e compensado automaticamente.
- **Trava de segurança:** se os cortes fossem remover mais de 60% do vídeo, o sistema desconfia (áudio
  estranho, limiar errado) e aplica só os cortes óbvios (início/fim sem fala e silêncios mortos).

## Configuração (`editor.config.json`)

**Perfil:** `"perfil": "dinamico"` (padrão) deixa no máximo ~0,25 s entre falas, corta respirações e acelera fala
lenta. `"perfil": "natural"` mantém pausas de até 0,7 s, preserva respirações e não acelera. Os perfis ficam em
`perfis` e sobrescrevem só as chaves que definem.

| Seção / chave | Padrão | Para que serve |
|---|---|---|
| `transcricao.modelo` | `small` | `base` (mais rápido), `small`, `medium` (mais preciso e ~3× mais lento) |
| `pausas.faixas` | ver arquivo | Faixas de duração da pausa e a ação para cada uma: `manter`, `reduzir` (com `manter_segundos`) ou `remover` |
| `pausas.margem_antes_fala` / `margem_depois_fala` | 0,12 s | Silêncio preservado em volta de cada fala |
| `pausas.margem_inicio_video` / `margem_fim_video` | 0,15 / 0,40 s | Respiro no começo e no fim do vídeo |
| `pausas.corte_minimo` | 0,20 s | Cortes menores que isso não valem o "pulo" na imagem |
| `pausas.preservar_respiracoes` | `true` | Mantém respirações entre pausas |
| `pausas.sem_fala_minimo` | 1,5 s | Ruído sem fala a partir dessa duração é tratado como trecho morto |
| `silencio.limiar_db` | `auto` | Limiar de silêncio. `auto` se adapta ao ruído da gravação; ou um número (ex.: `-45`) |
| `hesitacoes.ativo` | `true` | Liga/desliga a remoção de hesitações |
| `hesitacoes.remover_sons_sem_palavras` | `true` | Remove sons de voz sem palavra ("haaaa" não transcrito) a partir de `som_sem_palavra_minimo` |
| `recomecos.ativo` | `true` | Liga/desliga recomeços, gaguejos e palavras abandonadas |
| `recomecos.semelhanca_minima` | 0,60 | Quanto da fala abandonada precisa reaparecer na nova para contar como recomeço (maior = mais conservador) |
| `recomecos.janela_palavras` | 20 | Até quantas palavras para trás procurar o início da tentativa abandonada |
| `velocidade.ativo` / `faixas` | perfil | Aceleração automática e os limites de sílabas/s de cada fator (máximo 1,2×) |
| `hesitacoes.muletas` / `palavras_ambiguas` | ver arquivo | Quais palavras contam como hesitação |
| `repeticoes.ativo` | `true` | Liga/desliga a remoção de frases repetidas/recomeçadas |
| `enquadramento.ativo` | `true` | Liga/desliga a conversão para vertical (desligado = mantém a proporção original) |
| `enquadramento.modo` | `smart` | `smart` (segue o rosto) ou `centro` |
| `enquadramento.sem_rosto` | `desfoque` | Sem rosto no vídeo: `desfoque` (vídeo inteiro sobre fundo desfocado), `centro` ou `barras` |
| `enquadramento.zona_morta` | 0,08 | Quanto o rosto pode se mexer (fração da largura) antes de o recorte acompanhar |
| `audio.normalizar` / `loudness_alvo` | `true` / −14 LUFS | Loudness padrão de Instagram/TikTok/YouTube |
| `audio.pico_maximo` | −1,5 dBTP | Controle de picos (nada estoura) |
| `audio.reducao_ruido` | ativo, 10 dB | Redução de ruído moderada. Aumente com cuidado: acima de ~15 dB a voz fica metálica |
| `saida.largura` / `altura` | 1080×1920 | Resolução final |
| `saida.crf` / `preset` | 18 / `fast` | Qualidade (menor CRF = melhor e maior) e velocidade do x264 |
| `saida.fps` | `original` | Ou um valor fixo, ex.: `30` |
| `processamento.renders_paralelos` | 2 | Trechos renderizados ao mesmo tempo |

## Arquivos de auditoria

Em `trabalho/<id>/` ficam:

- `transcricao.json`: texto e cada palavra com início, fim e confiança;
- `cortes.json`: cada corte com motivo, cada pausa analisada (mantida ou reduzida), hesitações e repetições
  detectadas, trechos mantidos e onde cada um começa no vídeo editado;
- `enquadramento.json`: modo usado e posição do recorte em cada trecho;
- `legendas.srt` e `resultado.json`.

## Testes

```bash
.venv\Scripts\python.exe testes\gerar_videos_teste.py caminho\foto_com_rosto.png
.venv\Scripts\python.exe testes\testar_pipeline.py
.venv\Scripts\python.exe testes\testar_disfluencias.py   # recomeços, gaguejos e o que NÃO pode ser cortado
.venv\Scripts\python.exe testes\testar_sincronia.py      # flash + clique: sincronia em 1,0×, 1,1× e 1,2×
```

São gerados 3 vídeos com fala sintética (voz pt-BR do Windows): horizontal com muitas pausas e pessoa se
movendo, vertical com fala corrida e horizontal sem rosto. O teste confere transcrição, cortes, reframing,
loudness, picos, codecs, resolução, FPS, duração e a sincronia (correlação entre o áudio original e o editado).

## Limitações

- **Velocidade:** sem placa NVIDIA, a transcrição roda no processador. Num i5-3470, um vídeo de 50 s leva cerca de 1 min no total.
  Com GPU NVIDIA + CUDA, o sistema usa a placa automaticamente.
- **Detecção de rosto:** o classificador Haar do OpenCV é bom com rosto de frente e boa luz, mas falha em perfil
  ou com o rosto muito pequeno. Nesses casos o recorte mantém a última posição conhecida e, se não houver rosto
  no vídeo todo, usa o fundo desfocado (não corta às cegas).
- **Hesitações:** dependem do Whisper transcrevê-las. O prompt inicial ajuda, mas às vezes ele omite um "é...".
  Nesse caso a hesitação fica (ela não é removida por engano, só deixa de ser removida).
- **Recomeços:** dependem da transcrição. Se o Whisper errar uma das palavras da tentativa refeita, o recomeço pode passar.
  Recomeços reformulados com palavras totalmente diferentes ("esse produto ajuda" → "com ele você ganha tempo")
  não são detectados — só quando o autor repete boa parte do que disse.
- **Palavras arrastadas** ("nooo", "conseeegue") não são encurtadas por dentro; a aceleração compensa em parte.
- **Vídeo HDR** (iPhone em HDR/Dolby Vision) é convertido para SDR sem mapeamento de tons. As cores podem ficar lavadas;
  grave em SDR ("Alta eficiência" desligado ou HDR desligado) para melhor resultado.
- **Upscale:** um vídeo horizontal 1080p recortado para vertical usa só ~608 px de largura e é ampliado para 1080 px.
  Para máxima nitidez, grave em 4K ou já na vertical.
- Legendas embutidas (queimadas no vídeo) ainda não. O `.srt` já sai pronto para isso.
