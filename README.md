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

## Como decide

1. Descarrega cada URL de [`config/bilhetes.json`](config/bilhetes.json) e reduz o
   HTML a texto (descartando `script`/`style`, mas apanhando rótulos em `title`,
   `alt` e `aria-label`, onde vive o texto dos botões).
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
| `A_VENDA` | sinais de compra na janela do evento | **10** |
| `ALTEROU` | a página do evento mudou desde a última corrida | **10** |
| `SEM_CONTEUDO` | página sem texto analisável (JavaScript) — **verificação não fiável** | 2 |
| `BLOQUEADO` | listado, mas "esgotado" / "em breve" | 0 |
| `SEM_SINAL` | listado, sem sinais reconhecidos | 0 |
| `NAO_LISTADO` | o evento ainda não aparece | 0 |
| `ERRO` | não foi possível ler a página | 1 |

## O limite honesto deste monitor

O script foi escrito **sem eu poder abrir a bilheteira real** (a rede da sessão
onde foi desenvolvido bloqueia `fpf.pt`), por isso os sinais de texto são um
palpite informado, validado apenas contra as *fixtures* em `tests/fixtures/`.

Se a bilheteira for uma aplicação JavaScript, o HTML inicial não traz o texto
dos eventos e o script devolve `SEM_CONTEUDO` — e diz-te isso em vez de ficar
calado a fingir que está a vigiar. **Corre-o uma vez à mão antes de confiar nele:**

```bash
./scripts/check_bilhetes.py --guardar-html /tmp/paginas
```

Se der `SEM_CONTEUDO`, o HTML guardado em `/tmp/paginas` mostra com o que
estamos a lidar, e a correção é apontar o script ao endpoint JSON que a página
usa (via `--url`) em vez do HTML.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

25 testes, sem rede: cobrem cada estado, a precedência do bloqueio sobre o botão
de compra, a deteção de mudança, a extração de texto e os códigos de saída.

## Ajustar sem tocar no código

Tudo o que importa está em [`config/bilhetes.json`](config/bilhetes.json):
`urls` (acrescenta o URL direto do evento quando existir), `match`, `janela`,
`sinais_venda` e `sinais_bloqueio`.
