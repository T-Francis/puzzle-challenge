# Conventions

Every module relies on these; change them only everywhere at once.

## Units and orientation

- **Unit `u`**: 100 u = median long side of the core of a piece (≈ 2 cm, so 1 u ≈ 0.2 mm). Median piece: 90 u × 100 u.
  Whole sheet photos ≈ 2.2 px/u, half sheets ≈ 3.1 px/u, board photo ≈ 1.26 px/u, rectified board 2 px/u. A sheet
  photo above 4 px/u is measured on a working image resampled to 3.2 px/u (`measure_sheets.working_image`); stored
  coordinates stay in pixels of the original photo.
- **Reading direction**: every photo is stored the right way up (pixels actually rotated, EXIF orientation = 1).
  Sheets: readable label at the top left. Puzzle: the text "Marcella Giulia Pace" at the bottom.
- **Contours clockwise on screen** (y pointing down), for pieces and board holes alike: a piece side is compared with
  a socket without flipping.

## Sides

- **Side profile**: 128 points, local frame x = corner → corner chord, y = outwards.
- **Types**: `T` tab, `B` blank, `F` flat (threshold 0.15 · L). A socket of type `T` means the cell requires a tab.
- **Side names**: `N`, `E`, `S`, `W` (clockwise, N on top).

## Frames and rotations

- **Canonical frame of a piece**: piece standing (short side on top = N), N/E/S/W clockwise, minimal rotation from its
  reference view (the most recent view, i.e. the current state of the sheet). `rotation_deg` = clockwise rotation that
  brings the piece, as it lies on its sheet the right way up, to this frame.
- **Display** (SVG / HTML catalog): the piece as on its sheet, rounded to the quarter turn: the canonical frame rotated
  by k = (−round(rotation_deg / 90)) mod 4 clockwise quarter turns. Displayed sides "top, right, bottom, left".
- **Rotation q** (engine): clockwise quarter turns applied to the canonical frame; board side k receives piece side
  (k − q) % 4. For the user: `plate_turns = round((rotation_deg + 90 · q) / 90) mod 4` clockwise quarter turns from
  the sheet (`wave.plate_turns`).

## Identifiers

- **Pieces**: sheet letter + number, sheets by rows of 10: `A001` … `D100`, `E001` … `E012` (shown as `D51` in the
  workshop, as written on the sheets).
- **Cells**: `L{line}-C{column}`, 1-based, from the top-left corner in reading direction, frame included.
- **Views**: `<photo>#<k>` (photo = file name without extension); npz keys `<photo>#<k>__<field>`.

## Sheet photos (`photos/manifest.json`, help at the top of the file)

1. Every useful photo of a sheet is declared and fused; none is favoured by hand (the weight of each view depends on
   its sharpness, its position in the image and its plausibility). Only the photos actually used are kept.
2. Naming `<sheet>[_colXX-YY]_<date>[_<time>].jpg`, stored in the reading direction of the sheet.
3. Number = row × 10 + sheet column; a photo of a former layout declares `number_grid` (number of each position of
   the photo, row by row).
4. `zone_x` / `zone_y`: area of the pieces of the sheet; pieces of another sheet in the frame are ignored (lying at
   another height, their scale would be wrong by 1 to 3 %).
5. Scale: automatic, nothing entered (`fusion.calibrate_scales`).
6. The most recent photo of a piece gives its orientation on the sheet (proposed rotations).

## `data/placements.json`

```json
{"placed": [{"cell": "L18-C23", "piece": "D051", "rotation": 0}],
 "excluded": ["L29-C13:A042"]}
```

`rotation` = q in degrees (canonical frame, not sheet): the value of `proposals.json` for the confirmed candidate.
Placements are processed in order; each placed piece is fitted on its sockets and its free sides become sockets for
its neighbours. Once the workshop runs, this file is derived from its journal (`docs/ATELIER.md`).
