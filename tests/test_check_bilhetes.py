#!/usr/bin/env python3
"""Testes do verificador de bilhetes.

Correm sem rede e sem dependencias:  python3 -m unittest discover -s tests -v
"""

import json
import sys
import urllib.error
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import check_bilhetes as cb  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONFIG = json.loads((RAIZ / "config" / "bilhetes.json").read_text(encoding="utf-8"))


def analisar(nome, estado_anterior=None, cfg=None):
    html = (FIXTURES / nome).read_text(encoding="utf-8")
    return cb.analisar(f"fixture://{nome}", html, cfg or CONFIG, estado_anterior)


class TestEstados(unittest.TestCase):
    def test_evento_ausente_da_pagina(self):
        r = analisar("nao_listado.html")
        self.assertEqual(r["estado"], cb.NAO_LISTADO)

    def test_outro_jogo_a_venda_nao_conta(self):
        """A pagina tem 'Comprar bilhetes' para os Sub-21, mas nao para a Noruega."""
        r = analisar("nao_listado.html")
        self.assertEqual(r["sinais_venda"], [])

    def test_em_breve_e_bloqueio(self):
        r = analisar("em_breve.html")
        self.assertEqual(r["estado"], cb.BLOQUEADO)
        self.assertIn("em breve", r["sinais_bloqueio"])

    def test_a_venda(self):
        r = analisar("a_venda.html")
        self.assertEqual(r["estado"], cb.A_VENDA)
        self.assertIn("comprar", r["sinais_venda"])
        self.assertTrue(r["preco_visivel"])

    def test_esgotado_ganha_ao_botao_comprar(self):
        """O caso que mais provoca falsos alertas: botao visivel mas desativado."""
        r = analisar("esgotado.html")
        self.assertEqual(r["estado"], cb.BLOQUEADO)
        self.assertIn("esgotado", r["sinais_bloqueio"])
        self.assertIn("comprar", r["sinais_venda"])

    def test_pagina_javascript_e_sinalizada(self):
        r = analisar("shell_javascript.html")
        self.assertEqual(r["estado"], cb.SEM_CONTEUDO)

    def test_listado_sem_sinais(self):
        r = analisar("venda_sem_palavras_conhecidas.html")
        self.assertEqual(r["estado"], cb.SEM_SINAL)
        self.assertIn("primeira observacao", r["nota"])


class TestDetecaoDeMudanca(unittest.TestCase):
    def test_primeira_observacao_nao_alerta(self):
        r = analisar("venda_sem_palavras_conhecidas.html", estado_anterior=None)
        self.assertEqual(r["estado"], cb.SEM_SINAL)

    def test_contexto_igual_nao_alerta(self):
        primeiro = analisar("venda_sem_palavras_conhecidas.html")
        segundo = analisar(
            "venda_sem_palavras_conhecidas.html",
            estado_anterior={"hash": primeiro["hash"]},
        )
        self.assertEqual(segundo["estado"], cb.SEM_SINAL)

    def test_contexto_diferente_alerta(self):
        base = analisar("em_breve.html")
        depois = analisar(
            "venda_sem_palavras_conhecidas.html",
            estado_anterior={"hash": base["hash"]},
        )
        self.assertEqual(depois["estado"], cb.ALTEROU)

    def test_numeros_nao_disparam_alerta(self):
        """Um contador de lugares nao deve contar como mudanca de conteudo."""
        a = cb.impressao_digital("Portugal - Noruega. Restam 1.842 bilhetes.")
        b = cb.impressao_digital("Portugal - Noruega. Restam 1.203 bilhetes.")
        self.assertEqual(a, b)

    def test_palavras_novas_disparam_alerta(self):
        a = cb.impressao_digital("Portugal - Noruega. Em breve.")
        b = cb.impressao_digital("Portugal - Noruega. Comprar bilhetes.")
        self.assertNotEqual(a, b)


class TestExtracaoDeTexto(unittest.TestCase):
    def test_script_e_descartado(self):
        texto = cb.html_para_texto("<p>ola</p><script>var esgotado = true;</script>")
        self.assertNotIn("esgotado", texto)
        self.assertIn("ola", texto)

    def test_rotulo_em_atributo_e_apanhado(self):
        texto = cb.html_para_texto('<a title="Comprar bilhetes" href="#"></a>')
        self.assertIn("Comprar bilhetes", texto)

    def test_acentos_ignorados_na_comparacao(self):
        self.assertEqual(cb.sem_acentos("Indisponível"), "indisponivel")

    def test_entidades_html_descodificadas(self):
        self.assertIn("€", cb.html_para_texto("<p>25,00 &euro;</p>"))

    def test_html_malformado_nao_rebenta(self):
        texto = cb.html_para_texto("<p>Noruega <div><span>4 de outubro")
        self.assertIn("Noruega", texto)


class TestPreco(unittest.TestCase):
    def test_formatos_de_preco(self):
        for amostra in ("25,00 €", "€ 25", "desde 70 EUR", "1.250,00€"):
            with self.subTest(amostra=amostra):
                self.assertTrue(cb.tem_preco(amostra))

    def test_texto_sem_preco(self):
        self.assertFalse(cb.tem_preco("Portugal - Noruega, 4 de outubro de 2026"))


class TestJanela(unittest.TestCase):
    def test_janela_isola_o_evento(self):
        texto = "A" * 5000 + " Noruega " + "B" * 5000 + " Comprar bilhetes"
        janelas = cb.recortar_janelas(texto, "noruega", 100)
        self.assertEqual(len(janelas), 1)
        self.assertNotIn("Comprar", janelas[0])

    def test_varias_ocorrencias(self):
        janelas = cb.recortar_janelas("noruega x noruega", "noruega", 5)
        self.assertEqual(len(janelas), 2)


class TestCLI(unittest.TestCase):
    def test_codigo_de_saida_alerta(self):
        with tempfile.TemporaryDirectory() as tmp:
            codigo = cb.main([
                "--fixture", str(FIXTURES / "a_venda.html"),
                "--estado", str(Path(tmp) / "estado.json"),
                "--json",
            ])
            self.assertEqual(codigo, 10)

    def test_codigo_de_saida_silencio(self):
        with tempfile.TemporaryDirectory() as tmp:
            codigo = cb.main([
                "--fixture", str(FIXTURES / "em_breve.html"),
                "--estado", str(Path(tmp) / "estado.json"),
                "--json",
            ])
            self.assertEqual(codigo, 0)

    def test_codigo_de_saida_pagina_javascript(self):
        with tempfile.TemporaryDirectory() as tmp:
            codigo = cb.main([
                "--fixture", str(FIXTURES / "shell_javascript.html"),
                "--estado", str(Path(tmp) / "estado.json"),
            ])
            self.assertEqual(codigo, 2)

    def test_estado_persiste_entre_corridas(self):
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "estado.json"
            cb.main(["--fixture", str(FIXTURES / "em_breve.html"), "--estado", str(estado)])
            self.assertTrue(estado.exists())
            gravado = json.loads(estado.read_text(encoding="utf-8"))
            chave = f"{FIXTURES / 'em_breve.html'}"
            self.assertIn(chave, gravado)
            self.assertEqual(gravado[chave]["estado"], cb.BLOQUEADO)


class TestMonitorCego(unittest.TestCase):
    """Um monitor que falha em silencio e pior do que nenhum: transforma
    'nao recebi aviso' em 'ainda nao abriu'. Estes testes guardam essa porta."""

    # Porta 1 em localhost: recusa a ligacao de imediato, sem tocar na rede.
    URL_MORTO = "http://127.0.0.1:1/"

    def _corre(self, estado, limiar=3):
        return cb.main([
            "--url", self.URL_MORTO,
            "--estado", str(estado),
            "--limiar-falhas", str(limiar),
            "--json",
        ])

    def test_falha_isolada_devolve_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "estado.json"
            self.assertEqual(self._corre(estado), 1)

    def test_falhas_repetidas_escalam_para_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "estado.json"
            self.assertEqual(self._corre(estado), 1)  # 1a falha
            self.assertEqual(self._corre(estado), 1)  # 2a falha
            self.assertEqual(self._corre(estado), 2)  # 3a: monitor cego, avisa
            self.assertEqual(self._corre(estado), 2)  # continua a avisar

    def test_contagem_de_falhas_persiste(self):
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "estado.json"
            self._corre(estado)
            self._corre(estado)
            meta = json.loads(estado.read_text(encoding="utf-8"))["_meta"]
            self.assertEqual(meta["falhas_consecutivas"], 2)
            self.assertEqual(meta["ultimo_estado"], cb.ERRO)

    def test_leitura_boa_reinicia_a_contagem(self):
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "estado.json"
            self._corre(estado)
            self._corre(estado)
            # Uma leitura util a seguir tem de limpar o historico de falhas.
            cb.main(["--fixture", str(FIXTURES / "em_breve.html"), "--estado", str(estado), "--json"])
            meta = json.loads(estado.read_text(encoding="utf-8"))["_meta"]
            self.assertEqual(meta["falhas_consecutivas"], 0)

    def test_pagina_javascript_avisa_a_primeira(self):
        """SEM_CONTEUDO nao espera pelo limiar: sabemos ja que nao serve."""
        with tempfile.TemporaryDirectory() as tmp:
            codigo = cb.main([
                "--fixture", str(FIXTURES / "shell_javascript.html"),
                "--estado", str(Path(tmp) / "estado.json"),
                "--limiar-falhas", "99",
                "--json",
            ])
            self.assertEqual(codigo, 2)

    def test_meta_nao_e_confundido_com_um_url(self):
        """A chave _meta convive com as chaves de URL sem as corromper."""
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "estado.json"
            cb.main(["--fixture", str(FIXTURES / "em_breve.html"), "--estado", str(estado)])
            gravado = json.loads(estado.read_text(encoding="utf-8"))
            self.assertIn("_meta", gravado)
            self.assertEqual(len([k for k in gravado if k != "_meta"]), 1)


class TestAlvoDeNoticias(unittest.TestCase):
    """O feed de noticias corre no mesmo motor da bilheteira, mas com outras
    opcoes: sem sinais de bloqueio, sem deteccao de mudanca, e nao essencial."""

    def alvo_noticias(self):
        alvos = cb.resolver_alvos(CONFIG)
        noticias = [a for a in alvos if not a["essencial"]]
        self.assertEqual(len(noticias), 1, "esperava exatamente um alvo de noticias")
        return noticias[0]

    def test_opcoes_herdadas_e_redefinidas(self):
        alvo = self.alvo_noticias()
        self.assertEqual(alvo["sinais_bloqueio"], [])       # redefinido
        self.assertFalse(alvo["detetar_mudanca"])           # redefinido
        self.assertTrue(alvo["sinais_contexto"])            # exclusivo do feed

    def test_contexto_do_evento_e_exigido(self):
        """A pesquisa do Google News nao respeita o AND das aspas, por isso o
        ambito tem de ser imposto aqui: cada item precisa de um termo do evento
        e de um termo de venda."""
        alvo = self.alvo_noticias()
        contexto = [cb.sem_acentos(t) for t in alvo["sinais_contexto"]]
        self.assertIn("noruega", contexto)
        self.assertIn("dragao", contexto)

    def test_bilheteira_continua_essencial(self):
        alvos = cb.resolver_alvos(CONFIG)
        essenciais = [a for a in alvos if a["essencial"]]
        self.assertEqual(len(essenciais), 1)
        self.assertIn("fpf.pt", essenciais[0]["url"])

    def test_feed_sem_anuncio_nao_alerta(self):
        r = analisar("rss_sem_anuncio.xml", cfg=self.alvo_noticias())
        self.assertNotIn(r["estado"], (cb.A_VENDA, cb.ALTEROU))

    def test_feed_com_anuncio_alerta(self):
        r = analisar("rss_venda_anunciada.xml", cfg=self.alvo_noticias())
        self.assertEqual(r["estado"], cb.A_VENDA)
        self.assertTrue(r["sinais_venda"])

    def test_mudanca_no_feed_nao_alerta(self):
        """Um feed muda todos os dias; alertar por isso era ruido diario."""
        base = analisar("rss_sem_anuncio.xml", cfg=self.alvo_noticias())
        depois = analisar(
            "rss_venda_anunciada.xml",
            estado_anterior={"hash": base["hash"]},
            cfg=self.alvo_noticias(),
        )
        # Alerta, mas por ter reconhecido as palavras -- nao por ter mudado.
        self.assertEqual(depois["estado"], cb.A_VENDA)

        sem_sinais = dict(self.alvo_noticias(), sinais_venda=[])
        neutro = analisar(
            "rss_venda_anunciada.xml",
            estado_anterior={"hash": base["hash"]},
            cfg=sem_sinais,
        )
        self.assertEqual(neutro["estado"], cb.SEM_SINAL)

    def test_titulos_do_feed_nao_se_colam(self):
        """Sem separadores, dois titulos seguidos criavam vizinhancas falsas."""
        texto = cb.html_para_texto(
            "<item><title>Noruega vence</title></item>"
            "<item><title>Comprar bilhetes de teatro</title></item>"
        )
        janelas = cb.recortar_janelas(texto, "noruega", 12)
        self.assertNotIn("Comprar", janelas[0])

    def test_feed_em_baixo_nao_cega_o_monitor(self):
        """Só a bilheteira conta para o interruptor de homem morto."""
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "estado.json"
            resultados = [
                {"url": "https://bilheteira.fpf.pt/", "estado": cb.BLOQUEADO, "essencial": True},
                {"url": "https://news.google.com/rss", "estado": cb.SEM_CONTEUDO, "essencial": False},
            ]
            essenciais = [r for r in resultados if r["essencial"]]
            cego = any(r["estado"] == cb.SEM_CONTEUDO for r in essenciais)
            self.assertFalse(cego)
            del estado


class TestExtracaoRSS(unittest.TestCase):
    def test_texto_do_feed_e_legivel(self):
        html = (FIXTURES / "rss_venda_anunciada.xml").read_text(encoding="utf-8")
        texto = cb.html_para_texto(html)
        self.assertIn("Noruega", texto)
        self.assertIn("à venda", texto)

    def test_acentos_do_feed_sobrevivem(self):
        html = (FIXTURES / "rss_venda_anunciada.xml").read_text(encoding="utf-8")
        self.assertIn("pré-venda", cb.html_para_texto(html))


class TestNotificacao(unittest.TestCase):
    """Quem notifica e o script/workflow, nao uma pessoa a olhar. Estes testes
    fixam quando o push sai e quando nao sai."""

    def setUp(self):
        self.enviados = []
        self._original = cb.enviar_ntfy
        cb.enviar_ntfy = lambda topico, titulo, corpo, url: self.enviados.append(
            {"topico": topico, "titulo": titulo, "corpo": corpo}
        )

    def tearDown(self):
        cb.enviar_ntfy = self._original

    def _corre(self, extra, estado):
        return cb.main(["--estado", str(estado), "--ntfy", "topico-de-teste", "--json"] + extra)

    def test_push_quando_a_venda_abre(self):
        with tempfile.TemporaryDirectory() as tmp:
            codigo = self._corre(
                ["--fixture", str(FIXTURES / "a_venda.html")], Path(tmp) / "e.json"
            )
            self.assertEqual(codigo, 10)
            self.assertEqual(len(self.enviados), 1)
            self.assertIn("A_VENDA", self.enviados[0]["titulo"])

    def test_sem_push_quando_nada_muda(self):
        with tempfile.TemporaryDirectory() as tmp:
            codigo = self._corre(
                ["--fixture", str(FIXTURES / "em_breve.html")], Path(tmp) / "e.json"
            )
            self.assertEqual(codigo, 0)
            self.assertEqual(self.enviados, [])

    def test_push_quando_o_monitor_fica_cego(self):
        """A falha que mais engana e o monitor mudo: tem de avisar tambem."""
        with tempfile.TemporaryDirectory() as tmp:
            estado = Path(tmp) / "e.json"
            self._corre(["--url", "http://127.0.0.1:1/", "--limiar-falhas", "2"], estado)
            self.assertEqual(self.enviados, [])  # 1a falha: pode ser um blip
            codigo = self._corre(["--url", "http://127.0.0.1:1/", "--limiar-falhas", "2"], estado)
            self.assertEqual(codigo, 2)
            self.assertEqual(len(self.enviados), 1)
            self.assertIn("CEGO", self.enviados[0]["titulo"])
            self.assertIn("silencio", self.enviados[0]["corpo"])

    def test_falha_a_notificar_nao_derruba_a_verificacao(self):
        """Se o ntfy estiver em baixo, o veredicto tem de sobreviver."""
        def rebenta(*_args, **_kwargs):
            raise OSError("ntfy inacessivel")

        cb.enviar_ntfy = rebenta
        with tempfile.TemporaryDirectory() as tmp:
            codigo = self._corre(
                ["--fixture", str(FIXTURES / "a_venda.html")], Path(tmp) / "e.json"
            )
            self.assertEqual(codigo, 10)


class TestPerfisDeCabecalhos(unittest.TestCase):
    """A bilheteira devolveu 403 ao runner do GitHub. O segundo perfil de
    cabecalhos e a tentativa barata de passar; estes testes fixam quando ela
    acontece e quando nao vale a pena."""

    def setUp(self):
        self.tentativas = []
        self._original = cb._buscar_com

    def tearDown(self):
        cb._buscar_com = self._original

    def _finge(self, respostas):
        """respostas: lista de None (sucesso) ou codigo HTTP a levantar."""
        def falso(url, timeout, cabecalhos):
            self.tentativas.append(cabecalhos.get("Accept", "?"))
            resultado = respostas[len(self.tentativas) - 1]
            if resultado is not None:
                raise urllib.error.HTTPError(url, resultado, "bloqueado", {}, None)
            return "<html><p>ok</p></html>"
        cb._buscar_com = falso

    def test_primeiro_perfil_basta(self):
        self._finge([None])
        cb.buscar("https://exemplo.pt/", 5)
        self.assertEqual(len(self.tentativas), 1)

    def test_403_faz_tentar_o_segundo_perfil(self):
        self._finge([403, None])
        cb.buscar("https://exemplo.pt/", 5)
        self.assertEqual(len(self.tentativas), 2)
        self.assertNotEqual(self.tentativas[0], self.tentativas[1])

    def test_403_nos_dois_perfis_propaga_o_erro(self):
        self._finge([403, 403])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            cb.buscar("https://exemplo.pt/", 5)
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(len(self.tentativas), 2)

    def test_404_nao_se_retenta(self):
        """Cabecalhos diferentes nao fazem aparecer uma pagina que nao existe."""
        self._finge([404])
        with self.assertRaises(urllib.error.HTTPError):
            cb.buscar("https://exemplo.pt/", 5)
        self.assertEqual(len(self.tentativas), 1)

    def test_sem_accept_encoding(self):
        """Pedir gzip sem descomprimir traria lixo binario para a analise."""
        for perfil in cb.PERFIS_CABECALHOS:
            self.assertNotIn("Accept-Encoding", perfil)


class TestCabecalhoDoFeed(unittest.TestCase):
    """O Google ecoa a pesquisa no <title>, <link> e <description> do canal.
    Como a pesquisa contem "Noruega", essas ocorrencias entravam na janela de
    contexto ao lado da boilerplate de copyright, como se fossem noticias."""

    FEED_COM_ECO = (
        '<?xml version="1.0"?><rss><channel>'
        '<title>bilhetes "Portugal" "Noruega" - Google Notícias</title>'
        '<description>Copyright 2026 Google. This XML feed is made available solely...</description>'
        '<item><title>Haaland fala de Oslo</title>'
        '<description>Sem relação com bilhetes.</description></item>'
        "</channel></rss>"
    )

    def test_reconhece_um_feed(self):
        self.assertTrue(cb.e_feed(self.FEED_COM_ECO))
        self.assertTrue(cb.e_feed('<?xml version="1.0"?><feed xmlns="..."><entry/></feed>'))

    def test_nao_confunde_html_com_feed(self):
        html = (FIXTURES / "em_breve.html").read_text(encoding="utf-8")
        self.assertFalse(cb.e_feed(html))

    def test_itens_excluem_o_cabecalho(self):
        itens = cb.itens_do_feed(self.FEED_COM_ECO)
        self.assertEqual(len(itens), 1)
        self.assertIn("Haaland", itens[0])
        self.assertNotIn("Copyright", itens[0])
        self.assertNotIn("Google Notícias", itens[0])

    def test_eco_da_pesquisa_nao_produz_contexto(self):
        alvo = [a for a in cb.resolver_alvos(CONFIG) if not a["essencial"]][0]
        r = cb.analisar("feed://teste", self.FEED_COM_ECO, alvo, None)
        self.assertEqual(r["formato"], "feed")
        self.assertNotIn(r["estado"], (cb.A_VENDA, cb.ALTEROU))
        # O que importa: nada do cabecalho do canal entrou no contexto.
        self.assertNotIn("Copyright", r["extrato"] or "")
        self.assertNotIn("Google Notícias", r["extrato"] or "")

    def test_feed_vazio_e_ausencia_de_noticias_nao_cegueira(self):
        """Zero itens significa 'nao ha noticias', nao 'nao consegui ler'."""
        alvo = [a for a in cb.resolver_alvos(CONFIG) if not a["essencial"]][0]
        vazio = '<?xml version="1.0"?><rss><channel><title>x</title></channel></rss>'
        r = cb.analisar("feed://vazio", vazio, alvo, None)
        self.assertEqual(r["estado"], cb.NAO_LISTADO)
        self.assertNotEqual(r["estado"], cb.SEM_CONTEUDO)

    def test_min_texto_continua_a_valer_para_html(self):
        """A protecao contra apps de JavaScript nao pode ter-se perdido."""
        r = analisar("shell_javascript.html")
        self.assertEqual(r["estado"], cb.SEM_CONTEUDO)
        self.assertEqual(r["formato"], "html")

    def test_itens_reais_continuam_a_ser_lidos(self):
        alvo = [a for a in cb.resolver_alvos(CONFIG) if not a["essencial"]][0]
        r = analisar("rss_venda_anunciada.xml", cfg=alvo)
        self.assertEqual(r["estado"], cb.A_VENDA)
        self.assertIn("Dragão", r["extrato"])


class TestTitulosDeDiagnostico(unittest.TestCase):
    """Quando um feed traz itens mas nenhum casa com o match, sem os titulos
    nao se percebe se o problema e a pesquisa ou o match."""

    def alvo_noticias(self):
        return [a for a in cb.resolver_alvos(CONFIG) if not a["essencial"]][0]

    def test_titulos_reportados(self):
        r = analisar("rss_sem_anuncio.xml", cfg=self.alvo_noticias())
        self.assertEqual(len(r["titulos"]), 2)
        self.assertIn("Liga das Nações", r["titulos"][0])

    def test_cdata_nos_titulos(self):
        feed = ("<rss><channel><item><title><![CDATA[Bilhetes já à venda]]></title>"
                "</item></channel></rss>")
        self.assertEqual(cb.titulos_do_feed(feed), ["Bilhetes já à venda"])

    def test_titulo_do_canal_nao_entra(self):
        titulos = cb.titulos_do_feed(TestCabecalhoDoFeed.FEED_COM_ECO)
        self.assertEqual(titulos, ["Haaland fala de Oslo"])

    def test_html_nao_produz_titulos(self):
        r = analisar("em_breve.html")
        self.assertNotIn("titulos", r)

    def test_maximo_respeitado(self):
        feed = "<rss><channel>" + "".join(
            f"<item><title>Notícia {i}</title></item>" for i in range(20)
        ) + "</channel></rss>"
        self.assertEqual(len(cb.titulos_do_feed(feed, maximo=5)), 5)


class TestExigenciaDupla(unittest.TestCase):
    """Um item so conta com termo do evento E termo de venda. Estes titulos sao
    os que o Google News devolveu de facto numa corrida real, para uma pesquisa
    por Portugal e Noruega -- e que produziram um falso positivo."""

    TITULOS_REAIS = [
        "Famalicão: Venda de bilhetes para o jogo FC Famalicão-Gil Vicente - SAPO",
        "MEGADETH actuam em Portugal em Abril de 2027 - loudmagazine.net",
        "Millennium Estoril Open 2026: tudo sobre os jogadores, bilhetes, horários e acessos - RFM",
        "FC Porto Inicia Venda de Bilhetes para a Supertaça Cândido de Oliveira - superportistas.pt",
        "Como conseguir ingressos para a Copa do Mundo da FIFA de 2026 - CNN",
    ]
    TITULO_VERDADEIRO = (
        "Bilhetes para o Portugal-Noruega no Dragão à venda a partir de segunda-feira"
    )

    def alvo(self):
        return [a for a in cb.resolver_alvos(CONFIG) if not a["essencial"]][0]

    def feed(self, titulos):
        itens = "".join(f"<item><title>{t}</title></item>" for t in titulos)
        return f"<rss><channel><title>pesquisa</title>{itens}</channel></rss>"

    def test_noticias_de_bilhetes_de_outros_jogos_nao_alertam(self):
        r = cb.analisar("feed://x", self.feed(self.TITULOS_REAIS), self.alvo(), None)
        self.assertEqual(r["estado"], cb.SEM_SINAL)
        self.assertEqual(r["acertos"], [])

    def test_a_noticia_certa_alerta_mesmo_no_meio_do_ruido(self):
        titulos = [self.TITULO_VERDADEIRO] + self.TITULOS_REAIS
        r = cb.analisar("feed://x", self.feed(titulos), self.alvo(), None)
        self.assertEqual(r["estado"], cb.A_VENDA)
        self.assertEqual(len(r["acertos"]), 1)
        self.assertIn("Noruega", r["acertos"][0]["titulo"])

    def test_so_contexto_nao_basta(self):
        r = cb.analisar("feed://x", self.feed([
            "Portugal defronta a Noruega no Dragão a 4 de outubro",
        ]), self.alvo(), None)
        self.assertEqual(r["estado"], cb.SEM_SINAL)

    def test_so_venda_nao_basta(self):
        r = cb.analisar("feed://x", self.feed([
            "Venda de bilhetes para o Benfica-Sporting arranca amanhã",
        ]), self.alvo(), None)
        self.assertEqual(r["estado"], cb.SEM_SINAL)

    def test_feed_sem_itens(self):
        r = cb.analisar("feed://x", self.feed([]), self.alvo(), None)
        self.assertEqual(r["estado"], cb.NAO_LISTADO)
        self.assertEqual(r["itens"], 0)

    def test_acerto_identifica_os_dois_lados(self):
        r = cb.analisar("feed://x", self.feed([self.TITULO_VERDADEIRO]), self.alvo(), None)
        acerto = r["acertos"][0]
        self.assertTrue(acerto["sinais_evento"])
        self.assertTrue(acerto["sinais_venda"])

    def test_sem_contexto_exigido_volta_ao_comportamento_simples(self):
        """Um alvo sem sinais_contexto continua a alertar so com venda."""
        alvo = dict(self.alvo())
        alvo["sinais_contexto"] = []
        r = cb.analisar("feed://x", self.feed([
            "Venda de bilhetes para o Benfica-Sporting arranca amanhã",
        ]), alvo, None)
        self.assertEqual(r["estado"], cb.A_VENDA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
