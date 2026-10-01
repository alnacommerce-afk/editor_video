"""Recebe o arquivo de vídeo e analisa suas características (duração, resolução, FPS, rotação, áudio)."""
import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .ferramentas import ErroFerramenta, ffprobe

EXTENSOES_ACEITAS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".3gp"}

# FPS "de verdade" que celulares e câmeras usam; um VFR de 29,96 vira 30000/1001.
FPS_PADROES = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
               Fraction(50), Fraction(60000, 1001), Fraction(60), Fraction(120)]


@dataclass
class InfoVideo:
    caminho: Path
    duracao: float
    largura: int        # já considerando a rotação (como o vídeo aparece na tela)
    altura: int
    fps: Fraction
    rotacao: int
    codec_video: str
    tem_audio: bool
    taxa_audio: int
    canais_audio: int

    @property
    def proporcao(self) -> float:
        return self.largura / self.altura

    def ja_esta_na_proporcao(self, alvo: float, tolerancia: float = 0.02) -> bool:
        return abs(self.proporcao - alvo) / alvo < tolerancia

    def como_dict(self) -> dict:
        return {"arquivo": self.caminho.name, "duracao": round(self.duracao, 3), "largura": self.largura,
                "altura": self.altura, "fps": str(self.fps), "rotacao": self.rotacao, "codec_video": self.codec_video,
                "tem_audio": self.tem_audio, "taxa_audio": self.taxa_audio, "canais_audio": self.canais_audio}


def _fracao(texto: str | None) -> Fraction | None:
    try:
        f = Fraction(texto)
        return f if f > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _escolher_fps(stream: dict) -> Fraction:
    media = _fracao(stream.get("avg_frame_rate")) or _fracao(stream.get("r_frame_rate")) or Fraction(30)
    proximo = min(FPS_PADROES, key=lambda p: abs(media - p))
    return proximo if abs(media - proximo) / proximo < 0.03 else media.limit_denominator(1001)


def _rotacao(stream: dict) -> int:
    for dado in stream.get("side_data_list", []) or []:
        if "rotation" in dado:
            return int(round(float(dado["rotation"]))) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    return int(tag) % 360 if tag else 0


def analisar(caminho: Path) -> InfoVideo:
    if caminho.suffix.lower() not in EXTENSOES_ACEITAS:
        raise ErroFerramenta(f"Formato não suportado: {caminho.suffix}. Use {', '.join(sorted(EXTENSOES_ACEITAS))}.")
    dados = json.loads(ffprobe(["-print_format", "json", "-show_format", "-show_streams", str(caminho)],
                               "ler as informações do vídeo"))
    videos = [s for s in dados["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in dados["streams"] if s["codec_type"] == "audio"]
    if not videos:
        raise ErroFerramenta("O arquivo não tem faixa de vídeo.")
    if not audios:
        raise ErroFerramenta("O vídeo não tem áudio — sem fala não há o que editar.")

    v, a = videos[0], audios[0]
    rot = _rotacao(v)
    w, h = int(v["width"]), int(v["height"])
    if rot in (90, 270):
        w, h = h, w
    # A duração útil é a menor entre vídeo e áudio, para nunca pedir frames que não existem.
    duracoes = [float(x) for x in (v.get("duration"), a.get("duration"), dados["format"].get("duration")) if x]
    duracao = min(duracoes[:2]) if len(duracoes) >= 2 else duracoes[0]

    return InfoVideo(caminho=caminho, duracao=duracao, largura=w, altura=h, fps=_escolher_fps(v), rotacao=rot,
                     codec_video=v.get("codec_name", "?"), tem_audio=True, taxa_audio=int(a.get("sample_rate", 48000)),
                     canais_audio=int(a.get("channels", 2)))
