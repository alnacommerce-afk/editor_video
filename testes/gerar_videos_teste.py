"""Gera vídeos de teste com fala real (voz pt-BR do Windows) e roteiro controlado.

    python testes/gerar_videos_teste.py caminho/para/foto_com_rosto.png

Cria em testes/videos/:
  muitas_pausas_horizontal.mp4  1920x1080, pausas longas, "é..." isolado, frase recomeçada, rosto se movendo
  poucas_pausas_vertical.mp4    1080x1920, fala corrida (quase nada deve ser cortado)
  sem_rosto_horizontal.mp4      1920x1080, padrão de teste sem pessoa (deve usar fundo desfocado)
"""
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from editor.ferramentas import binario  # noqa: E402

PASTA = Path(__file__).resolve().parent / "videos"
TAXA = 48000

ROTEIRO_PAUSAS = [
    ("pausa", 2.5),
    ("fala", "Oi, pessoal! Tudo bem com vocês?"), ("pausa", 0.3),
    ("fala", "Hoje eu vou falar sobre"), ("pausa", 0.9),
    ("fala", "Hoje eu vou mostrar três dicas para organizar a sua casa."), ("pausa", 1.8),
    ("fala", "Ééé."), ("pausa", 0.9),
    ("fala", "A primeira dica é separar tudo por categoria."), ("pausa", 0.6),
    ("fala", "É muito importante você saber disso."), ("pausa", 4.0),
    ("fala", "A segunda dica é usar caixas organizadoras."), ("pausa", 1.0),
    ("fala", "Então."), ("pausa", 1.1),
    ("fala", "A terceira dica é manter uma rotina de limpeza."), ("pausa", 0.45),
    ("fala", "Gostou? Então salva esse vídeo e me segue para mais dicas."),
    ("pausa", 3.0),
]

ROTEIRO_CORRIDO = [
    ("pausa", 0.4),
    ("fala", "Você sabia que a maioria das pessoas guarda as roupas do jeito errado?"), ("pausa", 0.25),
    ("fala", "O segredo é dobrar na vertical, assim você enxerga todas as peças de uma vez."), ("pausa", 0.3),
    ("fala", "E o melhor: sobra muito mais espaço na gaveta."), ("pausa", 0.2),
    ("fala", "Testa aí e depois me conta o resultado."),
    ("pausa", 0.5),
]


def falar(texto: str, destino: Path) -> np.ndarray:
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.SelectVoice('Microsoft Maria Desktop'); $s.Rate = 1; "
          f"$s.SetOutputToWaveFile('{destino}'); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=texto, text=True, encoding="utf-8", check=True)
    convertido = destino.with_suffix(".48k.wav")
    subprocess.run([binario("ffmpeg"), "-y", "-loglevel", "error", "-i", str(destino), "-ar", str(TAXA), "-ac", "1",
                    str(convertido)], check=True)
    with wave.open(str(convertido)) as f:
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def montar_audio(roteiro, destino: Path, tmp: Path) -> float:
    rng = np.random.default_rng(1)
    partes = []
    for i, (tipo, valor) in enumerate(roteiro):
        if tipo == "pausa":
            partes.append(np.zeros(int(valor * TAXA), dtype=np.float32))
        else:
            fala = falar(valor, tmp / f"f{i}.wav")
            # O TTS já deixa um pouco de silêncio nas pontas; aparamos para a pausa do roteiro ser a real.
            ativo = np.flatnonzero(np.abs(fala) > 0.01)
            partes.append(fala[max(ativo[0] - 480, 0):ativo[-1] + 2400] if len(ativo) else fala)
    audio = np.concatenate(partes)
    audio = audio * 0.6 + rng.normal(0, 10 ** (-58 / 20), len(audio)).astype(np.float32)   # ruído de sala
    with wave.open(str(destino), "wb") as f:
        f.setnchannels(1), f.setsampwidth(2), f.setframerate(TAXA)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return len(audio) / TAXA


def ffmpeg(*args):
    subprocess.run([binario("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    foto = Path(sys.argv[1])
    PASTA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        dur1 = montar_audio(ROTEIRO_PAUSAS, tmp / "pausas.wav", tmp)
        dur2 = montar_audio(ROTEIRO_CORRIDO, tmp / "corrido.wav", tmp)

        # Horizontal: pessoa começa à direita e caminha devagar para a esquerda no meio do vídeo.
        ffmpeg("-f", "lavfi", "-i", f"color=c=0xd9d4cc:s=1920x1080:r=30000/1001:d={dur1}",
               "-loop", "1", "-i", str(foto), "-i", str(tmp / "pausas.wav"),
               "-filter_complex", "[1:v]scale=-2:1080[p];"
               f"[0:v][p]overlay=x='if(lt(t,8),1050,if(lt(t,14),1050-(t-8)*120,330))':y=0:shortest=1,format=yuv420p[v]",
               "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-shortest",
               str(PASTA / "muitas_pausas_horizontal.mp4"))

        ffmpeg("-f", "lavfi", "-i", f"color=c=0xe8e2d8:s=1080x1920:r=30:d={dur2}",
               "-loop", "1", "-i", str(foto), "-i", str(tmp / "corrido.wav"),
               "-filter_complex", "[1:v]scale=1080:-2[p];[0:v][p]overlay=0:(H-h)/2:shortest=1,format=yuv420p[v]",
               "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-shortest",
               str(PASTA / "poucas_pausas_vertical.mp4"))

        ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=25:d={dur2}", "-i", str(tmp / "corrido.wav"),
               "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-shortest", str(PASTA / "sem_rosto_horizontal.mp4"))
    print(f"Vídeos gerados em {PASTA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
