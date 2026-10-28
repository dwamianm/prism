# Embedding models: tables

## The store pack and the cities question

| Recipe | Cities rank, three spellings | Cities MRR | Store pack top 3 | Rank 1 | MRR (17) |
|---|---:|---:|---:|---:|---:|
| bge-small | 26, 51, 52 | 0.026 | 14/17 | 6 | 0.559 |
| bge-small+q | 26, 52, 52 | 0.026 | 14/17 | 7 | 0.598 |
| minilm | 20, 52, 54 | 0.029 | 14/17 | 6 | 0.577 |
| bge-base | 19, 50, 52 | 0.031 | 13/17 | 7 | 0.604 |
| bge-base+q | 19, 51, 51 | 0.031 | 13/17 | 7 | 0.601 |
| bge-large | 19, 50, 51 | 0.031 | 13/17 | 7 | 0.604 |
| bge-large+q | 19, 51, 52 | 0.03 | 13/17 | 7 | 0.604 |
| mxbai | 17, 51, 51 | 0.033 | 14/17 | 6 | 0.57 |
| mxbai+q | 18, 51, 51 | 0.032 | 12/17 | 6 | 0.567 |
| nomic | 18, 51, 51 | 0.032 | 14/17 | 9 | 0.668 |
| nomic-bare | 18, 51, 52 | 0.031 | 14/17 | 7 | 0.609 |

Cosine of the cities question to the memory that answers it, to the best memory that is not the answer, and to the best memory that says "Japan trip" (three spellings, in the order above), and the answer's rank by cosine alone among the 75:

| Recipe | Spelling 1 | Spelling 2 | Spelling 3 |
|---|---:|---:|---:|
| bge-small | 0.52 / 0.75 / 0.67, #14 | 0.57 / 0.71 / 0.71, #12 | 0.50 / 0.69 / 0.67, #40 |
| bge-small+q | 0.49 / 0.72 / 0.64, #15 | 0.55 / 0.69 / 0.69, #14 | 0.47 / 0.69 / 0.64, #44 |
| minilm | 0.39 / 0.63 / 0.56, #10 | 0.41 / 0.61 / 0.61, #9 | 0.20 / 0.70 / 0.46, #53 |
| bge-base | 0.53 / 0.73 / 0.66, #9 | 0.56 / 0.70 / 0.70, #6 | 0.50 / 0.64 / 0.62, #29 |
| bge-base+q | 0.46 / 0.68 / 0.62, #9 | 0.50 / 0.67 / 0.67, #6 | 0.43 / 0.59 / 0.59, #27 |
| bge-large | 0.55 / 0.76 / 0.63, #7 | 0.56 / 0.69 / 0.68, #8 | 0.48 / 0.72 / 0.59, #29 |
| bge-large+q | 0.54 / 0.75 / 0.62, #9 | 0.54 / 0.68 / 0.66, #10 | 0.45 / 0.69 / 0.57, #36 |
| mxbai | 0.55 / 0.80 / 0.64, #7 | 0.56 / 0.72 / 0.70, #7 | 0.46 / 0.68 / 0.60, #29 |
| mxbai+q | 0.56 / 0.80 / 0.65, #7 | 0.57 / 0.73 / 0.71, #7 | 0.46 / 0.71 / 0.61, #33 |
| nomic | 0.65 / 0.83 / 0.72, #8 | 0.68 / 0.79 / 0.76, #6 | 0.61 / 0.78 / 0.69, #31 |
| nomic-bare | 0.61 / 0.82 / 0.69, #7 | 0.61 / 0.75 / 0.71, #7 | 0.50 / 0.70 / 0.61, #41 |

## The scale harness

### Blind questions (151), answers in the top 3 / top 5 / rank 1, MRR

| Recipe | Core 189 | 2,000 look-alike | 10,000 look-alike | 2,000 conversation |
|---|---:|---:|---:|---:|
| bge-small | 107/117/68, 0.598 | 99/109/82, 0.624 | 100/106/85, 0.629 | 108/116/70, 0.603 |
| bge-small+q | 109/116/70, 0.605 | 98/111/82, 0.622 | 102/105/83, 0.622 | 108/116/70, 0.604 |
| minilm | 103/116/56, 0.545 | 94/101/68, 0.564 | 97/100/73, 0.575 | 103/114/63, 0.564 |
| bge-base | 107/118/68, 0.603 | 106/113/80, 0.635 | 105/112/82, 0.631 | 113/120/72, 0.621 |
| bge-base+q | 106/117/67, 0.594 | 105/108/82, 0.634 | 101/107/84, 0.627 | 107/117/67, 0.592 |
| bge-large | 111/117/69, 0.611 | 102/113/83, 0.637 | 103/111/88, 0.652 | 112/119/70, 0.611 |
| bge-large+q | 110/118/70, 0.613 | 100/111/83, 0.632 | 102/106/86, 0.642 | 110/117/69, 0.606 |
| mxbai | 107/118/63, 0.580 | 98/109/79, 0.609 | 100/107/82, 0.620 | 108/118/65, 0.587 |
| mxbai+q | 106/116/65, 0.585 | 99/109/79, 0.614 | 103/107/82, 0.622 | 104/117/65, 0.582 |
| nomic | 104/115/60, 0.562 | 99/106/74, 0.597 | 100/109/79, 0.609 | 104/117/67, 0.587 |

### All 204 questions, answers in the top 3 / top 5 / rank 1, MRR

| Recipe | Core 189 | 2,000 look-alike | 10,000 look-alike | 2,000 conversation |
|---|---:|---:|---:|---:|
| bge-small | 128/144/80, 0.535 | 120/131/93, 0.542 | 118/128/99, 0.550 | 134/143/81, 0.537 |
| bge-small+q | 131/144/82, 0.541 | 117/134/92, 0.538 | 120/125/96, 0.539 | 133/143/81, 0.538 |
| minilm | 121/140/64, 0.478 | 110/120/79, 0.490 | 115/119/84, 0.499 | 124/140/74, 0.504 |
| bge-base | 128/146/80, 0.542 | 125/136/95, 0.560 | 122/133/98, 0.556 | 138/148/86, 0.563 |
| bge-base+q | 127/147/78, 0.533 | 123/130/96, 0.557 | 117/127/99, 0.548 | 131/144/80, 0.537 |
| bge-large | 132/147/81, 0.548 | 121/135/96, 0.555 | 120/130/102, 0.562 | 136/147/85, 0.556 |
| bge-large+q | 131/146/83, 0.551 | 119/133/97, 0.554 | 119/124/100, 0.555 | 135/145/84, 0.552 |
| mxbai | 129/146/74, 0.523 | 114/130/92, 0.533 | 117/127/95, 0.536 | 132/146/78, 0.532 |
| mxbai+q | 126/144/77, 0.526 | 115/129/92, 0.534 | 119/127/95, 0.537 | 127/144/78, 0.527 |
| nomic | 124/139/70, 0.500 | 116/126/86, 0.522 | 118/128/92, 0.530 | 125/143/81, 0.530 |

### Blind top 3 against bge-small (answers, points of 151)

| Recipe | Core 189 | 2,000 look-alike | 10,000 look-alike | 2,000 conversation |
|---|---:|---:|---:|---:|
| bge-small | +0 (+0.0 pts) | +0 (+0.0 pts) | +0 (+0.0 pts) | +0 (+0.0 pts) |
| bge-small+q | +2 (+1.3 pts) | -1 (-0.7 pts) | +2 (+1.3 pts) | +0 (+0.0 pts) |
| minilm | -4 (-2.6 pts) | -5 (-3.3 pts) | -3 (-2.0 pts) | -5 (-3.3 pts) |
| bge-base | +0 (+0.0 pts) | +7 (+4.6 pts) | +5 (+3.3 pts) | +5 (+3.3 pts) |
| bge-base+q | -1 (-0.7 pts) | +6 (+4.0 pts) | +1 (+0.7 pts) | -1 (-0.7 pts) |
| bge-large | +4 (+2.6 pts) | +3 (+2.0 pts) | +3 (+2.0 pts) | +4 (+2.6 pts) |
| bge-large+q | +3 (+2.0 pts) | +1 (+0.7 pts) | +2 (+1.3 pts) | +2 (+1.3 pts) |
| mxbai | +0 (+0.0 pts) | -1 (-0.7 pts) | +0 (+0.0 pts) | +0 (+0.0 pts) |
| mxbai+q | -1 (-0.7 pts) | +0 (+0.0 pts) | +3 (+2.0 pts) | -4 (-2.6 pts) |
| nomic | -3 (-2.0 pts) | +0 (+0.0 pts) | +0 (+0.0 pts) | -4 (-2.6 pts) |

### Top 3 by question set: original (53) / blind-v1 (86) / blind-v2 (65)

| Recipe | 2,000 look-alike | 10,000 look-alike | 2,000 conversation |
|---|---:|---:|---:|
| bge-small | 21 / 43 / 56 | 18 / 44 / 56 | 26 / 51 / 57 |
| bge-small+q | 19 / 42 / 56 | 18 / 46 / 56 | 25 / 52 / 56 |
| minilm | 16 / 39 / 55 | 18 / 42 / 55 | 21 / 47 / 56 |
| bge-base | 19 / 50 / 56 | 17 / 49 / 56 | 25 / 57 / 56 |
| bge-base+q | 18 / 49 / 56 | 16 / 47 / 54 | 24 / 53 / 54 |
| bge-large | 19 / 46 / 56 | 17 / 48 / 55 | 24 / 56 / 56 |
| bge-large+q | 19 / 44 / 56 | 17 / 47 / 55 | 25 / 55 / 55 |
| mxbai | 16 / 43 / 55 | 17 / 47 / 53 | 24 / 53 / 55 |
| mxbai+q | 16 / 43 / 56 | 16 / 48 / 55 | 23 / 50 / 54 |
| nomic | 17 / 43 / 56 | 18 / 46 / 54 | 21 / 51 / 53 |

### Blind questions gained and lost in the top 3 against bge-small (paired, with a two-sided sign test)

| Recipe | Core 189 | 2,000 look-alike | 10,000 look-alike | 2,000 conversation |
|---|---:|---:|---:|---:|
| bge-small+q | +2 / -0, p 0.50 | +0 / -1, p 1.00 | +3 / -1, p 0.62 | +1 / -1, p 1.00 |
| minilm | +2 / -6, p 0.29 | +3 / -8, p 0.23 | +3 / -6, p 0.51 | +2 / -7, p 0.18 |
| bge-base | +4 / -4, p 1.00 | +7 / -0, p 0.02 | +6 / -1, p 0.12 | +6 / -1, p 0.12 |
| bge-base+q | +3 / -4, p 1.00 | +8 / -2, p 0.11 | +5 / -4, p 1.00 | +4 / -5, p 1.00 |
| bge-large | +5 / -1, p 0.22 | +5 / -2, p 0.45 | +5 / -2, p 0.45 | +5 / -1, p 0.22 |
| bge-large+q | +4 / -1, p 0.38 | +4 / -3, p 1.00 | +5 / -3, p 0.73 | +4 / -2, p 0.69 |
| mxbai | +5 / -5, p 1.00 | +3 / -4, p 1.00 | +6 / -6, p 1.00 | +4 / -4, p 1.00 |
| mxbai+q | +3 / -4, p 1.00 | +4 / -4, p 1.00 | +7 / -4, p 0.55 | +3 / -7, p 0.34 |
| nomic | +4 / -7, p 0.55 | +5 / -5, p 1.00 | +6 / -6, p 1.00 | +4 / -8, p 0.39 |

### Top 3 by question type against bge-small, 2000 lookalike memories (all 204 questions)

| Recipe | lookup (76) | list (27) | multi-hop (25) | temporal (21) | correction (19) | negation (23) | aggregation (13) | Worst |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bge-small | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
| bge-small+q | -1 | -1 | +0 | +0 | -1 | +0 | +0 | -1 |
| minilm | -3 | -2 | -1 | +1 | -4 | -1 | +0 | -4 |
| bge-base | +1 | +0 | +0 | +3 | +1 | +0 | +0 | +0 |
| bge-base+q | +3 | -2 | +0 | +2 | -1 | +1 | +0 | -2 |
| bge-large | +0 | -1 | -1 | +1 | +1 | +1 | +0 | -1 |
| bge-large+q | -1 | -2 | -1 | +1 | +1 | +1 | +0 | -2 |
| mxbai | -3 | -1 | -1 | +1 | -3 | +1 | +0 | -3 |
| mxbai+q | -3 | -2 | -1 | +2 | -2 | +1 | +0 | -3 |
| nomic | +0 | -1 | -1 | +0 | -1 | -1 | +0 | -1 |

### Top 3 by question type against bge-small, 2000 conversation memories (all 204 questions)

| Recipe | lookup (76) | list (27) | multi-hop (25) | temporal (21) | correction (19) | negation (23) | aggregation (13) | Worst |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bge-small | +0 | +0 | +0 | +0 | +0 | +0 | +0 | +0 |
| bge-small+q | -1 | +0 | +0 | +0 | +1 | -1 | +0 | -1 |
| minilm | -6 | -1 | -1 | +0 | +0 | -2 | +0 | -6 |
| bge-base | +0 | +1 | +2 | +0 | +1 | +0 | +0 | +0 |
| bge-base+q | -2 | +0 | -1 | +0 | +1 | -1 | +0 | -2 |
| bge-large | +0 | +0 | +1 | +1 | +1 | -1 | +0 | -1 |
| bge-large+q | +0 | +0 | +1 | +0 | +1 | -1 | +0 | -1 |
| mxbai | -1 | -1 | +0 | +0 | +0 | +0 | +0 | -1 |
| mxbai+q | -3 | -1 | -1 | +0 | +0 | -2 | +0 | -3 |
| nomic | -3 | -1 | -2 | -1 | +0 | -2 | +0 | -3 |

## Cost

### Embedding cost on one thread

| Model | Dim | p50 ms/text | p95 ms/text | p50 vs bge-small | p50 ms, conversation turn | Embed the 10,000 store | Download | Loaded model memory | Within 8x |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bge-small | 384 | 2.83 | 4.65 | 1.0x | 3.1 | 0.7 min | 67 MB | 310 MB | yes |
| minilm | 384 | 1.25 | 2.09 | 0.4x | 1.46 | 0.4 min | 91 MB | 265 MB | yes |
| bge-base | 768 | 6.44 | 11.12 | 2.3x | 6.96 | 1.5 min | 218 MB | 940 MB | yes |
| bge-large | 1024 | 17.91 | 32.56 | 6.3x | 19.38 | 4.1 min | 1338 MB | 2691 MB | yes |
| mxbai | 1024 | 17.65 | 31.83 | 6.2x | 19.42 | 4.1 min | 1338 MB | 2678 MB | yes |
| nomic | 768 | 8.2 | 14.93 | 2.9x | 10.41 | 2.1 min | 548 MB | 1199 MB | yes |

### PostgreSQL pgvector per 10,000 memories

| Model | Dim | Bytes per vector | Vector column MB | HNSW index MB | Vector and index MB | HNSW build s | Vector UPDATE p50 / p95 ms | Exact search p50 / p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bge-small | 384 | 1544.0 | 18.97 | 20.49 | 39.46 | 1.2 | 0.48 / 0.85 | 5.45 / 6.43 |
| minilm | 384 | 1544.0 | 18.97 | 20.49 | 39.46 | 1.3 | 0.47 / 0.69 | 5.4 / 5.9 |
| bge-base | 768 | 3076.0 | 41.81 | 40.97 | 82.78 | 1.8 | 0.66 / 0.84 | 21.72 / 22.62 |
| bge-large | 1024 | 4100.0 | 55.54 | 81.93 | 137.47 | 2.2 | 0.78 / 1.11 | 24.15 / 25.46 |
| mxbai | 1024 | 4100.0 | 55.54 | 81.93 | 137.47 | 2.2 | 0.73 / 0.95 | 22.75 / 23.47 |
| nomic | 768 | 3076.0 | 41.81 | 40.97 | 82.78 | 2.0 | 0.69 / 0.85 | 19.94 / 21.51 |

### One store() and one retrieve() call on PostgreSQL, one thread (the 75-memory pack)

| Model | store() p50 / p95 ms | of which embedding p50 ms | retrieve() p50 / p95 ms | of which question embedding p50 ms |
|---|---:|---:|---:|---:|
| bge-small | 28.2 / 34.9 | 3.1 | 19.0 / 72.2 | 2.5 |
| minilm | 26.0 / 29.2 | 1.6 | 17.4 / 27.3 | 1.4 |
| bge-base | 35.4 / 39.2 | 6.9 | 21.8 / 43.2 | 5.8 |
| bge-large | 47.6 / 59.8 | 17.9 | 32.1 / 44.9 | 15.4 |
| mxbai | 47.9 / 55.8 | 17.9 | 33.2 / 60.5 | 15.6 |
| nomic | 37.0 / 46.2 | 8.6 | 23.2 / 44.0 | 7.2 |

## Pass rule lines 1, 2 and 6

| Recipe | Blind gain at 2,000 look-alike (questions) | Line 1 (at least 7) | Worst type, look-alike | Worst type, conversation | Line 2 (not below -1) | p50 vs bge-small | Line 6 (within 8x) |
|---|---:|---:|---:|---:|---:|---:|---:|
| bge-small | 0 | False | 0 | 0 | True | 1.0x | True |
| bge-small+q | -1 | False | -1 | -1 | True | 1.0x | True |
| minilm | -5 | False | -4 | -6 | False | 0.4x | True |
| bge-base | 7 | True | 0 | 0 | True | 2.3x | True |
| bge-base+q | 6 | False | -2 | -2 | False | 2.3x | True |
| bge-large | 3 | False | -1 | -1 | True | 6.3x | True |
| bge-large+q | 1 | False | -2 | -1 | False | 6.3x | True |
| mxbai | -1 | False | -3 | -1 | False | 6.2x | True |
| mxbai+q | 0 | False | -3 | -3 | False | 6.2x | True |
| nomic | 0 | False | -1 | -3 | False | 2.9x | True |

