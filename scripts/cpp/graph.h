/*
 * graph.h representacao de grafo estatico, somente-leitura, em CSR
 * (Compressed Sparse Row).
 *
 * Pensada para: carregar uma vez, nunca modificar depois, e ser lida
 * repetidamente por uma metaheuristica (muitas iteracoes sobre o mesmo
 * grafo). Por isso prioriza:
 *   - compacidade (poucos bytes por vertice/aresta, cabe mais em cache)
 *   - localidade (vizinhos de um vertice ficam contiguos na memoria)
 *   - carregamento rapido em execucoes repetidas (formato binario + mmap)
 *
 * Uso tipico:
 *   Graph *g = graph_load_dimacs("instancia.col");
 *   uint32_t degree;
 *   const uint32_t *nbrs = graph_neighbors(g, v, &degree);
 *   for (uint32_t i = 0; i < degree; i++) { ... nbrs[i] ... }
 *   graph_free(g);
 *
 * Para leituras repetidas do MESMO grafo (ex: rodar a metaheuristica varias
 * vezes na mesma instancia), converta uma vez para o formato binario e
 * recarregue por mmap dai em diante -- e ordens de grandeza mais rapido
 * que reparsear o .col toda vez:
 *
 *   Graph *g = graph_load_dimacs("instancia.col");
 *   graph_save_binary(g, "instancia.bin");
 *   graph_free(g);
 *   ...
 *   Graph *g2 = graph_load_binary("instancia.bin");  // quase instantaneo
 */
#ifndef GRAPH_H
#define GRAPH_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  uint32_t n; /* numero de vertices (0..n-1) */
  uint64_t m; /* numero de arestas (grafo nao-direcionado) */

  /* CSR: vizinhos do vertice v estao em adjacency[offset[v] .. offset[v+1]-1],
   * ordenados de forma crescente (permite busca binaria). */
  uint32_t *offset;    /* tamanho n+1 */
  uint32_t *adjacency; /* tamanho 2*m */

  /* Uso interno: != NULL quando os arrays acima apontam para uma regiao
   * mapeada via mmap (graph_load_binary). Nesse caso offset/adjacency NAO
   * devem ser liberados com free() individualmente -- graph_free() trata
   * isso automaticamente, o campo so existe para o caso o usuario
   * inspecione a struct diretamente. */
  void *mmap_base;
  size_t mmap_len;
} Graph;

/* Carrega um grafo a partir de um arquivo texto no formato DIMACS .col
 * ("p edge n m" + linhas "e u v", vertices 1-indexados no arquivo,
 * convertidos para 0-indexados na struct). Retorna NULL em caso de erro
 * (arquivo nao encontrado, formato invalido, falha de alocacao) -- nesse
 * caso uma mensagem e' impressa em stderr. */
Graph *graph_load_dimacs(const char *path);

/* Salva um grafo ja carregado no formato binario proprio (rapido de
 * recarregar via graph_load_binary). Retorna 0 em sucesso, -1 em erro. */
int graph_save_binary(const Graph *g, const char *path);

/* Carrega um grafo do formato binario proprio via mmap (leitura
 * essencialmente instantanea, sem parsing). Retorna NULL em caso de erro. */
Graph *graph_load_binary(const char *path);

/* Libera toda a memoria associada ao grafo (malloc ou mmap, conforme como
 * foi carregado). Seguro chamar com g == NULL. */
void graph_free(Graph *g);

/*Acesso: todas inline/estaticas para zero overhead de chamada em loops quentes
 * da metaheuristica.*/

static inline uint32_t graph_num_vertices(const Graph *g) { return g->n; }

static inline uint64_t graph_num_edges(const Graph *g) { return g->m; }

static inline uint32_t graph_degree(const Graph *g, uint32_t v) {
  return g->offset[v + 1] - g->offset[v];
}

/* Retorna ponteiro para o inicio da lista de vizinhos de v (ordenada
 * crescente) e escreve o grau em *out_degree. O ponteiro retornado e'
 * valido enquanto o grafo nao for liberado -- nao precisa (nem deve) ser
 * liberado separadamente. */
static inline const uint32_t *graph_neighbors(const Graph *g, uint32_t v,
                                              uint32_t *out_degree) {
  uint32_t start = g->offset[v];
  *out_degree = g->offset[v + 1] - start;
  return &g->adjacency[start];
}

/* Verifica se a aresta (u, v) existe, via busca binaria na lista de
 * vizinhos do vertice de menor grau (O(log grau)). */
int graph_has_edge(const Graph *g, uint32_t u, uint32_t v);

#ifdef __cplusplus
}
#endif

#endif /* GRAPH_H */
