"""Orquestra o fluxo completo: vídeo → análise → áudio → transcrição → pausas → cortes → enquadramento → render.

`progresso(etapa, fracao, mensagem)` é chamado ao longo do caminho; a interface web e o
modo linha de comando usam o mesmo pipeline.
"""
import shutil
import time
from pathlib import Path

from . import audio_extraction, cut_detection, output, pause_detection, render, smart_reframing, transcription, video_input
from .config import pasta

ETAPAS = [
    ("enviando", "Enviando"),
    ("transcrevendo", "Transcrevendo"),
    ("pausas", "Identificando pausas"),
    ("cortes", "Criando cortes"),
    ("enquadramento", "Ajustando enquadramento"),
    ("renderizando", "Renderizando"),
    ("finalizado", "Finalizado"),
]

# Cortes "óbvios", mantidos mesmo quando o modo de segurança entra em ação.
CORTES_SEGUROS = {"inicio_sem_fala", "fim_sem_fala", "pausa_silencio_morto", "trecho_sem_fala"}


def nome_saida(original: str, cfg: dict) -> Path:
    destino = pasta(cfg, "saida", "pasta")
    base = Path(original).stem + cfg["saida"]["sufixo"]
    caminho, n = destino / f"{base}.mp4", 2
    while caminho.exists():
        caminho, n = destino / f"{base}_{n}.mp4", n + 1
    return caminho


def processar(video: Path, trabalho: Path, cfg: dict, nome_original: str | None = None,
              progresso=lambda etapa, fracao=None, mensagem=None: None, simular: bool = False) -> dict:
    trabalho.mkdir(parents=True, exist_ok=True)
    inicio = time.time()

    # 1–2. Vídeo de entrada + análise
    progresso("transcrevendo", 0.0, "Analisando o vídeo")
    info = video_input.analisar(video)

    # 3. Extração do áudio
    wav = audio_extraction.extrair(video, trabalho / "audio.wav")
    amostras = audio_extraction.carregar(wav)

    # 4. Transcrição com timestamps por palavra
    progresso("transcrevendo", 0.02, f"Transcrevendo com o modelo {cfg['transcricao']['modelo']}")
    transcricao = transcription.transcrever(amostras, info.duracao, cfg, trabalho / "transcricao.json",
                                            lambda f: progresso("transcrevendo", 0.02 + 0.98 * f))
    palavras = transcription.palavras(transcricao)

    # 5. Análise das pausas (energia do áudio + palavras)
    progresso("pausas", 0.1, "Medindo silêncios e ritmo da fala")
    analise = pause_detection.analisar(amostras, audio_extraction.TAXA_ANALISE, palavras, cfg)
    progresso("pausas", 1.0)

    # 6–7. Trechos a remover + geração dos cortes
    progresso("cortes", 0.1, "Decidindo o que sai")
    plano = cut_detection.planejar_cortes(info.duracao, analise, cfg)
    segmentos = cut_detection.segmentos_mantidos(info.duracao, plano["cortes"], info.fps, analise, cfg)
    removido = 100 * (1 - sum(b - a for a, b in segmentos) / info.duracao)
    limite = cfg["seguranca"]["remocao_maxima_percentual"]
    if removido > limite:
        # Algo está estranho (áudio muito baixo, limiar errado...). Na dúvida, preserva: só cortes óbvios.
        plano["avisos"].append(f"Os cortes removeriam {removido:.0f}% do vídeo (limite {limite}%). "
                               "Por segurança, só foram aplicados os cortes de silêncio morto e início/fim.")
        plano["cortes"] = [c for c in plano["cortes"] if c["motivo"] in CORTES_SEGUROS]
        segmentos = cut_detection.segmentos_mantidos(info.duracao, plano["cortes"], info.fps, analise, cfg)

    converter = output.mapa_tempo(segmentos)
    output.salvar_json(trabalho / "cortes.json", {
        "video": info.como_dict(),
        "analise_audio": analise.resumo(),
        "configuracao_pausas": cfg["pausas"],
        "cortes": [{**c, "inicio": round(c["inicio"], 3), "fim": round(c["fim"], 3),
                    "duracao": round(c["fim"] - c["inicio"], 3)} for c in plano["cortes"]],
        "pausas_analisadas": plano["pausas"],
        "hesitacoes": plano["hesitacoes"],
        "repeticoes": plano["repeticoes"],
        "segmentos_mantidos": [{"inicio": round(a, 3), "fim": round(b, 3),
                                "no_video_editado": round(converter(a), 3)} for a, b in segmentos],
        "palavras_descartadas": analise.palavras_descartadas,
        "avisos": plano["avisos"],
    })
    if cfg["saida"]["gerar_srt"]:
        (trabalho / "legendas.srt").write_text(output.gerar_srt(analise.palavras, segmentos, cfg), encoding="utf-8")
    progresso("cortes", 1.0, f"{len(plano['cortes'])} cortes planejados")

    # 8. Enquadramento vertical
    progresso("enquadramento", 0.0, "Procurando o rosto de quem fala")
    enquadramento = smart_reframing.planejar(info, segmentos, cfg, lambda f: progresso("enquadramento", f))
    output.salvar_json(trabalho / "enquadramento.json", {k: v for k, v in enquadramento.items() if k != "filtros"})

    if simular:
        return {"simulacao": True, "segmentos": len(segmentos), "cortes": len(plano["cortes"]),
                "duracao_original": info.duracao, "duracao_final": sum(b - a for a, b in segmentos),
                "trabalho": str(trabalho)}

    # 9–10. Reconstrução + renderização
    duracao_final = sum(b - a for a, b in segmentos)
    progresso("renderizando", 0.0, f"Renderizando {len(segmentos)} trechos")
    trechos = render.renderizar_trechos(info, segmentos, enquadramento["filtros"], cfg, trabalho / "trechos",
                                        lambda f: progresso("renderizando", 0.75 * f))
    saida = nome_saida(nome_original or video.name, cfg)
    progresso("renderizando", 0.75, "Tratando o áudio e gerando o MP4")
    audio = render.finalizar(trechos, saida, duracao_final, cfg, trabalho, lambda f: progresso("renderizando", 0.75 + 0.25 * f))
    verificacao = render.verificar(saida)
    shutil.rmtree(trabalho / "trechos", ignore_errors=True)

    if cfg["saida"]["gerar_srt"]:
        shutil.copyfile(trabalho / "legendas.srt", saida.with_suffix(".srt"))

    resultado = output.resumo(info, segmentos, plano, verificacao, enquadramento)
    resultado.update({"arquivo": str(saida), "srt": str(saida.with_suffix(".srt")) if cfg["saida"]["gerar_srt"] else None,
                      "audio": audio, "tempo_processamento": round(time.time() - inicio, 1), "trabalho": str(trabalho)})
    if not verificacao["sincronizado"]:
        resultado["avisos"].append(f"Diferença de {verificacao['diferenca_av_ms']} ms entre vídeo e áudio.")
    output.salvar_json(trabalho / "resultado.json", resultado)
    progresso("finalizado", 1.0, "Seu vídeo está pronto")
    return resultado
