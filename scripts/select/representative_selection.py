#!/usr/bin/env python3
"""Seleciona grafos representativos com isolamento de memória e retomada."""

from __future__ import annotations

import argparse
import csv
import math
import multiprocessing as mp
import os
import random
import resource
import shutil
import sqlite3
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

FEATURES_INTEIRAS = {
    "n_vertices",
    "n_arestas",
    "grau_minimo",
    "grau_maximo",
    "num_componentes",
}


def formatar_feature_para_csv(feature: str, valor: float) -> int | str:
    """Preserva contagens como inteiros e floats com seis casas decimais."""
    if feature in FEATURES_INTEIRAS:
        return int(valor)
    return f"{float(valor):.6f}"


def ler_cabecalho_dimacs(caminho: Path) -> tuple[int, int] | None:
    """Lê somente `p edge n m`, sem carregar o grafo."""
    with caminho.open("r", encoding="utf-8", errors="replace") as arquivo:
        for linha in arquivo:
            if linha.startswith("p "):
                partes = linha.split()
                if len(partes) < 4:
                    return None
                return int(partes[2]), int(partes[3])
    return None


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
            "características estruturais, com isolamento de memória e retomada"
        )
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bfs-samples", type=int, default=10)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--db-name", help="Nome da base dentro de datasets/")
    parser.add_argument("--db", type=Path, help="Caminho explícito do banco SQLite")
    parser.add_argument(
        "--max-vertices",
        type=int,
        default=500_000,
        help="Pula grafos acima deste número de vértices; use 0 para desativar",
    )
    parser.add_argument(
        "--memory-limit-mb",
        "--limite-memoria-mb",
        dest="memory_limit_mb",
        type=int,
        default=4096,
        help="Limite de memória por subprocesso em MB; padrão: 4096",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=300,
        help="Tempo máximo por grafo; use 0 para desativar",
    )
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
    parser.add_argument(
        "--only-select",
        "--so-selecionar",
        dest="only_select",
        action="store_true",
        help="Usa os dados já persistidos no banco sem calcular novos grafos",
    )
    return parser.parse_args()


def resolver_caminho_banco(args: argparse.Namespace) -> Path:
    if args.db is not None:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        return args.db

    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida; execute pelo mise ou passe --db"
        )
    nome_base = args.db_name or args.input_dir.parent.name
    destino = Path(project_root) / "datasets" / nome_base
    destino.mkdir(parents=True, exist_ok=True)
    return destino / "features.sqlite3"


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
                        feature: formatar_feature_para_csv(
                            feature, matriz[indice, coluna]
                        )
                        for coluna, feature in enumerate(FEATURES)
                    },
                }
            )


def _worker(
    caminho: str,
    bfs_samples: int,
    seed: int,
    limite_bytes: int,
    transmissor,
) -> None:
    """Calcula um grafo em subprocesso limitado e devolve um resultado pequeno."""
    try:
        if limite_bytes > 0:
            resource.setrlimit(resource.RLIMIT_AS, (limite_bytes, limite_bytes))
    except (ValueError, OSError):
        pass

    try:
        grafo = ler_dimacs_col(Path(caminho))
        transmissor.send((True, extrair_features(grafo, bfs_samples, seed)))
    except MemoryError:
        transmissor.send((False, "memória insuficiente"))
    except Exception as erro:
        transmissor.send((False, f"{type(erro).__name__}: {erro}"))
    finally:
        transmissor.close()


def calcular_features_isolado(
    caminho: Path,
    bfs_samples: int,
    seed: int,
    limite_bytes: int,
    timeout_s: float,
) -> tuple[bool, dict | str]:
    """Executa um worker descartável, sobrevivendo a timeout, crash ou OOM."""
    contexto = mp.get_context("fork")
    receptor, transmissor = contexto.Pipe(duplex=False)
    processo = contexto.Process(
        target=_worker,
        args=(str(caminho), bfs_samples, seed, limite_bytes, transmissor),
    )
    processo.start()
    transmissor.close()
    processo.join(timeout_s if timeout_s > 0 else None)

    if processo.is_alive():
        processo.terminate()
        processo.join(5)
        if processo.is_alive():
            processo.kill()
            processo.join()
        receptor.close()
        return False, f"timeout após {timeout_s:.0f}s"

    if receptor.poll(1):
        resultado = receptor.recv()
        receptor.close()
        return resultado
    receptor.close()
    return (
        False,
        f"processo terminou sem resultado (exitcode={processo.exitcode}; provável OOM)",
    )


def preparar_banco(db_path: Path) -> sqlite3.Connection:
    colunas = ", ".join(f'"{feature}" REAL NOT NULL' for feature in FEATURES)
    conexao = sqlite3.connect(db_path)
    conexao.execute("PRAGMA journal_mode=WAL")
    conexao.execute("PRAGMA synchronous=NORMAL")
    conexao.execute(
        f"CREATE TABLE IF NOT EXISTS features "
        f"(arquivo TEXT PRIMARY KEY, {colunas})"
    )
    conexao.execute(
        "CREATE TABLE IF NOT EXISTS failures "
        "(arquivo TEXT PRIMARY KEY, motivo TEXT NOT NULL)"
    )
    conexao.commit()
    return conexao


def ordenar_por_tamanho(arquivos: list[Path]) -> list[Path]:
    """Ordena pelo número declarado de vértices sem carregar os grafos."""
    return sorted(
        arquivos,
        key=lambda caminho: (ler_cabecalho_dimacs(caminho) or (0, 0))[0],
    )


def calcular_e_persistir_features(
    arquivos: list[Path],
    bfs_samples: int,
    seed: int,
    db_path: Path,
    max_vertices: int,
    memory_limit_mb: int,
    timeout_s: float,
) -> None:
    """Retoma o banco e processa somente arquivos ainda desconhecidos."""
    conexao = preparar_banco(db_path)
    processados = {
        registro[0] for registro in conexao.execute("SELECT arquivo FROM features")
    }
    processados.update(
        registro[0] for registro in conexao.execute("SELECT arquivo FROM failures")
    )
    pendentes = [
        caminho for caminho in ordenar_por_tamanho(arquivos)
        if caminho.name not in processados
    ]
    print(
        f"Banco persistente: {db_path} | concluídos: {len(processados)} | "
        f"pendentes: {len(pendentes)}"
    )

    placeholders = ", ".join("?" for _ in range(1 + len(FEATURES)))
    limite_bytes = memory_limit_mb * 1024 * 1024
    for indice, caminho in enumerate(pendentes, start=1):
        cabecalho = ler_cabecalho_dimacs(caminho)
        if max_vertices > 0 and cabecalho and cabecalho[0] > max_vertices:
            motivo = f"n={cabecalho[0]} excede max-vertices={max_vertices}"
            conexao.execute(
                "INSERT OR REPLACE INTO failures VALUES (?, ?)",
                (caminho.name, motivo),
            )
            conexao.commit()
            print(f"[{indice}/{len(pendentes)}] {caminho.name}: PULADO ({motivo})")
            continue

        sucesso, resultado = calcular_features_isolado(
            caminho, bfs_samples, seed, limite_bytes, timeout_s
        )
        if sucesso:
            conexao.execute(
                f"INSERT OR REPLACE INTO features VALUES ({placeholders})",
                (caminho.name, *(resultado[feature] for feature in FEATURES)),
            )
            conexao.commit()
            print(
                f"[{indice}/{len(pendentes)}] {caminho.name}: "
                f"n={resultado['n_vertices']}, m={resultado['n_arestas']}, "
                f"densidade={resultado['densidade']:.6f} [OK]"
            )
        else:
            conexao.execute(
                "INSERT OR REPLACE INTO failures VALUES (?, ?)",
                (caminho.name, str(resultado)),
            )
            conexao.commit()
            print(f"[{indice}/{len(pendentes)}] {caminho.name}: FALHOU ({resultado})")
    conexao.close()


def carregar_features(
    banco_path: Path,
) -> tuple[list[str], np.ndarray, list[tuple[str, str]]]:
    """Carrega somente os registros bem-sucedidos para o k-means."""
    colunas = ", ".join(f'"{feature}"' for feature in FEATURES)
    with sqlite3.connect(banco_path) as conexao:
        registros = conexao.execute(
            f"SELECT arquivo, {colunas} FROM features ORDER BY arquivo"
        ).fetchall()
        falhas = conexao.execute(
            "SELECT arquivo, motivo FROM failures ORDER BY arquivo"
        ).fetchall()
    nomes = [registro[0] for registro in registros]
    matriz = np.asarray([registro[1:] for registro in registros], dtype=np.float64)
    return nomes, matriz, falhas


def main() -> None:
    args = parse_args()
    if args.n < 1:
        raise ValueError("n deve ser maior ou igual a 1")
    if args.bfs_samples < 1:
        raise ValueError("bfs-samples deve ser maior ou igual a 1")
    if args.max_vertices < 0:
        raise ValueError("max-vertices deve ser maior ou igual a 0")
    if args.memory_limit_mb < 0:
        raise ValueError("memory-limit-mb deve ser maior ou igual a 0")

    banco_path = resolver_caminho_banco(args)
    if not args.only_select:
        arquivos = sorted(args.input_dir.glob("*.col"))
        if args.ignorar_sem_arestas:
            invalidos = [
                caminho for caminho in arquivos
                if not possui_vertices_e_arestas(caminho)
            ]
            arquivos = [caminho for caminho in arquivos if caminho not in invalidos]
            for caminho in invalidos:
                print(f"IGNORADO [sem arestas]: {caminho}")
        if not arquivos:
            raise FileNotFoundError(
                f"nenhum arquivo .col encontrado em {args.input_dir}"
            )
        calcular_e_persistir_features(
            arquivos,
            args.bfs_samples,
            args.seed,
            banco_path,
            args.max_vertices,
            args.memory_limit_mb,
            args.timeout_s,
        )
    elif not banco_path.exists():
        raise FileNotFoundError(f"banco de características não encontrado: {banco_path}")

    nomes, matriz, falhas = carregar_features(banco_path)
    if falhas:
        print(f"\n{len(falhas)} arquivo(s) pulado(s) ou com falha:")
        for nome, motivo in falhas:
            print(f"  {nome}: {motivo}")
    if not nomes:
        raise RuntimeError("nenhum grafo foi processado com sucesso")

    n_alvo = args.n
    if n_alvo > len(nomes):
        if not args.usar_todos_se_faltar:
            raise ValueError(
                f"foram solicitados {n_alvo} grafos, mas somente "
                f"{len(nomes)} foram processados com sucesso"
            )
        print(
            f"AVISO: solicitados {n_alvo}, disponíveis {len(nomes)}; "
            "selecionando todos"
        )
        n_alvo = len(nomes)

    matriz_padronizada = StandardScaler().fit_transform(matriz)
    kmeans = KMeans(n_clusters=n_alvo, random_state=args.seed, n_init=10)
    clusters = kmeans.fit_predict(matriz_padronizada)

    selecionados: list[int] = []
    for cluster in range(n_alvo):
        indices = np.where(clusters == cluster)[0]
        if not len(indices):
            continue
        pontos = matriz_padronizada[indices]
        distancias = np.linalg.norm(pontos - kmeans.cluster_centers_[cluster], axis=1)
        selecionados.append(int(indices[np.argmin(distancias)]))

    if len(selecionados) < n_alvo:
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
            restantes[indice] for indice in ordem[: n_alvo - len(selecionados)]
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
            f"densidade={valores['densidade']:.6f}, "
            f"grau médio={valores['grau_medio']:.6f}"
        )

    if args.csv:
        salvar_csv(args.csv, nomes, matriz, clusters, selecionados)
        print(f"Características salvas em {args.csv}")
    print(f"Grafos copiados para {args.out}")


if __name__ == "__main__":
    main()
