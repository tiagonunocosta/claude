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

Push para o telefone via [ntfy.sh](https://ntfy.sh):

```bash
NTFY_TOPICO=o-meu-topico-difícil-de-adivinhar ./scripts/check_bilhetes.py
```

No `crontab -e`, três vezes por dia:

```cron
0 8,13,19 * * * cd ~/claude && NTFY_TOPICO=o-meu-topico ./scripts/check_bilhetes.py >> /tmp/bilhetes.log 2>&1
```

Só precisa de Python 3.9+. **Sem dependências** — apenas a biblioteca padrão.

## Como chega o aviso até ti

| Canal | Chega onde | Precisa de |
|---|---|---|
| **Issue no repo** | email do GitHub | nada — já está ligado |
| **Push no telefone** | app ntfy | criar o secret `NTFY_TOPICO` |
| **Webhook** | onde quiseres | criar o secret `BILHETES_WEBHOOK` |

**Testa o canal antes de confiar nele.** Corre o workflow à mão com
`forcar_alerta: true` e confirma que o email chega mesmo. Se não chegar, é
porque não estás a observar o repo: *Watch → All Activity*, no canto superior
direito da página do repositório.

Para push no telefone: instala a app [ntfy](https://ntfy.sh), subscreve um
tópico com um nome que ninguém adivinhe, e cria esse nome como secret
`NTFY_TOPICO` em *Settings → Secrets and variables → Actions*. Qualquer pessoa
que saiba o nome do tópico pode ler as notificações — por isso não uses
`bilhetes-portugal`.

O push sai em dois casos: **venda detetada** e **monitor cego**. O segundo é
deliberado — um monitor mudo é a falha que mais engana.

## Dois alvos

| Alvo | O que é | Essencial |
|---|---|---|
| **bilheteira oficial** | `bilheteira.fpf.pt` — a verdade, mas atrás da Cloudflare | sim |
| **notícias** | feed RSS do Google News, filtrado por *bilhetes + Portugal + Noruega* | não |

O feed de notícias não é decoração: a FPF **anuncia a data de abertura antes de
a venda abrir**, e a imprensa noticia-o. Na prática o feed tende a avisar
primeiro. É também o alvo mais robusto — RSS é XML puro, sem JavaScript e sem
proteção anti-bot, exatamente onde a bilheteira é mais frágil. Neste momento é
o **único** canal a funcionar em GitHub Actions.

Um feed é analisado **notícia a notícia**, e um item só conta quando traz as
duas coisas: um termo do evento (`sinais_contexto`: *noruega*, *dragão*,
*liga das nações*) **e** um termo de venda. Sem essa exigência dupla, qualquer
notícia de bilhetes do país dispara um alerta — e disparou.

Diferenças declaradas na config:

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

## O que cinco corridas reais mostraram

O script foi escrito sem acesso à bilheteira (a rede da sessão de
desenvolvimento bloqueia `fpf.pt`), validado só contra as *fixtures*. Cinco
corridas reais no GitHub Actions revelaram, cada uma, um defeito diferente —
e nenhum deles aparecia nos 44 testes que existiam na altura:

| # | O que se descobriu | Correção |
|---|---|---|
| 1 | bilheteira devolve `HTTP 403` ao runner; feed lê 10.786 bytes | segundo perfil de cabeçalhos |
| 2 | 403 passado, mas a página dá **0 caracteres** de texto | nada a fazer por HTTP simples |
| 3 | feed com 8.452 caracteres e hash = SHA-256 de `""` — zero ocorrências de "Noruega" | deixar de filtrar duas vezes |
| 4 | **falso positivo**: alertou com notícias do Famalicão, do FC Porto e dos MEGADETH | exigir evento **E** venda no mesmo item |
| 5 | `SEM_SINAL`, zero acertos, sem alerta | confirmação |

### A bilheteira não é legível por HTTP simples

`bilheteira.fpf.pt` está atrás da Cloudflare (`104.18.10.225`). O primeiro
perfil de cabeçalhos leva `403`; o segundo passa, e o que vem por trás são
**3830 bytes de HTML com 0 caracteres de texto** — aplicação JavaScript ou
interstício de desafio. Não é problema que se resolva com cabeçalhos.

### O Google News não respeita o AND das aspas

A pesquisa `bilhetes "Portugal" "Noruega"` devolveu notícias sobre o
Famalicão-Gil Vicente, a Supertaça e um concerto dos MEGADETH. **O âmbito tem
de ser imposto no código, não delegado na pesquisa** — daí os `sinais_contexto`.

Ambos os casos fazem o monitor **avisar** em vez de ficar calado (ver o
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

69 testes, sem rede: cobrem cada estado, a precedência do bloqueio sobre o botão
de compra, a deteção de mudança, a extração de texto e de RSS, os códigos de
saída, a escalada do interruptor de homem morto, a herança de opções por alvo e
quando o push sai, e a exigência dupla nas notícias — com os títulos reais que
provocaram o falso positivo da corrida #4 como teste de regressão.

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
