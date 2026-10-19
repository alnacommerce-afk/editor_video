"""Teste do planejamento dos emotion graphics, sem renderizar nada.

    .venv\\Scripts\\python.exe testes\\testar_graficos.py [caminho\\transcricao.json]

Usa uma transcrição real (por padrão a da gravação de 19/10/2026 em trabalho/) e confere as regras:
chamada para ação detectada, texto sempre vindo da fala, cartões sem sobreposição, duração e espaçamento.
"""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from editor import config, graficos, transcription  # noqa: E402

PADRAO = RAIZ / "trabalho" / "20261019-152836-8d9409" / "transcricao.json"
falhas = []


def conferir(ok: bool, texto: str):
    print(("  ok   " if ok else "  FALHA ") + texto)
    if not ok:
        falhas.append(texto)


def main() -> int:
    caminho = Path(sys.argv[1]) if len(sys.argv) > 1 else PADRAO
    if not caminho.is_file():
        print(f"Transcrição não encontrada: {caminho}")
        return 1
    cfg = config.carregar()
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    palavras = transcription.palavras(dados)
    pal = [(p["inicio"], p["fim"], p["palavra"]) for p in palavras]   # sem cortes: tempo original = tempo final
    duracao = dados["duracao"]
    itens = graficos.planejar(pal, duracao, cfg)

    print(f"{len(itens)} cartões em {duracao:.1f}s:")
    for i in itens:
        print(f"  {i['inicio']:6.2f}-{i['fim']:6.2f}  {i['tipo']:6}  [{i['rotulo']}] {i['texto']}")

    falada = graficos._norm(" ".join(w[2] for w in pal))
    conferir(len(itens) >= 3, "gera pelo menos 3 cartões num vídeo de 1 minuto")
    conferir(all(b["inicio"] >= a["fim"] for a, b in zip(itens, itens[1:])), "cartões não se sobrepõem")
    conferir(all(i["fim"] - i["inicio"] >= 1.2 for i in itens), "cada cartão fica pelo menos 1,2 s na tela")
    conferir(all(i["fim"] <= duracao for i in itens), "nenhum cartão passa do fim do vídeo")
    conferir(all(graficos._norm(i["texto"].rstrip("…?")).split()[0] in falada for i in itens if i["tipo"] != "numero"),
             "o texto principal de cada cartão vem da fala")
    ctas = [i for i in itens if i["tipo"] == "cta"]
    if "comenta" in falada:
        # Sem cortes a gravação tem as duas tomadas da frase final; no pipeline real a 1ª tomada já saiu.
        conferir(len(ctas) >= 1 and all(graficos._norm(c["texto"]) in falada for c in ctas),
                 "chamada 'comenta X' vira um balão com a palavra pedida")

    # Regras isoladas, com falas inventadas só para o teste (não vão para nenhum vídeo).
    fala = [(0.0, 0.5, "Foram"), (0.5, 1.2, "R$"), (1.2, 2.2, "42.643,45"), (2.2, 3.0, "este"), (3.0, 3.6, "mês.")]
    n = graficos.planejar(fala, 10, cfg)
    conferir(len(n) == 1 and n[0]["tipo"] == "numero" and "42.643,45" in n[0]["texto"], "valor em reais vira cartão de número")
    conferir(graficos.planejar([(0, 0.4, "hum"), (0.4, 0.8, "então")], 5, cfg) == [], "trecho sem conteúdo não gera cartão")
    dupla = [(0, 0.5, "Comenta"), (0.5, 0.9, "aqui"), (0.9, 1.6, "ferramenta."),
             (3.0, 3.5, "Comenta"), (3.5, 3.9, "aqui"), (3.9, 4.6, "ferramenta.")]
    d = graficos.planejar(dupla, 10, cfg)
    conferir(len(d) == 1 and d[0]["fim"] >= 4.6, "mesma chamada dita duas vezes vira um balão só")

    print("\nTudo certo." if not falhas else f"\n{len(falhas)} falha(s).")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
