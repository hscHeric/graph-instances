/*
 * Camada de propriedades mutaveis para graph.
 *
 * A topologia permanece no grafo base. Esta estrutura guarda apenas uma
 * referencia para ele e os vetores de propriedades registrados pelo
 * chamador. Uma propriedade pode ter um elemento por vertice ou por
 * posicao no vetor de adjacencia.
 *
 * Clonar um grafo mutavel copia as propriedades, mas continua usando a
 * mesma topologia. O chamador deve manter o grafo base vivo enquanto
 * houver algum mutable_graph que o referencie.
 *
 * Uso tipico:
 *
 *   graph *base = graph_load_dimacs("instancia.col");
 *   mutable_graph *mg = mgraph_create(base);
 *
 *   int label_h  = mgraph_add_vertex_property(mg, sizeof(int32_t));
 *   int count_h  = mgraph_add_vertex_property(mg, sizeof(int32_t));
 *
 *   int32_t *label = (int32_t *)mgraph_vertex_property(mg, label_h);
 *   int32_t *count = (int32_t *)mgraph_vertex_property(mg, count_h);
 *
 *   for (long it = 0; it < 10000000; it++) {
 *       uint32_t v = escolher_vertice(...);
 *       label[v] = novo_rotulo(...);
 *       count[v]++;
 *   }
 *
 *   mutable_graph *melhor = mgraph_clone(mg);
 *   ...
 *   mgraph_free(mg);
 *   mgraph_free(melhor);
 *   graph_free(base);
 */
#ifndef MUTABLE_GRAPH_H
#define MUTABLE_GRAPH_H

#include <stddef.h>
#include <stdint.h>

#include "graph.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum property_kind {
  MGRAPH_PROP_VERTEX, /* um elemento por vertice (tamanho n) */
  MGRAPH_PROP_EDGE    /* um elemento por posicao de adjacencia (tamanho 2*m) */
} property_kind;

typedef struct property_array {
  void *data;
  size_t element_size;
  size_t count; /* n para vertice, 2*m para aresta */
  property_kind kind;
} property_array;

typedef struct mutable_graph {
  const graph *base;

  property_array *properties;
  int properties_count;
  int properties_capacity;
} mutable_graph;

/* Cria uma camada mutavel sobre base. A propriedade de base continua com
 * o chamador. */
mutable_graph *mgraph_create(const graph *base);

/* Adiciona uma propriedade por vertice. O vetor comeca zerado. Retorna o
 * identificador da propriedade ou -1 em caso de erro. */
int mgraph_add_vertex_property(mutable_graph *mg, size_t element_size);

/* Adiciona uma propriedade por posicao no vetor de adjacencia. Cada aresta
 * nao direcionada ocupa duas posicoes. */
int mgraph_add_edge_property(mutable_graph *mg, size_t element_size);

/* Retorna o vetor associado ao identificador informado. */
static inline void *mgraph_vertex_property(const mutable_graph *mg, int handle) {
  return mg->properties[handle].data;
}

static inline void *mgraph_edge_property(const mutable_graph *mg, int handle) {
  return mg->properties[handle].data;
}

/* Zera todos os elementos de uma propriedade. */
void mgraph_reset_property(mutable_graph *mg, int handle);

/* Retorna a posicao da propriedade associada ao i-esimo vizinho de v. */
static inline size_t mgraph_edge_slot(const mutable_graph *mg, uint32_t v,
                                      uint32_t neighbor_index) {
  return (size_t)mg->base->offset[v] + neighbor_index;
}

/* Clona as propriedades e compartilha o mesmo grafo base. */
mutable_graph *mgraph_clone(const mutable_graph *mg);

/* Libera as propriedades e a camada mutavel. O grafo base nao e liberado. */
void mgraph_free(mutable_graph *mg);

/* Atalhos para consultar a topologia do grafo base. */

static inline uint32_t mgraph_num_vertices(const mutable_graph *mg) {
  return graph_num_vertices(mg->base);
}

static inline uint64_t mgraph_num_edges(const mutable_graph *mg) {
  return graph_num_edges(mg->base);
}

static inline uint32_t mgraph_degree(const mutable_graph *mg, uint32_t v) {
  return graph_degree(mg->base, v);
}

static inline const uint32_t *
mgraph_neighbors(const mutable_graph *mg, uint32_t v, uint32_t *out_degree) {
  return graph_neighbors(mg->base, v, out_degree);
}

static inline int mgraph_has_edge(const mutable_graph *mg, uint32_t u,
                                  uint32_t v) {
  return graph_has_edge(mg->base, u, v);
}

#ifdef __cplusplus
}
#endif

#endif /* MUTABLE_GRAPH_H */
