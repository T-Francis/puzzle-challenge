# Wave 1 — proposals to try

Placements already confirmed: 0 — empty cells left: 412

Cell = L(line)-C(column), counted from the top-left corner of the puzzle seen in reading direction (text "Marcella Giulia Pace" at the bottom), frame included (L1 and C1 = frame).
Rotation: to apply to the piece **as it lies on its sheet** (sheet the right way up, as in the catalog), the puzzle being seen in reading direction.
**Reliability** = number of computation conditions (out of 9: catalog of the photos of the 18th, of the 19th or of all × 3 settings of the hole edges) where this piece comes first. The engine % is indicative: it is too optimistic until calibrated on real trials.

| # | Cell | Known sides | Proposed piece | Rotation | Reliability | Engine % | 2nd choice | 3rd choice |
|---|---|---|---|---|---|---|---|---|
| 1 | L18-C23 | 4 | **D051** | no turn | 9/9 | 100 % | C083 (no turn, 0 %) | D051 (half turn, 0 %) |
| 2 | L18-C27 | 3 | **D083** | ¼ turn anticlockwise | 9/9 | 100 % | C035 (no turn, 0 %) | B071 (¼ turn clockwise, 0 %) |
| 3 | L23-C27 | 3 | **A088** | no turn | 9/9 | 100 % | B097 (no turn, 0 %) | C085 (half turn, 0 %) |
| 4 | L21-C26 | 3 | **C048** | no turn | 9/9 | 99 % | D046 (¼ turn anticlockwise, 0 %) | B012 (¼ turn clockwise, 0 %) |
| 5 | L29-C13 | 3 | **A042** | no turn | 9/9 | 99 % | E005 (half turn, 1 %) | B015 (no turn, 0 %) |
| 6 | L19-C26 | 3 | **C038** | half turn | 9/9 | 94 % | C039 (¼ turn anticlockwise, 5 %) | C038 (no turn, 0 %) |
| 7 | L31-C16 | 2 | **B072** | no turn | 9/9 | 96 % | C045 (¼ turn clockwise, 2 %) | C096 (no turn, 0 %) |
| 8 | L20-C27 | 2 | **B034** | half turn | 9/9 | 91 % | C036 (¼ turn anticlockwise, 3 %) | D060 (no turn, 2 %) |
| 9 | L35-C3 | 2 | **A045** | no turn | 9/9 | 90 % | A058 (no turn, 9 %) | B007 (¼ turn clockwise, 1 %) |
| 10 | L28-C21 | 2 | **D043** | half turn | 9/9 | 78 % | C053 (¼ turn clockwise, 10 %) | A053 (half turn, 6 %) |
| 11 | L26-C8 | 2 | **B057** | ¼ turn clockwise | 9/9 | 77 % | D065 (¼ turn anticlockwise, 8 %) | C068 (¼ turn clockwise, 7 %) |
| 12 | L6-C17 | 2 | **B032** | ¼ turn clockwise | 9/9 | 74 % | A074 (¼ turn clockwise, 15 %) | B089 (half turn, 4 %) |
| 13 | L10-C4 | 2 | **A017** | half turn | 9/9 | 72 % | B091 (¼ turn anticlockwise, 18 %) | C073 (¼ turn clockwise, 4 %) |
| 14 | L10-C25 | 2 | **A078** | ¼ turn clockwise | 9/9 | 65 % | B030 (¼ turn anticlockwise, 22 %) | D051 (¼ turn clockwise, 2 %) |
| 15 | L7-C21 | 2 | **C090** | ¼ turn clockwise | 9/9 | 64 % | A079 (¼ turn clockwise, 35 %) | A034 (no turn, 1 %) |
| 16 | L25-C25 | 2 | **B005** | ¼ turn anticlockwise | 9/9 | 64 % | A032 (¼ turn clockwise, 18 %) | D065 (¼ turn anticlockwise, 16 %) |
| 17 | L26-C24 | 2 | **A038** | no turn | 9/9 | 64 % | D008 (half turn, 26 %) | B072 (half turn, 8 %) |
| 18 | L8-C23 | 2 | **A087** | no turn | 9/9 | 55 % | D072 (half turn, 35 %) | B041 (half turn, 8 %) |

Virtual fitting: `reports/wave_1_fitting.jpg` (green = 1st choice, orange = 2nd choice).

For each line: try the piece; if it fits, place it and note "OK", otherwise try the 2nd then the 3rd choice.
Send back the results quoting the cell and the piece (e.g. "L18-C23 D051 OK, L18-C27 KO"): they are recorded in `data/placements.json`.