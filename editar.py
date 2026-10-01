"""Linha de comando: python editar.py video.mp4 [--simular] [--config ajustes.json]

Gera saida/video_editado.mp4 (+ .srt). Arquivos de auditoria ficam em trabalho/<video>_<id>/.
"""
import argparse
import hashlib
import sys
import time
from pathlib import Path

from editor import config, pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Editor automático de vídeos para redes sociais")
    parser.add_argument("videos", nargs="+", type=Path, help="um ou mais arquivos de vídeo")
    parser.add_argument("--config", help="JSON com ajustes que sobrescrevem editor.config.json")
    parser.add_argument("--simular", action="store_true", help="só analisa e gera cortes.json, sem renderizar")
    args = parser.parse_args()
    cfg = config.carregar(args.config)

    for video in args.videos:
        if not video.is_file():
            print(f"Arquivo não encontrado: {video}")
            return 1
        estado = video.stat()
        chave = hashlib.sha1(f"{video.resolve()}|{estado.st_size}|{estado.st_mtime_ns}".encode()).hexdigest()[:8]
        trabalho = config.pasta(cfg, "processamento", "pasta_trabalho") / f"{video.stem}_{chave}"
        nomes = dict(pipeline.ETAPAS)
        ultimo = [None, 0.0]

        def progresso(etapa, fracao=None, mensagem=None):
            agora = time.time()
            if etapa != ultimo[0] or mensagem or agora - ultimo[1] > 2:
                texto = f"[{nomes[etapa]}]" + (f" {fracao:.0%}" if fracao is not None else "") + (f" — {mensagem}" if mensagem else "")
                print(texto, flush=True)
                ultimo[0], ultimo[1] = etapa, agora

        print(f"\n=== {video.name} ===")
        r = pipeline.processar(video, trabalho, cfg, progresso=progresso, simular=args.simular)
        if args.simular:
            print(f"Simulação: {r['cortes']} cortes, {r['duracao_original']:.1f}s → {r['duracao_final']:.1f}s. Detalhes em {trabalho}")
            continue
        print(f"\nPronto: {r['arquivo']}")
        print(f"  {r['duracao_original']}s → {r['duracao_final']}s ({r['tempo_removido']}s removidos, {r['cortes']} cortes)")
        print(f"  Enquadramento: {r['enquadramento']} | Sincronia A/V: {r['arquivo_final']['diferenca_av_ms']} ms")
        for aviso in r["avisos"]:
            print(f"  AVISO: {aviso}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
