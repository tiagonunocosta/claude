# Monitor de bilhetes — Portugal × Noruega, 4/10/2026, Estádio do Dragão

Verificação automática da **bilheteira oficial da FPF** ([bilheteira.fpf.pt](https://bilheteira.fpf.pt/))
para avisar quando os bilhetes do jogo abrirem para venda.

Liga das Nações 2026/27, 4.ª jornada · domingo, 4 de outubro de 2026, 19h45 · Estádio do Dragão, Porto.

## Só para ficar claro

O canal oficial é **`bilheteira.fpf.pt`** — um subdomínio de `fpf.pt`, na mesma
infraestrutura da federação. Sites como `fpfbilheteira.com` **não são oficiais**:
esse resolve para `23.227.38.70`, um IP partilhado de lojas Shopify, e a FPF não
usa Shopify para nada. Compra apenas em domínios que terminem em `.fpf.pt`.

Se és sócio [Portugal+](https://portugal.fpf.pt/) (gratuito), a pré-venda e o
código de desconto chegam-te por email — continua a ser o aviso mais fiável.
Este monitor é a rede de segurança, não a fonte principal.

## Duas formas de correr

### 1. GitHub Actions (recomendado — não precisa do teu computador ligado)

O workflow [`.github/workflows/bilhetes.yml`](.github/workflows/bilhetes.yml) corre
três vezes por dia (08h, 13h e 19h de Lisboa) e, quando deteta venda aberta,
**abre um issue neste repositório** — e o GitHub envia-te o email.

Não precisa de configuração nem de segredos. Para testar agora:
*Actions → Bilhetes Portugal-Noruega → Run workflow*.

Detalhes que valem a pena saber:

- Comenta o issue existente **só quando o estado muda**, para não encher a caixa
  de correio a cada corrida.
- O relatório de cada corrida fica como artefacto e no resumo da corrida.
- O GitHub desativa workflows agendados em repos sem atividade durante 60 dias.
  Se isso acontecer, reativa em *Actions*.
- Cron do GitHub é *best-effort*: pode atrasar-se dezenas de minutos.

### 2. Localmente, com cron

```bash
./scripts/check_bilhetes.py            # relatório legível
./scripts/check_bilhetes.py --json     # para outros programas
```

Push para o telefone via [ntfy.sh](https://ntfy.sh) (gratuito, sem conta —
instala a app, subscreve um tópico com um nome que ninguém adivinhe):

```bash
NTFY_TOPICO=o-meu-topico-difícil-de-adivinhar ./scripts/check_bilhetes.py
```

No `crontab -e`, três vezes por dia:

```cron
0 8,13,19 * * * cd ~/claude && NTFY_TOPICO=o-meu-topico ./scripts/check_bilhetes.py >> /tmp/bilhetes.log 2>&1
```

Só precisa de Python 3.9+. **Sem dependências** — apenas a biblioteca padrão.

## Dois alvos

| Alvo | O que é | Essencial |
|---|---|---|
| **bilheteira oficial** | `bilheteira.fpf.pt` — a verdade, mas atrás da Cloudflare | sim |
| **notícias** | feed RSS do Google News, filtrado por *bilhetes + Portugal + Noruega* | não |

O feed de notícias não é decoração: a FPF **anuncia a data de abertura antes de
a venda abrir**, e a imprensa noticia-o. Na prática o feed tende a avisar
primeiro. É também o alvo mais robusto — RSS é XML puro, sem JavaScript e sem
proteção anti-bot, exatamente onde a bilheteira é mais frágil.

Corre no mesmo motor, com três diferenças declaradas na config:

- `sinais_bloqueio: []` — numa notícia "em breve" é informação, não é estado da
  bilheteira, e não deve calar um alerta.
- `detetar_mudanca: false` — um feed muda todos os dias; alertar por isso seria
  ruído diário.
- `essencial: false` — um feed em baixo é uma pena, não é cegueira, e não deve
  disparar o interruptor de homem morto.

Para verificar só a bilheteira: `--so-bilheteira`.

## Como decide

1. Descarrega cada alvo de [`config/bilhetes.json`](config/bilhetes.json) e reduz
   o HTML/XML a texto (descartando `script`/`style`, mas apanhando rótulos em
   `title`, `alt` e `aria-label`, onde vive o texto dos botões, e separando as
   tags de RSS para que dois títulos de notícias não se colem).
2. Procura o nome do evento (`"match": "noruega"`) e recorta ±600 caracteres em
   volta. Só dentro dessa janela procura sinais — assim um "Comprar bilhetes"
   de *outro* jogo na mesma página não dispara um falso alerta.
3. **O bloqueio ganha sempre ao sinal de venda.** Uma página esgotada costuma
   manter o botão "Comprar bilhetes" visível mas desativado; sem esta regra, o
   monitor gritava por nada.
4. Guarda uma impressão digital da janela em `.cache/bilhetes-estado.json`. Se a
   página do evento mudar sem que nenhuma palavra conhecida apareça, avisa
   igualmente — é a rede de segurança para o caso de a FPF usar uma formulação
   que não previmos. Os dígitos são colapsados antes do hash, para que um
   contador de lugares não conte como mudança.

### Estados e códigos de saída

| Estado | Significado | Saída |
|---|---|---|
| `A_VENDA` | sinais de compra na janela do evento | **10** — avisa |
| `ALTEROU` | a página do evento mudou desde a última corrida | **10** — avisa |
| `SEM_CONTEUDO` | página sem texto analisável (JavaScript) | **2** — monitor cego, avisa |
| `ERRO` × 3 seguidas | não consegue ler a página | **2** — monitor cego, avisa |
| `BLOQUEADO` | listado, mas "esgotado" / "em breve" | 0 |
| `SEM_SINAL` | listado, sem sinais reconhecidos | 0 |
| `NAO_LISTADO` | o evento ainda não aparece | 0 |
| `ERRO` isolado | falha de leitura pontual (blip de rede) | 1 |

### O interruptor de homem morto

Um monitor que falha em silêncio é **pior** do que nenhum monitor, porque
transforma "não recebi aviso" em "ainda não abriu". Por isso a contagem de
leituras falhadas vive no ficheiro de estado: ao atingir `--limiar-falhas`
(3 por omissão, ou seja um dia de corridas), o script deixa de devolver 1
caladamente e passa a abrir um issue a dizer que está cego.

`SEM_CONTEUDO` não espera pelo limiar — nesse caso já sabemos que a leitura
não serve. Uma leitura útil, qualquer que seja o veredicto, reinicia a contagem.

## O limite honesto deste monitor

O script foi escrito **sem acesso à bilheteira real** (a rede da sessão onde foi
desenvolvido bloqueia `fpf.pt` — devolve `403` no túnel), por isso os sinais de
texto são um palpite informado, validado apenas contra as *fixtures* em
`tests/fixtures/`. A lógica está testada; o contacto com o site não.

Dois riscos, por ordem de probabilidade:

1. **Proteção anti-bot da Cloudflare.** `bilheteira.fpf.pt` está atrás da
   Cloudflare (IPs `104.18.10.225` / `104.18.11.225`). Bilheteiras são alvo de
   bots de revenda e costumam ter a proteção alta. Um pedido de `urllib` pode
   levar `403` ou um desafio JavaScript — e é **mais provável nos runners do
   GitHub**, que saem de IPs de datacenter, do que do teu IP doméstico.
2. **Página renderizada em JavaScript.** O HTML inicial não traria o texto dos
   eventos → `SEM_CONTEUDO`.

Em qualquer dos casos o monitor **avisa** em vez de ficar calado (ver o
interruptor de homem morto, acima). Mas vale mais descobrir agora:

```bash
./scripts/check_bilhetes.py --guardar-html /tmp/paginas
```

- `BLOQUEADO` ou `SEM_SINAL` → está a ler a página; o agendamento é fiável.
- `SEM_CONTEUDO` → o HTML em `/tmp/paginas` mostra com o que lidamos; a correção
  é apontar `--url` ao endpoint JSON que a página consome.
- `ERRO` local mas o workflow também falha → é o IP do runner. Passa o
  agendamento para cron local, que sai do teu IP doméstico.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

40 testes, sem rede: cobrem cada estado, a precedência do bloqueio sobre o botão
de compra, a deteção de mudança, a extração de texto e de RSS, os códigos de
saída, a escalada do interruptor de homem morto e a herança de opções por alvo.

## Ajustar sem tocar no código

Tudo o que importa está em [`config/bilhetes.json`](config/bilhetes.json). As
opções de topo (`match`, `janela`, `min_texto`, `sinais_venda`,
`sinais_bloqueio`) são herdadas por cada entrada de `alvos`, que redefine só o
que precisa. Para acrescentar um alvo — o URL direto do evento quando existir,
ou outro feed de notícias — basta mais uma entrada:

```json
{
  "nome": "notícias (outra pesquisa)",
  "url": "https://news.google.com/rss/search?q=FPF+bilhetes+sele%C3%A7%C3%A3o&hl=pt-PT&gl=PT&ceid=PT:pt",
  "essencial": false,
  "detetar_mudanca": false,
  "janela": 250,
  "min_texto": 40,
  "sinais_bloqueio": []
}
```
