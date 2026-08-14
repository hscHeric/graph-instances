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
python scripts/download/dowload_harwell_boeing.py
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
python scripts/download/dowload_harwell_boeing.py
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
passando o parâmetro depois de `--`. Ao executar os scripts diretamente, os
parâmetros continuam obrigatórios.

### Erdős-Rényi

Cada par de vértices recebe uma aresta com probabilidade `p`. É um modelo
simples para produzir grafos com densidades diferentes.

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
python scripts/generate/cubicos.py --n 100 200 400 --reps 5 --seed 42 --out datasets/cubicos/processed
```

Também é possível usar uma progressão geométrica:

```bash
# Com mise
mise run generate::cubic -- --doubling 100 2 5 --reps 5 --seed 42 --out datasets/cubicos/processed

# Sem mise
python scripts/generate/cubicos.py --doubling 100 2 5 --reps 5 --seed 42 --out datasets/cubicos/processed
```

Para exibir a ajuda de qualquer gerador:

```bash
# Com mise
mise run generate::erdos_renyi -- --help
mise run generate::barabasi_albert -- --help
mise run generate::watts_strogatz -- --help
mise run generate::cubic -- --help

# Sem mise
python scripts/generate/erdos_renyi.py
python scripts/generate/barabasi_albert.py
python scripts/generate/watts_strogatz.py
python scripts/generate/cubicos.py
```

### Parâmetros dos geradores

Parâmetros comuns a todos os geradores:

| Parâmetro | Significado |
| --- | --- |
| `--n N [N ...]` | Um ou mais números de vértices. |
| `--reps REPETIÇÕES` | Quantidade de grafos gerados para cada combinação. |
| `--seed SEED` | Seed inicial; é incrementada após cada grafo. |
| `--out PASTA` | Pasta em que os arquivos `.col` serão salvos. |
| `-h`, `--help` | Mostra a ajuda e não gera arquivos. |

Parâmetros específicos:

| Gerador | Parâmetro | Significado |
| --- | --- | --- |
| Erdős-Rényi | `--p P [P ...]` | Probabilidade de existir uma aresta entre cada par de vértices. |
| Barabási-Albert | `--m M [M ...]` | Arestas criadas por cada novo vértice. Deve ser positivo e menor que `n`. |
| Watts-Strogatz | `--k K [K ...]` | Vizinhos de cada vértice no anel inicial. Deve ser positivo e menor que `n`. |
| Watts-Strogatz | `--p-rewire P [P ...]` | Probabilidade de reconexão de cada aresta, entre 0 e 1. |
| Cúbicos | `--doubling INÍCIO FATOR PASSOS` | Gera tamanhos em progressão geométrica. Deve ser usado no lugar de `--n`. |

Todos esses parâmetros são obrigatórios ao executar os scripts diretamente.
As tasks do mise já fornecem os valores predefinidos. No caso dos cúbicos,
deve-se informar exatamente um entre `--n` e `--doubling` ao executar o script.

### Gerar todos com a configuração predefinida

```bash
mise run generate::all
```

Ela usa seed 42. Para Erdős-Rényi, combina `n = 100 200 400 800 1600` com
`p = 0.02 0.05 0.1 0.2 0.35` e gera duas repetições. Para os cúbicos, gera
seis grafos para cada tamanho em `n = 100 200 400 800 1600`. Para
Barabási-Albert, combina esses mesmos tamanhos com `m = 1 2 3 5 8` e gera
duas repetições. Para Watts-Strogatz, combina os mesmos tamanhos com
`k = 4 6 8 12` e `p-rewire = 0.01 0.1 0.3 0.5`, com uma repetição. As saídas
ficam nas respectivas pastas `datasets/<modelo>/processed/`.

## Formato DIMACS `.col`

Cada arquivo contém comentários opcionais, uma linha com o número de vértices
e arestas e uma linha para cada aresta:

```text
c descrição do grafo
p edge 3 2
e 1 2
e 2 3
```
