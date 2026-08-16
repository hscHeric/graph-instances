# graph-instances

Coleção de scripts para baixar, converter e gerar instâncias de grafos no
formato [DIMACS `.col`](https://mat.tepper.cmu.edu/COLOR/instances.html).

Os arquivos baixados ficam em `datasets/<base>/raw/`. Quando uma conversão é
necessária, os arquivos `.col` são salvos em `datasets/<base>/processed/`.
O diretório `datasets/` não é enviado ao Git.

## Preparação

### Com mise

O mise cria e ativa o ambiente virtual configurado em `mise.toml` e define
`PROJECT_ROOT` com o caminho do projeto:

```bash
mise install
pip install requests ssgetpy scipy networkx --break-system-packages
```

### Sem mise

Crie um ambiente virtual, instale as dependências e defina a raiz do projeto:

```bash
python -m venv .venv
source .venv/bin/activate
pip install requests ssgetpy scipy networkx
export PROJECT_ROOT="$(pwd)"
```

Os comandos Python abaixo devem ser executados na raiz do repositório.

## Bases baixadas

### Harwell-Boeing

Coleção clássica de matrizes esparsas obtida pelo SuiteSparse. As matrizes
quadradas e simétricas podem ser interpretadas como grafos não direcionados.
O projeto baixa os arquivos Matrix Market (`.mtx`) e os converte para `.col`.

```bash
# Com mise
mise run download::harwell_boeing
mise run convert::harwell_boeing

# Sem mise
python scripts/download/download_harwell_boeing.py
python scripts/convert/matrix_market.py datasets/harwell_boeing/raw
```

### DIMACS

Instâncias usadas em desafios de coloração e clique máximo. Os arquivos são
baixados do Network Repository em Matrix Market e convertidos para `.col`.

```bash
# Com mise
mise run download::dimacs
mise run convert::dimacs

# Sem mise
python scripts/download/download_dimacs.py
python scripts/convert/matrix_market.py datasets/dimacs/raw
```

### BHOSLIB

Grafos densos e difíceis, criados para testar algoritmos de clique máximo,
conjunto independente, cobertura de vértices e coloração.

```bash
# Com mise
mise run download::bhoslib
mise run convert::bhoslib

# Sem mise
python scripts/download/download_bhoslib.py
python scripts/convert/matrix_market.py datasets/bhoslib/raw
```

### SNAP

Redes reais da Stanford Large Network Dataset Collection. Inclui redes
sociais, citações, colaboração, páginas web, estradas, sistemas autônomos e
redes temporais. Os arquivos originais são listas de arestas `.txt` ou `.csv`.

```bash
# Com mise
mise run download::snap
mise run convert::snap

# Sem mise
python scripts/download/download_snap.py
python scripts/convert/snap.py
```

O download comum não inclui o `com-Friendster`, pois essa instância é muito
maior que as demais. Para baixar a coleção incluindo o Friendster:

```bash
# Com mise
mise run download::snap::full

# Sem mise
python scripts/download/download_snap.py --incluir-friendster
```

O conversor SNAP aceita listas de arestas `.txt`, `.csv`, `.tsv`, `.edge`,
`.edges` e `.edgelist`, além dos formatos especiais `wiki-RfA`, MOOC e matrizes temporais
do `comm-f2f-Resistance`. A saída fica em `datasets/snap/processed/`.

Como DIMACS `.col` representa grafos simples não direcionados, a conversão:

- ignora direção, peso, sinal e timestamp;
- remove laços e arestas duplicadas;
- renumera os vértices para o intervalo `1..n`;
- ignora atributos, labels, features, scripts, readmes e metadados;
- preserva todos os arquivos originais em `raw/`.

Para conferir o que será mantido ou ignorado sem converter:

```bash
# Com mise
mise run convert::snap -- --analisar

# Sem mise
python scripts/convert/snap.py --analisar
```

A task normal `convert::snap` também mostra essa classificação antes de
iniciar a conversão completa.

Para baixar ou converter todas as bases compatíveis:

```bash
# Com mise
mise run download::all
mise run convert::all

# Sem mise: execute os downloaders desejados e depois o conversor geral
python scripts/download/download_bhoslib.py
python scripts/download/download_dimacs.py
python scripts/download/download_harwell_boeing.py
python scripts/download/download_snap.py
python scripts/convert/matrix_market.py
python scripts/convert/snap.py
```

### Parâmetros dos downloads

Os downloaders não exigem argumentos posicionais. Todos usam `PROJECT_ROOT`
para localizar `datasets/` e permitem configurar o paralelismo por variável
de ambiente:

| Base | Variável | Padrão | Exemplo |
| --- | --- | ---: | --- |
| Harwell-Boeing | `HARWELL_BOEING_DOWNLOAD_WORKERS` | 15 | `HARWELL_BOEING_DOWNLOAD_WORKERS=8 mise run download::harwell_boeing` |
| DIMACS | `DIMACS_DOWNLOAD_WORKERS` | 4 | `DIMACS_DOWNLOAD_WORKERS=8 mise run download::dimacs` |
| BHOSLIB | `BHOSLIB_DOWNLOAD_WORKERS` | 36 | `BHOSLIB_DOWNLOAD_WORKERS=12 mise run download::bhoslib` |
| SNAP | `SNAP_DOWNLOAD_WORKERS` | 99 (100 com Friendster) | `SNAP_DOWNLOAD_WORKERS=20 mise run download::snap` |

Sem mise, a variável é usada da mesma forma:

```bash
SNAP_DOWNLOAD_WORKERS=20 python scripts/download/download_snap.py
```

O conversor recebe opcionalmente um arquivo `.mtx` ou uma pasta. Sem esse
argumento, procura arquivos em todas as pastas `datasets/*/raw/`:

```bash
python scripts/convert/matrix_market.py caminho/grafo.mtx
python scripts/convert/matrix_market.py caminho/pasta
python scripts/convert/matrix_market.py
```

## Grafos sintéticos

As tasks do mise possuem configurações predefinidas e salvam os resultados em
`datasets/<modelo>/processed/`. É possível substituir qualquer configuração
passando o parâmetro depois de `--`. Os scripts também possuem valores padrão
quando executados diretamente.

### Erdős-Rényi

Cada par de vértices recebe uma aresta com probabilidade `p`. Por padrão, o
script recebe um grau médio alvo e calcula `p = grau/(n-1)`, mantendo grafos
comparavelmente esparsos conforme o tamanho cresce. `--p` permite usar uma
probabilidade fixa.

```bash
# Com mise
mise run generate::erdos_renyi -- --n 100 250 500 --p 0.05 0.1 --reps 3 --seed 42 --out datasets/erdos_renyi/processed

# Sem mise
python scripts/generate/erdos_renyi.py --n 100 250 500 --p 0.05 0.1 --reps 3 --seed 42 --out datasets/erdos_renyi/processed
```

### Barabási-Albert

Modelo livre de escala baseado em ligação preferencial. Novos vértices tendem
a se conectar aos vértices que já possuem grau alto, formando hubs.

```bash
# Com mise
mise run generate::barabasi_albert -- --n 100 250 500 --m 2 4 --reps 3 --seed 42 --out datasets/barabasi_albert/processed

# Sem mise
python scripts/generate/barabasi_albert.py --n 100 250 500 --m 2 4 --reps 3 --seed 42 --out datasets/barabasi_albert/processed
```

### Watts-Strogatz

Modelo de mundo pequeno. Parte de um anel regular e reconecta as arestas com
probabilidade `p-rewire`, combinando agrupamento local e caminhos curtos.

```bash
# Com mise
mise run generate::watts_strogatz -- --n 100 250 500 --k 4 8 --p-rewire 0.1 --reps 3 --seed 42 --out datasets/watts_strogatz/processed

# Sem mise
python scripts/generate/watts_strogatz.py --n 100 250 500 --k 4 8 --p-rewire 0.1 --reps 3 --seed 42 --out datasets/watts_strogatz/processed
```

### Grafos cúbicos

Grafos aleatórios 3-regulares: todos os vértices têm grau exatamente 3. O
número de vértices deve ser par e maior que 3.

```bash
# Com mise
mise run generate::cubic -- --n 100 200 400 --reps 5 --seed 42 --out datasets/cubicos/processed

# Sem mise
python scripts/generate/cubic.py --n 100 200 400 --reps 5 --seed 42 --out datasets/cubicos/processed
```

Também é possível usar uma progressão geométrica:

```bash
# Com mise
mise run generate::cubic -- --doubling 100 2 5 --reps 5 --seed 42 --out datasets/cubicos/processed

# Sem mise
python scripts/generate/cubic.py --doubling 100 2 5 --reps 5 --seed 42 --out datasets/cubicos/processed
```

Para exibir a ajuda de qualquer gerador:

```bash
# Com mise
mise run generate::erdos_renyi -- --help
mise run generate::barabasi_albert -- --help
mise run generate::watts_strogatz -- --help
mise run generate::cubic -- --help

# Sem mise
python scripts/generate/erdos_renyi.py --help
python scripts/generate/barabasi_albert.py --help
python scripts/generate/watts_strogatz.py --help
python scripts/generate/cubic.py --help
```

### Parâmetros dos geradores

Parâmetros comuns a todos os geradores:

| Parâmetro | Significado |
| --- | --- |
| `--n N [N ...]` | Um ou mais números de vértices. |
| `--doubling INÍCIO FATOR PASSOS` | Progressão geométrica usada quando `--n` não é informado. |
| `--reps REPETIÇÕES` | Quantidade de grafos gerados para cada combinação. |
| `--seed SEED` | Seed inicial; é incrementada após cada grafo. |
| `--out PASTA` | Pasta em que os arquivos `.col` serão salvos. |
| `-h`, `--help` | Mostra a ajuda e não gera arquivos. |

Parâmetros específicos:

| Gerador | Parâmetro | Significado |
| --- | --- | --- |
| Erdős-Rényi | `--avg-degree G [G ...]` | Grau médio alvo; calcula `p = G/(n-1)`. |
| Erdős-Rényi | `--p P [P ...]` | Probabilidade de existir uma aresta entre cada par de vértices. |
| Barabási-Albert | `--m M [M ...]` | Arestas criadas por cada novo vértice. Deve ser positivo e menor que `n`. |
| Watts-Strogatz | `--k K [K ...]` | Vizinhos de cada vértice no anel inicial. Deve ser positivo e menor que `n`. |
| Watts-Strogatz | `--p-rewire P [P ...]` | Probabilidade de reconexão de cada aresta, entre 0 e 1. |
| Cúbicos | `--n N [N ...]` | Os valores devem ser pares e maiores que 3. |

Quando `--n` é informado, ele substitui a progressão de `--doubling`.

### Gerar todos com a configuração predefinida

```bash
mise run generate::all
```

| Script | Configuração predefinida | Resultado |
| --- | --- | --- |
| `cubic.py` | `--doubling 200 1.15 30 --reps 1` | 30 grafos, de 200 a 11.516 vértices |
| `erdos_renyi.py` | `--doubling 200 1.15 30 --avg-degree 10.0 --reps 1` | 30 grafos, com grau médio próximo de 10 |
| `barabasi_albert.py` | `--doubling 200 1.15 30 --m 4 --reps 1` | 30 grafos, com grau médio próximo de 8 |
| `watts_strogatz.py` | `--doubling 200 1.15 30 --k 6 --p-rewire 0.1 --reps 1` | 30 grafos |

Todos usam seed 42 e salvam nas respectivas pastas
`datasets/<modelo>/processed/`.

## Seleção representativa

O seletor agrupa os grafos pelas características estruturais e escolhe o
grafo mais próximo do centroide de cada cluster. Cada task possui uma
quantidade predefinida:

São usadas 16 características topológicas: tamanho, densidade, estatísticas
e entropia dos graus, clustering, transitividade, assortatividade,
componentes conexas e estimativas de diâmetro e caminho médio. Nenhuma
heurística específica de dominação participa da seleção.

Durante o cálculo, cada grafo roda em um subprocesso isolado com limite de
memória e timeout. As características são persistidas imediatamente em
`datasets/<base>/features.sqlite3`; uma nova execução retoma os arquivos
pendentes. Crash, OOM ou timeout de um grafo não derrubam o processo principal.
Ao final, somente a pequena matriz de características é carregada pelo k-means.
Os cálculos usam precisão `float64`; no CSV e nas mensagens, características
contínuas são apresentadas com seis casas decimais.

```bash
mise run select::snap                 # 20
mise run select::bhoslib              # 30
mise run select::harwell_boeing       # 50
mise run select::dimacs               # 80
mise run select::erdos_renyi          # 30
mise run select::barabasi_albert      # 30
mise run select::watts_strogatz       # 30
mise run select::cubic                # 30
mise run select::all
```

Para substituir a quantidade predefinida, passe outro valor depois de `--`,
por exemplo: `mise run select::snap -- --n 10`.

Cada task lê `datasets/<base>/processed/` e substitui os arquivos `.col` de
`datasets/<base>/selected/` pela nova seleção. Sem mise:

```bash
python scripts/select/representative_selection.py \
  --input-dir datasets/snap/processed \
  --n 30 \
  --out datasets/snap/selected
```

Parâmetros adicionais:

| Parâmetro | Significado |
| --- | --- |
| `--seed SEED` | Seed do k-means e da amostragem BFS; padrão 42. |
| `--bfs-samples N` | Origens de BFS para aproximar diâmetro e caminho médio; padrão 10. |
| `--csv ARQUIVO` | Salva as características, clusters e indicação dos selecionados. |
| `--db ARQUIVO` | Usa um caminho explícito para o banco persistente. |
| `--db-name BASE` | Salva o banco em `datasets/BASE/features.sqlite3`. |
| `--max-vertices N` | Pula grafos maiores que o limite; padrão 500.000, zero desativa. |
| `--memory-limit-mb MB` | Memória máxima por subprocesso; padrão 4.096 MB. |
| `--timeout-s SEGUNDOS` | Tempo máximo por grafo; padrão 300, zero desativa. |
| `--only-select` | Não calcula features; seleciona usando somente o banco atual. |
| `--ignorar-sem-arestas` | Ignora grafos vazios ou formados somente por vértices isolados. |
| `--usar-todos-se-faltar` | Usa todos os disponíveis quando a base possuir menos arquivos que `--n`. |

O cálculo de clustering e transitividade pode consumir bastante memória e
tempo em grafos com milhões de vértices. Reduzir `--bfs-samples` diminui
somente o custo das estimativas por BFS.

A seleção SNAP habilita automaticamente `--ignorar-sem-arestas`. Atualmente,
a base DIMACS possui 78 arquivos processados; sua task avisará sobre a falta
de duas instâncias e selecionará todos os 78. Quando existirem pelo menos 80,
selecionará exatamente 80.

Dependências:

```bash
python -m pip install -r requirements.txt --break-system-packages
```

## Formato DIMACS `.col`

Cada arquivo contém comentários opcionais, uma linha com o número de vértices
e arestas e uma linha para cada aresta:

```text
c descrição do grafo
p edge 3 2
e 1 2
e 2 3
```
