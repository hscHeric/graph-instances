/*
 * mutable_graph.c -- implementacao de mutable_graph.h.
 *
 * Nada aqui depende do problema que voce esta resolvendo -- so gerencia
 * buffers tipados por tamanho de elemento (bytes), anexados aos vertices
 * ou as posicoes de adjacencia de um grafo estatico.
 */
#include "mutable_graph.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__GNUC__)
#define UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define UNLIKELY(x) (x)
#endif

#define INITIAL_PROPERTIES_CAPACITY 8

mutable_graph *mgraph_create(const graph *base) {
  if (UNLIKELY(!base)) {
    fprintf(stderr, "mgraph_create: base nao pode ser NULL\n");
    return NULL;
  }

  mutable_graph *mg = calloc(1, sizeof(*mg));
  if (UNLIKELY(!mg)) {
    fprintf(stderr, "mgraph_create: falha de alocacao\n");
    return NULL;
  }

  mg->base = base;
  mg->properties_capacity = INITIAL_PROPERTIES_CAPACITY;
  mg->properties = calloc((size_t)mg->properties_capacity,
                          sizeof(*mg->properties));
  if (UNLIKELY(!mg->properties)) {
    fprintf(stderr, "mgraph_create: falha de alocacao\n");
    free(mg);
    return NULL;
  }
  mg->properties_count = 0;

  return mg;
}

static int mgraph_add_property(mutable_graph *mg, size_t element_size,
                               property_kind kind) {
  if (UNLIKELY(element_size == 0)) {
    fprintf(stderr, "mgraph_add_property: element_size nao pode ser 0\n");
    return -1;
  }

  if (mg->properties_count == mg->properties_capacity) {
    int new_capacity = mg->properties_capacity * 2;
    property_array *grown = realloc(
        mg->properties, (size_t)new_capacity * sizeof(*mg->properties));
    if (UNLIKELY(!grown)) {
      fprintf(stderr, "mgraph_add_property: falha de alocacao\n");
      return -1;
    }
    mg->properties = grown;
    mg->properties_capacity = new_capacity;
  }

  size_t count = (kind == MGRAPH_PROP_VERTEX) ? (size_t)mg->base->n
                                              : (size_t)(2 * mg->base->m);

  void *data = calloc(count > 0 ? count : 1, element_size);
  if (UNLIKELY(!data)) {
    fprintf(
        stderr,
        "mgraph_add_property: falha de alocacao (%zu elementos de %zu bytes)\n",
        count, element_size);
    return -1;
  }

  int handle = mg->properties_count;
  mg->properties[handle].data = data;
  mg->properties[handle].element_size = element_size;
  mg->properties[handle].count = count;
  mg->properties[handle].kind = kind;
  mg->properties_count++;

  return handle;
}

int mgraph_add_vertex_property(mutable_graph *mg, size_t element_size) {
  return mgraph_add_property(mg, element_size, MGRAPH_PROP_VERTEX);
}

int mgraph_add_edge_property(mutable_graph *mg, size_t element_size) {
  return mgraph_add_property(mg, element_size, MGRAPH_PROP_EDGE);
}

void mgraph_reset_property(mutable_graph *mg, int handle) {
  property_array *prop = &mg->properties[handle];
  memset(prop->data, 0, prop->count * prop->element_size);
}

mutable_graph *mgraph_clone(const mutable_graph *mg) {
  mutable_graph *clone = calloc(1, sizeof(*clone));
  if (UNLIKELY(!clone)) {
    fprintf(stderr, "mgraph_clone: falha de alocacao\n");
    return NULL;
  }

  clone->base = mg->base; /* topologia compartilhada, nao copiada */
  clone->properties_capacity = mg->properties_capacity;
  clone->properties = calloc((size_t)clone->properties_capacity,
                             sizeof(*clone->properties));
  if (UNLIKELY(!clone->properties)) {
    fprintf(stderr, "mgraph_clone: falha de alocacao\n");
    free(clone);
    return NULL;
  }

  for (int i = 0; i < mg->properties_count; i++) {
    const property_array *src = &mg->properties[i];
    size_t bytes = src->count * src->element_size;
    void *data_copy = malloc(bytes > 0 ? bytes : 1);
    if (UNLIKELY(!data_copy)) {
      fprintf(stderr,
              "mgraph_clone: falha de alocacao ao copiar propriedade %d\n", i);
      /* libera o que ja foi clonado antes de desistir */
      for (int j = 0; j < i; j++) {
        free(clone->properties[j].data);
      }
      free(clone->properties);
      free(clone);
      return NULL;
    }
    memcpy(data_copy, src->data, bytes);

    clone->properties[i].data = data_copy;
    clone->properties[i].element_size = src->element_size;
    clone->properties[i].count = src->count;
    clone->properties[i].kind = src->kind;
  }
  clone->properties_count = mg->properties_count;

  return clone;
}

void mgraph_free(mutable_graph *mg) {
  if (!mg) {
    return;
  }
  for (int i = 0; i < mg->properties_count; i++) {
    free(mg->properties[i].data);
  }
  free(mg->properties);
  free(mg);
  /* mg->base propositalmente NAO liberado: e' compartilhado */
}
