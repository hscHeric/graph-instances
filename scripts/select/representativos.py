#!/usr/bin/env python3
"""Seleciona N grafos DIMACS representativos por características topológicas."""

from __future__ import annotations

import argparse
import csv
import gc
import math
import random
import shutil
import sqlite3
import tempfile
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
    rng = random.Random(seed)
    amostras: list[int] = []
    for indice, vertice in enumerate(subgrafo):
        if indice < numero_amostras:
            amostras.append(vertice)
            continue
        substituto = rng.randrange(indice + 1)
        if substituto < numero_amostras:
            amostras[substituto] = vertice

    excentricidades: list[int] = []
    caminhos_medios: list[float] = []
    for origem in amostras:
        maior_distancia = 0
        soma_distancias = 0
        quantidade = 0
        for distancia in nx.single_source_shortest_path_length(subgrafo, origem).values():
            maior_distancia = max(maior_distancia, distancia)
            soma_distancias += distancia
            quantidade += 1
        excentricidades.append(maior_distancia)
        caminhos_medios.append(soma_distancias / quantidade)

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

    numero_componentes = 0
    tamanho_maior_componente = 0
    for componente in nx.connected_components(grafo):
        numero_componentes += 1
        tamanho_maior_componente = max(tamanho_maior_componente, len(componente))
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
        "num_componentes": numero_componentes,
        "frac_maior_componente": tamanho_maior_componente / n if n else 0.0,
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
    nomes: list[str],
    matriz: np.ndarray,
    clusters: np.ndarray,
    selecionados: list[int],
) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(
            arquivo, fieldnames=["arquivo", "cluster", "selecionado", *FEATURES]
        )
        escritor.writeheader()
        for indice, nome in enumerate(nomes):
            escritor.writerow(
                {
                    "arquivo": nome,
                    "cluster": int(clusters[indice]),
                    "selecionado": indice in selecionados,
                    **{
                        feature: matriz[indice, coluna]
                        for coluna, feature in enumerate(FEATURES)
                    },
                }
            )


def calcular_e_persistir_features(
    arquivos: list[Path], bfs_samples: int, seed: int
) -> Path:
    """Calcula um grafo por vez e persiste imediatamente as features em SQLite."""
    with tempfile.NamedTemporaryFile(
        prefix="selecao_grafos_", suffix=".sqlite3", delete=False
    ) as temporario:
        banco_path = Path(temporario.name)

    colunas = ", ".join(f'"{feature}" REAL NOT NULL' for feature in FEATURES)
    placeholders = ", ".join("?" for _ in range(2 + len(FEATURES)))
    conexao = sqlite3.connect(banco_path)
    try:
        conexao.execute("PRAGMA journal_mode=OFF")
        conexao.execute("PRAGMA synchronous=OFF")
        conexao.execute(
            f"CREATE TABLE features (indice INTEGER PRIMARY KEY, arquivo TEXT, {colunas})"
        )
        print(f"Características temporárias serão persistidas em {banco_path}")

        for indice, caminho in enumerate(arquivos):
            grafo = ler_dimacs_col(caminho)
            features = extrair_features(grafo, bfs_samples, seed)
            conexao.execute(
                f"INSERT INTO features VALUES ({placeholders})",
                (
                    indice,
                    caminho.name,
                    *(features[feature] for feature in FEATURES),
                ),
            )
            conexao.commit()
            print(
                f"[{indice + 1}/{len(arquivos)}] {caminho.name}: "
                f"n={features['n_vertices']}, m={features['n_arestas']}, "
                f"densidade={features['densidade']:.3f} [persistido]"
            )
            del features
            del grafo
            gc.collect()
    except BaseException:
        conexao.close()
        banco_path.unlink(missing_ok=True)
        raise
    else:
        conexao.close()
        return banco_path


def carregar_features(banco_path: Path) -> tuple[list[str], np.ndarray]:
    """Carrega do arquivo temporário somente os dados exigidos pelo k-means."""
    colunas = ", ".join(f'"{feature}"' for feature in FEATURES)
    with sqlite3.connect(banco_path) as conexao:
        registros = conexao.execute(
            f"SELECT arquivo, {colunas} FROM features ORDER BY indice"
        ).fetchall()
    nomes = [registro[0] for registro in registros]
    matriz = np.asarray([registro[1:] for registro in registros], dtype=np.float64)
    return nomes, matriz


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
    banco_path = calcular_e_persistir_features(
        arquivos, args.bfs_samples, args.seed
    )
    try:
        nomes, matriz = carregar_features(banco_path)
    finally:
        banco_path.unlink(missing_ok=True)
    print("Arquivo temporário removido; iniciando o agrupamento.")

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
            indice for indice in range(len(nomes)) if indice not in selecionados
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
    nomes_selecionados = {nomes[indice] for indice in selecionados}
    for existente in args.out.glob("*.col"):
        if existente.name not in nomes_selecionados:
            existente.unlink()

    print(f"Selecionados {len(selecionados)} grafos:")
    for indice in selecionados:
        nome = nomes[indice]
        valores = {
            feature: matriz[indice, coluna]
            for coluna, feature in enumerate(FEATURES)
        }
        shutil.copy2(args.input_dir / nome, args.out / nome)
        print(
            f"  {nome}: n={int(valores['n_vertices'])}, "
            f"m={int(valores['n_arestas'])}, "
            f"densidade={valores['densidade']:.3f}, "
            f"grau médio={valores['grau_medio']:.2f}"
        )

    if args.csv:
        salvar_csv(args.csv, nomes, matriz, clusters, selecionados)
        print(f"Características salvas em {args.csv}")
    print(f"Grafos copiados para {args.out}")


if __name__ == "__main__":
    main()
