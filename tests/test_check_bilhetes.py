#!/usr/bin/env python3
"""Testes do verificador de bilhetes.

Correm sem rede e sem dependencias:  python3 -m unittest discover -s tests -v
"""

import json
import sys
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
        self.assertEqual(alvo["match"], "noruega")          # herdado
        self.assertEqual(alvo["sinais_bloqueio"], [])       # redefinido
        self.assertFalse(alvo["detetar_mudanca"])           # redefinido
        self.assertLess(alvo["janela"], CONFIG["janela"])   # redefinido

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
