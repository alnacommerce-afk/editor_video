"""Carrega editor.config.json (e, opcionalmente, um arquivo extra que sobrescreve só algumas chaves)."""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONFIG_PADRAO = RAIZ / "editor.config.json"


def _mesclar(base: dict, extra: dict) -> dict:
    resultado = dict(base)
    for chave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(resultado.get(chave), dict):
            resultado[chave] = _mesclar(resultado[chave], valor)
        else:
            resultado[chave] = valor
    return resultado


def carregar(caminho_extra: str | Path | None = None) -> dict:
    cfg = json.loads(CONFIG_PADRAO.read_text(encoding="utf-8"))
    if caminho_extra:
        cfg = _mesclar(cfg, json.loads(Path(caminho_extra).read_text(encoding="utf-8")))
    # O perfil ("dinamico" ou "natural") sobrescreve só as chaves que ele define.
    perfil = cfg.get("perfil")
    if perfil:
        if perfil not in cfg.get("perfis", {}):
            raise ValueError(f"Perfil '{perfil}' não existe em editor.config.json (opções: {', '.join(cfg['perfis'])}).")
        cfg = _mesclar(cfg, cfg["perfis"][perfil])
    return cfg


def pasta(cfg: dict, secao: str, chave: str) -> Path:
    """Resolve uma pasta configurada relativa à raiz do projeto e garante que existe."""
    p = Path(cfg[secao][chave])
    if not p.is_absolute():
        p = RAIZ / p
    p.mkdir(parents=True, exist_ok=True)
    return p
