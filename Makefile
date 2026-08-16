# Flags pensadas para Ryzen 5 3500u (Zen+, 4C/8T, AVX2/FMA3, sem AVX-512).
#
# -march=native: deixa o GCC detectar exatamente as extensoes de instrucao
#   e o tuning de cache da CPU em que voce esta compilando -- normalmente a
#   melhor escolha quando voce compila na mesma maquina que vai executar
#   (que e' o seu caso). Se algum dia compilar num PC diferente para rodar
#   no Ryzen, troque para -march=znver1 -mtune=znver1 (Zen/Zen+ compartilham
#   o mesmo conjunto de instrucoes; o GCC nao tem um alvo "znver1+" separado).
#
# -flto: otimizacao entre arquivos (link-time), ajuda porque graph.c expõe
#   varias funcoes que o chamador vai inlinear/otimizar entre modulos.
#
# -funroll-loops: os loops de parsing e construcao do CSR sao previsiveis
#   o suficiente para se beneficiar de unroll.
#
# -falign-functions=32 -falign-loops=32: Zen prefere alinhamento de 32 bytes
#   para entradas de funcao/loop (afeta o front-end de decode).
#
# -fomit-frame-pointer: libera um registrador a mais (util em x86-64 com
#   poucos registradores de proposito geral fora dos que o SysV ABI reserva).

CC      := gcc
CFLAGS  := -std=c11 -O3 -march=native -mtune=native -flto \
           -funroll-loops -falign-functions=32 -falign-loops=32 \
           -fomit-frame-pointer -Wall -Wextra

.PHONY: all clean

all: graph.o

graph.o: src/graph/graph.c src/graph/graph.h
	$(CC) $(CFLAGS) -c src/graph/graph.c -o graph.o

clean:
	rm -f *.o *.bin
