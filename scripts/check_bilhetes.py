#!/usr/bin/env python3
"""Verifica se os bilhetes de um jogo da Selecao ja estao a venda na bilheteira
oficial da FPF (https://bilheteira.fpf.pt/).

Usa apenas a biblioteca padrao do Python, para correr em qualquer sitio sem
instalar nada: cron local, launchd, ou um runner do GitHub Actions.

Como funciona, em tres passos:

1. Descarrega cada alvo configurado (a bilheteira oficial, e feeds RSS de
   noticias) e reduz o HTML/XML a texto simples.
2. Procura o nome do evento (por omissao "noruega") e recorta uma janela de
   texto em volta de cada ocorrencia -- e nessa janela, e so nessa, que procura
   sinais de venda ("comprar", "escolher lugar", ...) e sinais de bloqueio
   ("esgotado", "em breve", ...). O bloqueio tem sempre prioridade.
3. Guarda uma impressao digital da janela num ficheiro de estado. Se a pagina
   do evento mudar de uma execucao para a outra, avisa -- mesmo que os sinais
   do passo 2 nao tenham reconhecido nada. E a rede de seguranca para o caso
   de a FPF usar palavras que nao previmos.

Codigos de saida (pensados para o cron/CI decidir se notifica):
  0   nada a reportar (evento nao listado, esgotado, ou "em breve")
  10  ALERTA -- bilhetes aparentemente a venda, ou a pagina do evento mudou
  2   O MONITOR ESTA CEGO -- ou a pagina nao trouxe texto analisavel
      (tipicamente um site em JavaScript), ou falhou a leitura em varias
      corridas seguidas. Em qualquer dos casos: nao confies no silencio.
  1   erro de leitura isolado (pode ser um blip de rede)

Sobre o codigo 2: um monitor que falha em silencio e pior do que nenhum,
porque transforma "nao recebi aviso" em "ainda nao abriu". Por isso a
contagem de falhas consecutivas vive no ficheiro de estado, e ao atingir
--limiar-falhas o script passa a gritar em vez de devolver 1 caladamente.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

REPO_RAIZ = Path(__file__).resolve().parent.parent
CONFIG_OMISSAO = REPO_RAIZ / "config" / "bilhetes.json"
ESTADO_OMISSAO = REPO_RAIZ / ".cache" / "bilhetes-estado.json"

# Um User-Agent de browser real: bilheteiras costumam recusar clientes anonimos.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Dois perfis de cabecalhos. O primeiro imita uma navegacao normal de browser
# (os Sec-Fetch-* fazem parte do que um Chrome real envia, e a ausencia deles e
# um sinal barato para um WAF). O segundo e minimalista, para o caso de ser a
# abundancia de cabecalhos a levantar suspeita.
#
# Nao pedimos Accept-Encoding: o urllib nao descomprime, e um gzip por
# descomprimir chegava aqui como lixo binario.
PERFIS_CABECALHOS = (
    {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-CH-UA": '"Chromium";v="126", "Not:A-Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"macOS"',
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    },
    {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    },
)

# Codigos onde vale a pena tentar o segundo perfil. Um 404 ou um 500 nao muda
# com cabecalhos diferentes; um 403/406/429/503 pode ser deteccao de bot.
CODIGOS_A_RETENTAR = frozenset({403, 406, 429, 503})

A_VENDA = "A_VENDA"
ALTEROU = "ALTEROU"
BLOQUEADO = "BLOQUEADO"
SEM_SINAL = "SEM_SINAL"
NAO_LISTADO = "NAO_LISTADO"
SEM_CONTEUDO = "SEM_CONTEUDO"
ERRO = "ERRO"

# Quanto maior, mais grave. Serve para escolher o estado global de uma corrida.
GRAVIDADE = {
    ERRO: -1,
    NAO_LISTADO: 0,
    SEM_SINAL: 1,
    BLOQUEADO: 2,
    SEM_CONTEUDO: 3,
    ALTEROU: 4,
    A_VENDA: 5,
}

EXPLICACAO = {
    A_VENDA: "bilhetes aparentemente A VENDA",
    ALTEROU: "a pagina do evento mudou desde a ultima verificacao",
    BLOQUEADO: "evento listado, mas a venda esta fechada (esgotado/em breve)",
    SEM_SINAL: "evento listado, sem sinais reconhecidos de venda nem de bloqueio",
    NAO_LISTADO: "evento ainda nao aparece na pagina",
    SEM_CONTEUDO: "pagina sem texto analisavel (provavelmente carregada por JavaScript)",
    ERRO: "nao foi possivel ler a pagina",
}


# --------------------------------------------------------------------------- #
# HTML -> texto
# --------------------------------------------------------------------------- #

class ExtratorTexto(HTMLParser):
    """Reduz HTML a texto legivel, descartando script/style.

    Tambem recolhe alguns atributos (alt, title, aria-label, value) porque em
    bilheteiras o rotulo do botao de compra vive muitas vezes so ai.
    """

    IGNORAR = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCO = {
        # HTML
        "p", "div", "li", "tr", "td", "th", "br", "section", "article", "header",
        "footer", "nav", "h1", "h2", "h3", "h4", "h5", "h6", "button", "a",
        "option", "label", "span", "table", "ul", "ol",
        # RSS/Atom: sem estes separadores, dois titulos de noticias seguidos
        # colavam-se e criavam vizinhancas que nao existem.
        "item", "entry", "title", "description", "summary", "content",
        "link", "pubdate", "updated", "source", "guid", "channel", "feed",
    }
    ATRIBUTOS_UTEIS = {"alt", "title", "aria-label", "value"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignorar_profundidade = 0
        self._partes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORAR:
            self._ignorar_profundidade += 1
            return
        if self._ignorar_profundidade:
            return
        if tag in self.BLOCO:
            self._partes.append("\n")
        for chave, valor in attrs:
            if chave in self.ATRIBUTOS_UTEIS and valor:
                self._partes.append(f" {valor} ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORAR:
            self._ignorar_profundidade = max(0, self._ignorar_profundidade - 1)
            return
        if self._ignorar_profundidade:
            return
        if tag in self.BLOCO:
            self._partes.append("\n")

    def handle_data(self, dados: str) -> None:
        if not self._ignorar_profundidade:
            self._partes.append(dados)

    def texto(self) -> str:
        return normalizar_espacos("".join(self._partes))


def normalizar_espacos(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace("\xa0", " ")).strip()


# Um feed RSS/Atom traz sempre um cabecalho de canal antes dos itens. Quando a
# pesquisa inclui o nome do evento, o Google ecoa-a no <title>, no <link> e na
# <description> do canal -- e essas ocorrencias, colonizadas pela boilerplate de
# copyright, entravam na janela de contexto como se fossem noticias.
PADRAO_ITEM = re.compile(r"<(item|entry)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
PADRAO_FEED = re.compile(r"<(rss|feed)\b|<channel\b", re.IGNORECASE)


def e_feed(documento: str) -> bool:
    return bool(PADRAO_FEED.search(documento[:4000]))


def itens_do_feed(documento: str) -> list[str]:
    """Os blocos <item>/<entry>, sem o cabecalho do canal."""
    return [m.group(0) for m in PADRAO_ITEM.finditer(documento)]


def html_para_texto(html: str) -> str:
    extrator = ExtratorTexto()
    try:
        extrator.feed(html)
        extrator.close()
    except Exception:
        # HTML malformado: em vez de falhar, cai para uma limpeza bruta.
        return normalizar_espacos(re.sub(r"<[^>]+>", " ", html))
    return extrator.texto()


def sem_acentos(texto: str) -> str:
    """Minusculas e sem acentos, para comparar 'Indisponível' com 'indisponivel'."""
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


# --------------------------------------------------------------------------- #
# Rede
# --------------------------------------------------------------------------- #

def _buscar_com(url: str, timeout: float, cabecalhos: dict) -> str:
    pedido = urllib.request.Request(url, headers=cabecalhos)
    with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
        bruto = resposta.read()
        codificacao = resposta.headers.get_content_charset() or "utf-8"
    return bruto.decode(codificacao, errors="replace")


def buscar(url: str, timeout: float) -> str:
    """Tenta cada perfil de cabecalhos ate um passar.

    Nao ha aqui nenhuma tentativa de contornar uma protecao a serio: se o
    servidor devolve um desafio, devolve 403 e o script reporta-o. Isto so
    cobre o caso banal de um WAF a recusar um cliente que nao se parece com
    browser nenhum.
    """
    ultimo_erro: Exception | None = None
    for indice, cabecalhos in enumerate(PERFIS_CABECALHOS):
        try:
            return _buscar_com(url, timeout, cabecalhos)
        except urllib.error.HTTPError as exc:
            ultimo_erro = exc
            if exc.code not in CODIGOS_A_RETENTAR or indice == len(PERFIS_CABECALHOS) - 1:
                raise
        except (urllib.error.URLError, OSError, ValueError):
            raise
    raise ultimo_erro if ultimo_erro else RuntimeError("nenhum perfil de cabecalhos tentado")


# --------------------------------------------------------------------------- #
# Analise
# --------------------------------------------------------------------------- #

def recortar_janelas(texto: str, alvo: str, janela: int) -> list[str]:
    """Devolve os pedacos de texto em volta de cada ocorrencia de `alvo`."""
    plano = sem_acentos(texto)
    agulha = sem_acentos(alvo)
    if not agulha:
        return [texto]

    recortes: list[str] = []
    inicio = 0
    while True:
        pos = plano.find(agulha, inicio)
        if pos == -1:
            break
        recortes.append(texto[max(0, pos - janela): pos + len(agulha) + janela])
        inicio = pos + len(agulha)
    return recortes


def impressao_digital(contexto: str) -> str:
    """Hash do contexto com os digitos colapsados em '#'.

    Sem isto, um contador de lugares disponiveis ou uma contagem decrescente
    mudariam o hash todos os dias e o alerta ALTEROU perdia significado.
    """
    estavel = re.sub(r"\d", "#", sem_acentos(normalizar_espacos(contexto)))
    return hashlib.sha256(estavel.encode("utf-8")).hexdigest()[:16]


def encontrar_sinais(contexto: str, sinais: list[str]) -> list[str]:
    plano = sem_acentos(contexto)
    return sorted({s for s in sinais if sem_acentos(s) in plano})


def tem_preco(contexto: str) -> bool:
    return bool(re.search(r"(?:€\s*\d|\d[\d .,]*\s*(?:€|eur\b))", contexto, re.IGNORECASE))


def analisar(url: str, html: str, cfg: dict, estado_anterior: dict | None) -> dict:
    feed = e_feed(html)
    if feed:
        # So os itens. Um feed sem itens da texto vazio, e isso e um resultado
        # legitimo ("nao ha noticias"), nao uma falha de leitura.
        texto = html_para_texto("\n".join(itens_do_feed(html)))
    else:
        texto = html_para_texto(html)

    resultado: dict = {
        "url": url,
        "formato": "feed" if feed else "html",
        "tamanho_html": len(html),
        "tamanho_texto": len(texto),
        "sinais_venda": [],
        "sinais_bloqueio": [],
        "preco_visivel": False,
        "hash": None,
        "hash_anterior": (estado_anterior or {}).get("hash"),
        "extrato": "",
    }

    # min_texto existe para apanhar uma pagina cujo conteudo vem por JavaScript.
    # Num feed que soubemos parsear isso nao se aplica: pouco texto significa
    # poucas noticias, o que e informacao e nao cegueira.
    if not feed and len(texto) < cfg["min_texto"]:
        resultado["estado"] = SEM_CONTEUDO
        resultado["extrato"] = texto[:300]
        return resultado

    janelas = recortar_janelas(texto, cfg["match"], cfg["janela"])
    if not janelas:
        resultado["estado"] = NAO_LISTADO
        resultado["hash"] = impressao_digital("")
        return resultado

    contexto = " ... ".join(janelas)
    resultado["hash"] = impressao_digital(contexto)
    resultado["extrato"] = normalizar_espacos(contexto)[:600]
    resultado["sinais_venda"] = encontrar_sinais(contexto, cfg["sinais_venda"])
    resultado["sinais_bloqueio"] = encontrar_sinais(contexto, cfg["sinais_bloqueio"])
    resultado["preco_visivel"] = tem_preco(contexto)

    # Ordem deliberada: o bloqueio ganha sempre ao sinal de venda, porque a
    # pagina de um jogo esgotado costuma manter o botao "Comprar bilhetes"
    # visivel mas desativado -- e um alerta nesse caso seria falso.
    if resultado["sinais_bloqueio"]:
        resultado["estado"] = BLOQUEADO
    elif resultado["sinais_venda"]:
        resultado["estado"] = A_VENDA
    elif (
        cfg.get("detetar_mudanca", True)
        and resultado["hash_anterior"]
        and resultado["hash"] != resultado["hash_anterior"]
    ):
        # Contexto novo sem nenhuma palavra reconhecida: pode ser a venda a
        # abrir com uma formulacao que nao previmos. Vale um aviso.
        #
        # Desligado nos feeds de noticias (detetar_mudanca: false): esses mudam
        # todos os dias por natureza, e o aviso perdia todo o significado.
        resultado["estado"] = ALTEROU
    else:
        resultado["estado"] = SEM_SINAL
        if resultado["hash_anterior"] is None:
            resultado["nota"] = "primeira observacao deste contexto; guardada como referencia"

    return resultado


# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #

def ler_estado(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def gravar_estado(caminho: Path, estado: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(estado, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Notificacoes
# --------------------------------------------------------------------------- #

def enviar_ntfy(topico: str, titulo: str, corpo: str, url_evento: str) -> None:
    pedido = urllib.request.Request(
        f"https://ntfy.sh/{topico}",
        data=corpo.encode("utf-8"),
        headers={
            "Title": titulo.encode("utf-8").decode("latin-1", errors="replace"),
            "Priority": "high",
            "Tags": "soccer",
            "Click": url_evento,
        },
        method="POST",
    )
    with urllib.request.urlopen(pedido, timeout=15) as resposta:
        resposta.read()


def enviar_webhook(url: str, payload: dict) -> None:
    pedido = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(pedido, timeout=15) as resposta:
        resposta.read()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

CHAVES_HERDADAS = (
    "match", "janela", "min_texto", "sinais_venda", "sinais_bloqueio",
    "detetar_mudanca", "essencial",
)


def resolver_alvos(cfg: dict) -> list[dict]:
    """Junta as opcoes globais com as opcoes de cada alvo.

    Cada alvo herda tudo o que nao redefinir. E assim que um feed de noticias
    convive com a bilheteira no mesmo motor: muda so os sinais que procura, a
    janela de contexto, e desliga a deteccao de mudanca.
    """
    globais = {chave: cfg[chave] for chave in CHAVES_HERDADAS if chave in cfg}
    alvos: list[dict] = []
    for entrada in cfg.get("alvos", []):
        # Tolera tanto {"url": ...} como uma string solta.
        alvo = {"url": entrada} if isinstance(entrada, str) else dict(entrada)
        resolvido = {**globais, **alvo}
        resolvido.setdefault("nome", resolvido["url"])
        resolvido.setdefault("detetar_mudanca", True)
        resolvido.setdefault("essencial", True)
        alvos.append(resolvido)
    return alvos


def carregar_config(caminho: Path, args: argparse.Namespace) -> dict:
    cfg = json.loads(caminho.read_text(encoding="utf-8"))
    cfg.setdefault("evento", "evento")
    cfg.setdefault("match", "noruega")
    cfg.setdefault("janela", 600)
    cfg.setdefault("min_texto", 400)
    cfg.setdefault("alvos", [])
    cfg.setdefault("sinais_venda", [])
    cfg.setdefault("sinais_bloqueio", [])
    if args.url:
        # --url substitui a configuracao: um alvo por URL, tudo herdado.
        cfg["alvos"] = [{"url": u} for u in args.url]
    if args.match:
        cfg["match"] = args.match
    if args.janela:
        cfg["janela"] = args.janela
    if args.so_bilheteira:
        cfg["alvos"] = [a for a in cfg["alvos"]
                        if not isinstance(a, dict) or a.get("essencial", True)]
    if not cfg["alvos"] and not args.fixture:
        raise SystemExit("erro: nenhum alvo configurado (usa --url ou preenche config/bilhetes.json)")
    return cfg


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Verifica se os bilhetes de um jogo da Selecao ja estao a venda.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  ./scripts/check_bilhetes.py\n"
            "  ./scripts/check_bilhetes.py --json --sem-estado\n"
            "  ./scripts/check_bilhetes.py --guardar-html /tmp/paginas\n"
            "  NTFY_TOPICO=meu-topico-secreto ./scripts/check_bilhetes.py\n"
        ),
    )
    p.add_argument("--config", type=Path, default=CONFIG_OMISSAO, help="ficheiro de configuracao")
    p.add_argument("--url", action="append", help="URL a verificar (repetivel; substitui a config)")
    p.add_argument("--match", help="texto que identifica o evento (por omissao: da config)")
    p.add_argument("--janela", type=int, help="caracteres de contexto em volta do evento")
    p.add_argument("--estado", type=Path, default=ESTADO_OMISSAO, help="ficheiro de estado")
    p.add_argument("--sem-estado", action="store_true", help="nao ler nem gravar estado")
    p.add_argument("--timeout", type=float, default=30.0, help="timeout de rede em segundos")
    p.add_argument("--so-bilheteira", action="store_true",
                   help="ignorar os feeds de noticias e verificar so a bilheteira")
    p.add_argument("--limiar-falhas", type=int, default=3, metavar="N",
                   help="apos N leituras falhadas seguidas, tratar como monitor cego (omissao: 3)")
    p.add_argument("--json", action="store_true", help="imprime o relatorio em JSON")
    p.add_argument("--fixture", type=Path, action="append",
                   help="analisa um ficheiro HTML local em vez de ir a rede (para testes)")
    p.add_argument("--guardar-html", type=Path, metavar="DIR",
                   help="grava o HTML descarregado, para inspecao/depuracao")
    p.add_argument("--ntfy", default=os.environ.get("NTFY_TOPICO", ""),
                   help="topico ntfy.sh para push no telefone (ou env NTFY_TOPICO)")
    p.add_argument("--webhook", default=os.environ.get("BILHETES_WEBHOOK", ""),
                   help="URL que recebe o relatorio em JSON (ou env BILHETES_WEBHOOK)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    cfg = carregar_config(args.config, args)

    estado_restaurado = (not args.sem_estado) and args.estado.exists()
    estado = {} if args.sem_estado else ler_estado(args.estado)
    meta = estado.get("_meta", {}) if isinstance(estado.get("_meta"), dict) else {}
    falhas_anteriores = int(meta.get("falhas_consecutivas", 0) or 0)
    agora = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    if args.fixture:
        alvos = [{**{c: cfg[c] for c in CHAVES_HERDADAS if c in cfg},
                  "url": str(caminho), "nome": caminho.name,
                  "detetar_mudanca": True, "essencial": True,
                  "_ficheiro": caminho}
                 for caminho in args.fixture]
    else:
        alvos = resolver_alvos(cfg)

    descarregados: list[tuple[dict, str | None, str | None]] = []
    for alvo in alvos:
        ficheiro = alvo.get("_ficheiro")
        if ficheiro is not None:
            try:
                descarregados.append((alvo, ficheiro.read_text(encoding="utf-8"), None))
            except OSError as exc:
                descarregados.append((alvo, None, str(exc)))
            continue
        try:
            html = buscar(alvo["url"], args.timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            descarregados.append((alvo, None, f"{type(exc).__name__}: {exc}"))
            continue
        if args.guardar_html:
            args.guardar_html.mkdir(parents=True, exist_ok=True)
            nome = re.sub(r"[^a-zA-Z0-9]+", "_", alvo["url"]).strip("_")[:80] or "pagina"
            (args.guardar_html / f"{nome}.html").write_text(html, encoding="utf-8")
        descarregados.append((alvo, html, None))

    resultados: list[dict] = []
    for alvo, html, erro in descarregados:
        rotulo = alvo["url"]
        if erro is not None:
            resultados.append({
                "url": rotulo, "nome": alvo["nome"], "essencial": alvo["essencial"],
                "estado": ERRO, "erro": erro,
            })
            continue
        resultado = analisar(rotulo, html or "", alvo, estado.get(rotulo))
        resultado["nome"] = alvo["nome"]
        resultado["essencial"] = alvo["essencial"]
        resultados.append(resultado)
        if not args.sem_estado and resultado.get("hash"):
            estado[rotulo] = {
                "hash": resultado["hash"],
                "estado": resultado["estado"],
                "visto_em": agora,
            }

    estado_global = max(
        (r["estado"] for r in resultados),
        key=lambda e: GRAVIDADE.get(e, 0),
        default=ERRO,
    )
    alerta = estado_global in (A_VENDA, ALTEROU)

    # "Leitura util" = conseguimos mesmo ver a pagina, seja o que for que diga.
    # ERRO e SEM_CONTEUDO nao contam: nesses casos nao sabemos nada.
    #
    # Conta so o que e essencial (a bilheteira). Um feed de noticias em baixo e
    # uma pena, nao e cegueira -- nao vale acordar ninguem por isso.
    essenciais = [r for r in resultados if r.get("essencial", True)]
    referencia = essenciais or resultados
    leitura_util = any(r["estado"] not in (ERRO, SEM_CONTEUDO) for r in referencia)
    falhas = 0 if leitura_util else falhas_anteriores + 1
    monitor_cego = (
        any(r["estado"] == SEM_CONTEUDO for r in referencia)
        or falhas >= args.limiar_falhas
    )

    if not args.sem_estado:
        estado["_meta"] = {
            "falhas_consecutivas": falhas,
            "ultima_corrida": agora,
            "ultimo_estado": estado_global,
        }
        gravar_estado(args.estado, estado)

    relatorio = {
        "evento": cfg["evento"],
        "verificado_em": agora,
        "estado": estado_global,
        "explicacao": EXPLICACAO.get(estado_global, estado_global),
        "alerta": alerta,
        "monitor_cego": monitor_cego,
        "falhas_consecutivas": falhas,
        "limiar_falhas": args.limiar_falhas,
        "estado_restaurado": estado_restaurado,
        "resultados": resultados,
    }

    if args.json:
        print(json.dumps(relatorio, indent=2, ensure_ascii=False))
    else:
        print(f"Evento     : {cfg['evento']}")
        print(f"Verificado : {agora}")
        print(f"Estado     : {estado_global} -- {relatorio['explicacao']}")
        if falhas:
            print(f"Falhas     : {falhas} leitura(s) seguida(s) sem resultado util")
        if monitor_cego:
            print("AVISO      : o monitor esta CEGO -- nao interpretes o silencio")
            print("             como 'ainda nao abriu'. Corre com --guardar-html")
            print("             e ve o que a pagina esta a devolver.")
        if not args.sem_estado and not estado_restaurado:
            print("Nota       : sem estado anterior; a deteccao de mudanca so")
            print("             fica ativa a partir da proxima corrida.")
        for r in resultados:
            etiqueta = "" if r.get("essencial", True) else " (noticias)"
            print(f"\n  {r.get('nome', r['url'])}{etiqueta}")
            print(f"    estado          : {r['estado']}")
            if r.get("erro"):
                print(f"    erro            : {r['erro']}")
                continue
            if r.get("sinais_venda"):
                print(f"    sinais de venda : {', '.join(r['sinais_venda'])}")
            if r.get("sinais_bloqueio"):
                print(f"    sinais de bloqueio: {', '.join(r['sinais_bloqueio'])}")
            if r.get("preco_visivel"):
                print("    preco visivel   : sim")
            if r.get("nota"):
                print(f"    nota            : {r['nota']}")
            if r.get("extrato"):
                print(f"    extrato         : {r['extrato'][:240]}")

    # O push tambem sai quando o monitor fica cego. Um monitor mudo e a falha
    # que mais engana: sem este aviso, a ausencia de notificacao passaria por
    # "ainda nao abriu".
    if alerta or monitor_cego:
        titulo = ("Bilhetes: " + estado_global) if alerta else "Monitor de bilhetes CEGO"
        corpo = f"{cfg['evento']}\n{relatorio['explicacao']}\n" + "\n".join(
            f"- {r.get('nome', r['url'])}: {r['estado']}" for r in resultados
        )
        if monitor_cego and not alerta:
            corpo += "\n\nNao interpretes o silencio como 'ainda nao abriu'."
        essencial_url = next((r["url"] for r in resultados if r.get("essencial", True)), None)
        primeiro_url = essencial_url or "https://bilheteira.fpf.pt/"
        if args.ntfy:
            try:
                enviar_ntfy(args.ntfy, titulo, corpo, primeiro_url)
            except Exception as exc:  # notificar nunca deve derrubar a verificacao
                print(f"aviso: falhou o envio para o ntfy: {exc}", file=sys.stderr)
        if args.webhook:
            try:
                enviar_webhook(args.webhook, relatorio)
            except Exception as exc:
                print(f"aviso: falhou o envio para o webhook: {exc}", file=sys.stderr)

    if alerta:
        return 10
    if monitor_cego:
        return 2
    if estado_global == ERRO:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
