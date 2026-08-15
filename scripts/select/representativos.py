#!/usr/bin/env python3
"""Seleciona N grafos DIMACS representativos por características topológicas."""

from __future__ import annotations

import argparse
import csv
import math
import random
import shutil
import warnings
from pathlib import Path

import networkx as nx
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


FEATURES = [
    "n_vertices",
    "n_arestas",
    "densidade",
    "grau_minimo",
    "grau_maximo",
    "grau_medio",
    "grau_mediana",
    "grau_desvio",
    "entropia_grau",
    "clustering_medio",
    "transitividade",
    "assortatividade",
    "num_componentes",
    "frac_maior_componente",
    "diametro_aprox",
    "caminho_medio_aprox",
]


def ler_dimacs_col(caminho: Path) -> nx.Graph:
    """Lê um grafo simples no formato DIMACS .col."""
    grafo = nx.Graph()
    with caminho.open("r", encoding="utf-8", errors="replace") as arquivo:
        for numero_linha, linha in enumerate(arquivo, start=1):
            texto = linha.strip()
            if not texto or texto.startswith("c"):
                continue
            partes = texto.split()
            try:
                if partes[0] == "p":
                    grafo.add_nodes_from(range(1, int(partes[2]) + 1))
                elif partes[0] == "e":
                    grafo.add_edge(int(partes[1]), int(partes[2]))
            except (IndexError, ValueError) as erro:
                raise ValueError(
                    f"linha DIMACS inválida em {caminho}:{numero_linha}: {texto}"
                ) from erro
    return grafo


def diametro_e_caminho_aproximados(
    grafo: nx.Graph, numero_amostras: int, seed: int
) -> tuple[float, float]:
    """Aproxima diâmetro e caminho médio por BFS na maior componente."""
    if grafo.number_of_nodes() == 0:
        return 0.0, 0.0

    maior_componente = max(nx.connected_components(grafo), key=len)
    subgrafo = grafo.subgraph(maior_componente)
    vertices = list(subgrafo)
    amostras = random.Random(seed).sample(
        vertices, min(numero_amostras, len(vertices))
    )
    excentricidades: list[int] = []
    caminhos_medios: list[float] = []
    for origem in amostras:
        distancias = list(nx.single_source_shortest_path_length(subgrafo, origem).values())
        excentricidades.append(max(distancias))
        caminhos_medios.append(sum(distancias) / len(distancias))

    return float(max(excentricidades)), float(np.mean(caminhos_medios))


def entropia_graus(graus: list[int]) -> float:
    if not graus:
        return 0.0
    _, contagens = np.unique(graus, return_counts=True)
    probabilidades = contagens / contagens.sum()
    return float(-np.sum(probabilidades * np.log2(probabilidades)))


def extrair_features(grafo: nx.Graph, bfs_samples: int, seed: int) -> dict[str, float]:
    """Extrai características estruturais topológicas do grafo."""
    n = grafo.number_of_nodes()
    m = grafo.number_of_edges()
    graus = [grau for _, grau in grafo.degree()]

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            assortatividade = nx.degree_assortativity_coefficient(grafo)
        if assortatividade is None or math.isnan(assortatividade):
            assortatividade = 0.0
    except Exception:
        assortatividade = 0.0

    componentes = list(nx.connected_components(grafo))
    diametro, caminho_medio = diametro_e_caminho_aproximados(
        grafo, bfs_samples, seed
    )
    return {
        "n_vertices": n,
        "n_arestas": m,
        "densidade": nx.density(grafo),
        "grau_minimo": min(graus, default=0),
        "grau_maximo": max(graus, default=0),
        "grau_medio": float(np.mean(graus)) if graus else 0.0,
        "grau_mediana": float(np.median(graus)) if graus else 0.0,
        "grau_desvio": float(np.std(graus)) if graus else 0.0,
        "entropia_grau": entropia_graus(graus),
        "clustering_medio": nx.average_clustering(grafo),
        "transitividade": nx.transitivity(grafo),
        "assortatividade": float(assortatividade),
        "num_componentes": len(componentes),
        "frac_maior_componente": (
            max(map(len, componentes)) / n if componentes and n else 0.0
        ),
        "diametro_aprox": diametro,
        "caminho_medio_aprox": caminho_medio,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seleciona grafos DIMACS representativos por clustering de "
            "características estruturais topológicas"
        )
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bfs-samples", type=int, default=10)
    parser.add_argument("--csv", type=Path)
    parser.add_argument(
        "--ignorar-sem-arestas",
        action="store_true",
        help=(
            "Ignora grafos com zero vértices, zero arestas ou somente "
            "vértices isolados"
        ),
    )
    parser.add_argument(
        "--usar-todos-se-faltar",
        action="store_true",
        help="Seleciona todos os disponíveis quando a base tiver menos que --n",
    )
    return parser.parse_args()


def possui_vertices_e_arestas(caminho: Path) -> bool:
    """Verifica pelo cabeçalho DIMACS se há vértices e alguma aresta."""
    with caminho.open("r", encoding="utf-8", errors="replace") as arquivo:
        for linha in arquivo:
            if linha.startswith("p "):
                partes = linha.split()
                if len(partes) < 4:
                    return False
                return int(partes[2]) > 0 and int(partes[3]) > 0
    return False


def salvar_csv(
    caminho: Path,
    linhas: list[dict],
    clusters: np.ndarray,
    selecionados: list[int],
) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(
            arquivo, fieldnames=["arquivo", "cluster", "selecionado", *FEATURES]
        )
        escritor.writeheader()
        for indice, linha in enumerate(linhas):
            escritor.writerow(
                {
                    "arquivo": linha["arquivo"],
                    "cluster": int(clusters[indice]),
                    "selecionado": indice in selecionados,
                    **{feature: linha[feature] for feature in FEATURES},
                }
            )


def main() -> None:
    args = parse_args()
    if args.n < 1:
        raise ValueError("n deve ser maior ou igual a 1")
    if args.bfs_samples < 1:
        raise ValueError("bfs-samples deve ser maior ou igual a 1")

    arquivos = sorted(args.input_dir.glob("*.col"))
    if args.ignorar_sem_arestas:
        invalidos = [caminho for caminho in arquivos if not possui_vertices_e_arestas(caminho)]
        arquivos = [caminho for caminho in arquivos if caminho not in invalidos]
        for caminho in invalidos:
            print(f"IGNORADO [sem arestas]: {caminho}")
    if not arquivos:
        raise FileNotFoundError(f"nenhum arquivo .col encontrado em {args.input_dir}")
    if args.n > len(arquivos):
        if not args.usar_todos_se_faltar:
            raise ValueError(
                f"foram solicitados {args.n} grafos, mas a base possui {len(arquivos)}"
            )
        print(
            f"AVISO: foram solicitados {args.n} grafos, mas existem somente "
            f"{len(arquivos)}; todos serão selecionados"
        )
        args.n = len(arquivos)

    print(f"Calculando características de {len(arquivos)} grafos...")
    linhas: list[dict] = []
    for indice, caminho in enumerate(arquivos, start=1):
        grafo = ler_dimacs_col(caminho)
        features = extrair_features(grafo, args.bfs_samples, args.seed)
        linhas.append({"arquivo": caminho.name, **features})
        print(
            f"[{indice}/{len(arquivos)}] {caminho.name}: "
            f"n={features['n_vertices']}, m={features['n_arestas']}, "
            f"densidade={features['densidade']:.3f}"
        )

    matriz = np.array([[linha[feature] for feature in FEATURES] for linha in linhas])
    matriz_padronizada = StandardScaler().fit_transform(matriz)
    kmeans = KMeans(n_clusters=args.n, random_state=args.seed, n_init=10)
    clusters = kmeans.fit_predict(matriz_padronizada)

    selecionados: list[int] = []
    for cluster in range(args.n):
        indices = np.where(clusters == cluster)[0]
        if not len(indices):
            continue
        pontos = matriz_padronizada[indices]
        distancias = np.linalg.norm(pontos - kmeans.cluster_centers_[cluster], axis=1)
        selecionados.append(int(indices[np.argmin(distancias)]))

    if len(selecionados) < args.n:
        restantes = [
            indice for indice in range(len(linhas)) if indice not in selecionados
        ]
        escolhidos = matriz_padronizada[selecionados]
        distancias = [
            np.min(np.linalg.norm(escolhidos - matriz_padronizada[indice], axis=1))
            for indice in restantes
        ]
        ordem = np.argsort(distancias)[::-1]
        selecionados.extend(
            restantes[indice] for indice in ordem[: args.n - len(selecionados)]
        )

    args.out.mkdir(parents=True, exist_ok=True)
    nomes_selecionados = {linhas[indice]["arquivo"] for indice in selecionados}
    for existente in args.out.glob("*.col"):
        if existente.name not in nomes_selecionados:
            existente.unlink()

    print(f"Selecionados {len(selecionados)} grafos:")
    for indice in selecionados:
        linha = linhas[indice]
        shutil.copy2(args.input_dir / linha["arquivo"], args.out / linha["arquivo"])
        print(
            f"  {linha['arquivo']}: n={linha['n_vertices']}, "
            f"m={linha['n_arestas']}, densidade={linha['densidade']:.3f}, "
            f"grau médio={linha['grau_medio']:.2f}"
        )

    if args.csv:
        salvar_csv(args.csv, linhas, clusters, selecionados)
        print(f"Características salvas em {args.csv}")
    print(f"Grafos copiados para {args.out}")


if __name__ == "__main__":
    main()
