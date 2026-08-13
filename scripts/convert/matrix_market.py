"""Converte matrizes Matrix Market em grafos DIMACS .col."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import scipy.io
import scipy.sparse


def ler_matriz(caminho_mtx: Path):
    """Lê uma matriz, corrigindo metadados coordinate inconsistentes em cópia."""
    with caminho_mtx.open("r", encoding="utf-8") as arquivo:
        banner = arquivo.readline()
        if not banner.lower().startswith("%%matrixmarket matrix coordinate"):
            return scipy.io.mmread(caminho_mtx, spmatrix=True)

        numero_linha_dimensoes = 1
        for linha in arquivo:
            numero_linha_dimensoes += 1
            texto = linha.strip()
            if texto and not texto.startswith("%"):
                dimensoes = texto.split()
                break
        else:
            raise ValueError("cabeçalho Matrix Market sem dimensões")

        if len(dimensoes) == 1:
            n_linhas = n_colunas = int(dimensoes[0])
        elif len(dimensoes) >= 2:
            n_linhas, n_colunas = map(int, dimensoes[:2])
        else:
            raise ValueError("dimensões inválidas no cabeçalho Matrix Market")

        entradas_reais = sum(
            1
            for linha in arquivo
            if linha.strip() and not linha.lstrip().startswith("%")
        )

    cabecalho_correto = f"{n_linhas} {n_colunas} {entradas_reais}"
    if len(dimensoes) >= 3 and dimensoes[:3] == cabecalho_correto.split():
        return scipy.io.mmread(caminho_mtx, spmatrix=True)

    cabecalho_original = " ".join(dimensoes)
    print(
        f"CORRIGINDO cabeçalho de {caminho_mtx.name}: "
        f"'{cabecalho_original}' -> '{cabecalho_correto}'"
    )

    caminho_temporario: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".mtx", delete=False
        ) as temporario, caminho_mtx.open("r", encoding="utf-8") as origem:
            caminho_temporario = Path(temporario.name)
            for numero_linha, linha in enumerate(origem, start=1):
                if numero_linha == numero_linha_dimensoes:
                    temporario.write(f"{cabecalho_correto}\n")
                else:
                    temporario.write(linha)

        return scipy.io.mmread(caminho_temporario, spmatrix=True)
    finally:
        if caminho_temporario is not None:
            caminho_temporario.unlink(missing_ok=True)


def converter_arquivo(caminho_mtx: Path, pasta_saida: Path) -> bool:
    """Converte uma matriz quadrada e simétrica em um grafo não direcionado."""
    caminho_col = pasta_saida / f"{caminho_mtx.stem}.col"

    try:
        matriz = ler_matriz(caminho_mtx)
    except Exception as error:
        print(f"FALHOU ao ler {caminho_mtx}: {error}")
        return False

    n_linhas, n_colunas = matriz.shape
    if n_linhas != n_colunas:
        print(
            f"PULADO {caminho_mtx.stem}: matriz não quadrada "
            f"({n_linhas}x{n_colunas}), não representa um grafo"
        )
        return False

    matriz_csr = scipy.sparse.csr_matrix(matriz)
    matriz_csr.eliminate_zeros()

    if (matriz_csr != matriz_csr.transpose()).nnz != 0:
        print(
            f"PULADO {caminho_mtx.stem}: matriz não simétrica, "
            "não representa um grafo não direcionado"
        )
        return False

    matriz_coo = matriz_csr.tocoo()
    arestas: set[tuple[int, int]] = set()

    for linha, coluna, valor in zip(
        matriz_coo.row, matriz_coo.col, matriz_coo.data
    ):
        if linha == coluna or valor == 0:
            continue

        u, v = sorted((int(linha) + 1, int(coluna) + 1))
        arestas.add((u, v))

    pasta_saida.mkdir(parents=True, exist_ok=True)
    with caminho_col.open("w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(
            f"c Convertido automaticamente de {caminho_mtx.name}\n"
        )
        arquivo.write(f"p edge {n_linhas} {len(arestas)}\n")
        for u, v in sorted(arestas):
            arquivo.write(f"e {u} {v}\n")

    print(
        f"OK: {caminho_mtx.stem} -> {n_linhas} vértices, "
        f"{len(arestas)} arestas -> {caminho_col}"
    )
    return True


def encontrar_matrizes(entrada: Path) -> list[Path]:
    """Retorna recursivamente os arquivos .mtx presentes na entrada."""
    if entrada.is_dir():
        return sorted(entrada.rglob("*.mtx"))
    if entrada.is_file() and entrada.suffix.lower() == ".mtx":
        return [entrada]
    raise ValueError(f"A entrada deve ser um arquivo .mtx ou uma pasta: {entrada}")


def encontrar_bases(datasets: Path) -> list[tuple[Path, Path]]:
    """Encontra os diretórios raw e processed de todas as bases."""
    return [
        (raw, raw.parent / "processed")
        for raw in sorted(datasets.glob("*/raw"))
        if raw.is_dir()
    ]


def converter_base(entrada: Path, pasta_saida: Path) -> tuple[int, int]:
    """Converte todos os arquivos Matrix Market de uma base."""
    matrizes = encontrar_matrizes(entrada)
    if not matrizes:
        print(f"Nenhum arquivo .mtx encontrado em {entrada}")
        return 0, 0

    print(f"Convertendo {len(matrizes)} matrizes de {entrada} para {pasta_saida}")
    convertidas = sum(
        converter_arquivo(caminho, pasta_saida) for caminho in matrizes
    )
    return convertidas, len(matrizes)


def main() -> None:
    project_root = os.environ.get("PROJECT_ROOT")
    if not project_root:
        raise RuntimeError(
            "PROJECT_ROOT não está definida. Execute este script pelo mise ou "
            "defina a variável de ambiente explicitamente."
        )

    datasets = Path(project_root) / "datasets"
    if len(sys.argv) > 2:
        raise ValueError("Uso: python matrix_market.py [arquivo.mtx | pasta_com_mtx]")

    if len(sys.argv) == 2:
        entrada = Path(sys.argv[1])
        pasta_base = entrada if entrada.is_dir() else entrada.parent
        pasta_saida = (
            pasta_base.parent / "processed"
            if pasta_base.name == "raw"
            else pasta_base / "processed"
        )
        bases = [(entrada, pasta_saida)]
    else:
        bases = encontrar_bases(datasets)

    if not bases:
        raise RuntimeError(f"Nenhuma base com diretório raw encontrada em {datasets}")

    total_convertidas = 0
    total_matrizes = 0
    for entrada, pasta_saida in bases:
        convertidas, encontradas = converter_base(entrada, pasta_saida)
        total_convertidas += convertidas
        total_matrizes += encontradas

    if total_matrizes == 0:
        raise RuntimeError("Nenhum arquivo .mtx foi encontrado nas bases de dados.")

    print(
        f"Concluído: {total_convertidas} de {total_matrizes} matrizes convertidas."
    )


if __name__ == "__main__":
    main()
