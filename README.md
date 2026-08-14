# graph-instances

Coleção de scripts para baixar, converter e gerar instâncias de grafos no
formato [DIMACS `.col`](https://mat.tepper.cmu.edu/COLOR/instances.html).

Os arquivos baixados ficam em `datasets/<base>/raw/`. Quando uma conversão é
necessária, os arquivos `.col` são salvos em `datasets/<base>/processed/`.
O diretório `datasets/` não é enviado ao Git.

## Bases baixadas

### Harwell-Boeing

Coleção clássica de matrizes esparsas obtida pelo SuiteSparse. As matrizes
quadradas e simétricas podem ser interpretadas como grafos não direcionados.
O projeto baixa os arquivos Matrix Market (`.mtx`) e os converte para `.col`.

```bash
mise run download::harwell_boeing
mise run convert::harwell_boeing
```

### DIMACS

Instâncias usadas em desafios de coloração e clique máximo. Os arquivos são
baixados do Network Repository em Matrix Market e convertidos para `.col`.

```bash
mise run download::dimacs
mise run convert::dimacs
```

### BHOSLIB

Grafos densos e difíceis, criados para testar algoritmos de clique máximo,
conjunto independente, cobertura de vértices e coloração.

```bash
mise run download::bhoslib
mise run convert::bhoslib
```

### SNAP

Redes reais da Stanford Large Network Dataset Collection. Inclui redes
sociais, citações, colaboração, páginas web, estradas, sistemas autônomos e
redes temporais. Os arquivos originais são listas de arestas `.txt` ou `.csv`.

```bash
mise run download::snap
```

O conversor Matrix Market não processa os arquivos SNAP, pois eles não usam o
formato `.mtx`.

Para baixar ou converter todas as bases compatíveis:

```bash
mise run download::all
mise run convert::all
```

## Grafos sintéticos

Os geradores exigem parâmetros. Quando uma task é executada sem argumentos,
ela apenas mostra a ajuda do comando.

### Erdős-Rényi

Cada par de vértices recebe uma aresta com probabilidade `p`. É um modelo
simples para produzir grafos com densidades diferentes.

```bash
mise run generate::erdos_renyi -- --n 100 250 500 --p 0.05 0.1 --reps 3
```

### Barabási-Albert

Modelo livre de escala baseado em ligação preferencial. Novos vértices tendem
a se conectar aos vértices que já possuem grau alto, formando hubs.

```bash
mise run generate::barabasi_albert -- --n 100 250 500 --m 2 4 --reps 3
```

### Watts-Strogatz

Modelo de mundo pequeno. Parte de um anel regular e reconecta as arestas com
probabilidade `p-rewire`, combinando agrupamento local e caminhos curtos.

```bash
mise run generate::watts_strogatz -- --n 100 250 500 --k 4 8 --p-rewire 0.1 --reps 3
```

### Grafos cúbicos

Grafos aleatórios 3-regulares: todos os vértices têm grau exatamente 3. O
número de vértices deve ser par e maior que 3.

```bash
mise run generate::cubicos -- --n 100 200 400 --reps 5
```

Também é possível usar uma progressão geométrica:

```bash
mise run generate::cubicos -- --doubling 100 2 5 --reps 5
```

Para exibir a ajuda de qualquer gerador:

```bash
mise run generate::erdos_renyi
mise run generate::barabasi_albert
mise run generate::watts_strogatz
mise run generate::cubicos
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
