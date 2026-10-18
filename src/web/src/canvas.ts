export interface TextStyle {
  font: string;
  font_size_mm: number;
  char_gap_mm: number;
  line_gap_mm: number;
}
export interface Geometry {
  id: string;
  x_mm: number;
  y_mm: number;
  width_mm: number;
  height_mm: number;
}
export interface TextBox extends Geometry, TextStyle {
  type: "text";
  text: string;
  align: "left" | "center" | "right";
}
export interface Cell {
  row: number;
  col: number;
  rowspan: number;
  colspan: number;
  text: string;
  align: "left" | "center" | "right";
  valign: "top" | "center" | "bottom";
}
export interface Table extends Geometry, TextStyle {
  type: "table";
  column_widths: number[];
  row_heights: number[];
  cells: Cell[];
  padding_mm: number;
  draw_borders: boolean;
}
export type PaperElement = TextBox | Table;
export interface WritingDocument {
  version: 2;
  name: string;
  paper: "A4" | "A5";
  margin_mm: number;
  elements: PaperElement[];
}
export interface Diagnostic {
  element_id: string;
  row?: number;
  col?: number;
  code: string;
  message: string;
}
export const blank: WritingDocument = {
  version: 2,
  name: "未命名文档",
  paper: "A4",
  margin_mm: 20,
  elements: [],
};
export const defaultStyle: TextStyle = {
  font: "",
  font_size_mm: 8,
  char_gap_mm: 0,
  line_gap_mm: 3,
};
export function pageSize(d: WritingDocument) {
  return d.paper === "A4" ? [210, 297] : [148, 210];
}
function placement(d: WritingDocument, w: number, h: number) {
  const [pw, ph] = pageSize(d),
    m = d.margin_mm;
  const candidates = [
    { x: m, y: m },
    ...d.elements.flatMap((e) => [
      { x: e.x_mm, y: e.y_mm + e.height_mm + 4 },
      { x: e.x_mm + e.width_mm + 4, y: e.y_mm },
    ]),
  ];
  return (
    candidates.find(
      (p) =>
        p.x + w <= pw - m &&
        p.y + h <= ph - m &&
        !d.elements.some(
          (e) =>
            p.x < e.x_mm + e.width_mm &&
            p.x + w > e.x_mm &&
            p.y < e.y_mm + e.height_mm &&
            p.y + h > e.y_mm,
        ),
    ) || { x: m, y: m }
  );
}
export function newText(d: WritingDocument, text = ""): TextBox {
  const [w, h] = pageSize(d),
    m = d.margin_mm;
  const boxWidth = Math.min(100, w - 2 * m);
  const position = placement(d, boxWidth, Math.min(60, h - 2 * m));
  return {
    ...defaultStyle,
    id: crypto.randomUUID(),
    type: "text",
    text,
    align: "left",
    x_mm: position.x,
    y_mm: position.y,
    width_mm: boxWidth,
    height_mm: Math.min(60, h - 2 * m),
  };
}
export function newCell(row: number, col: number): Cell {
  return {
    row,
    col,
    rowspan: 1,
    colspan: 1,
    text: "",
    align: "left",
    valign: "center",
  };
}
export function newTable(d: WritingDocument): Table {
  const [w, h] = pageSize(d),
    m = d.margin_mm;
  const width = Math.min(120, w - 2 * m),
    height = Math.min(60, h - 2 * m);
  const position = placement(d, width, height);
  return {
    ...defaultStyle,
    id: crypto.randomUUID(),
    type: "table",
    x_mm: position.x,
    y_mm: position.y,
    width_mm: width,
    height_mm: height,
    column_widths: Array(3).fill(width / 3),
    row_heights: Array(3).fill(height / 3),
    cells: Array.from({ length: 9 }, (_, i) =>
      newCell(Math.floor(i / 3), i % 3),
    ),
    padding_mm: 1.5,
    draw_borders: true,
  };
}
export function resizeElement(
  e: PaperElement,
  width: number,
  height: number,
): PaperElement {
  return e.type === "text"
    ? { ...e, width_mm: width, height_mm: height }
    : {
        ...e,
        width_mm: width,
        height_mm: height,
        column_widths: e.column_widths.map((v) => (v * width) / e.width_mm),
        row_heights: e.row_heights.map((v) => (v * height) / e.height_mm),
      };
}
export function cellBox(table: Table, c: Cell) {
  return {
    x: table.column_widths.slice(0, c.col).reduce((a, b) => a + b, 0),
    y: table.row_heights.slice(0, c.row).reduce((a, b) => a + b, 0),
    width: table.column_widths
      .slice(c.col, c.col + c.colspan)
      .reduce((a, b) => a + b, 0),
    height: table.row_heights
      .slice(c.row, c.row + c.rowspan)
      .reduce((a, b) => a + b, 0),
  };
}
export function mergeCells(t: Table, selected: Cell[]): Table {
  if (selected.length < 2)
    throw new Error("请按住 Shift 选择矩形区域中的多个单元格");
  const r0 = Math.min(...selected.map((c) => c.row)),
    c0 = Math.min(...selected.map((c) => c.col));
  const r1 = Math.max(...selected.map((c) => c.row + c.rowspan)),
    c1 = Math.max(...selected.map((c) => c.col + c.colspan));
  const covered = t.cells.filter(
    (c) =>
      c.row < r1 &&
      c.row + c.rowspan > r0 &&
      c.col < c1 &&
      c.col + c.colspan > c0,
  );
  if (
    covered.some(
      (c) =>
        c.row < r0 ||
        c.col < c0 ||
        c.row + c.rowspan > r1 ||
        c.col + c.colspan > c1,
    )
  )
    throw new Error("选区必须完整覆盖合并单元格");
  const nonempty = covered.filter((c) => c.text.trim());
  if (nonempty.length > 1)
    throw new Error("只能合并最多一个单元格有文字的区域");
  return {
    ...t,
    cells: [
      ...t.cells.filter((c) => !covered.includes(c)),
      {
        ...newCell(r0, c0),
        ...(nonempty[0]
          ? {
              text: nonempty[0].text,
              align: nonempty[0].align,
              valign: nonempty[0].valign,
            }
          : {}),
        rowspan: r1 - r0,
        colspan: c1 - c0,
      },
    ],
  };
}
export function splitCell(t: Table, selected: Cell): Table {
  const cells = t.cells.filter((c) => c !== selected);
  for (let r = selected.row; r < selected.row + selected.rowspan; r++)
    for (let c = selected.col; c < selected.col + selected.colspan; c++)
      cells.push({
        ...newCell(r, c),
        ...(r === selected.row && c === selected.col
          ? {
              text: selected.text,
              align: selected.align,
              valign: selected.valign,
            }
          : {}),
      });
  return { ...t, cells };
}
export function changeTrack(
  t: Table,
  axis: "row" | "col",
  index: number,
  remove: boolean,
): Table {
  const sizeKey = axis === "row" ? "row_heights" : "column_widths";
  const spanKey = axis === "row" ? "rowspan" : "colspan";
  const old = t[sizeKey];
  if (remove && old.length === 1) throw new Error("至少保留一行和一列");
  if (!remove && old.length >= (axis === "row" ? 80 : 40))
    throw new Error("已达到表格行列数量上限");
  const values = remove
    ? old.filter((_, i) => i !== index)
    : [
        ...old.slice(0, index),
        old[Math.min(index, old.length - 1)],
        ...old.slice(index),
      ];
  const total = old.reduce((a, b) => a + b, 0),
    sum = values.reduce((a, b) => a + b, 0);
  const cells: Cell[] = [];
  for (const cell of t.cells) {
    const start = cell[axis],
      end = start + cell[spanKey];
    if (remove && start === index && cell[spanKey] === 1) continue;
    const next = { ...cell };
    if (remove) {
      if (start > index) next[axis]--;
      else if (start <= index && end > index) next[spanKey]--;
    } else {
      if (start >= index) next[axis]++;
      else if (end > index) next[spanKey]++;
    }
    cells.push(next);
  }
  const rowCount = axis === "row" ? values.length : t.row_heights.length;
  const colCount = axis === "col" ? values.length : t.column_widths.length;
  for (let r = 0; r < rowCount; r++)
    for (let c = 0; c < colCount; c++)
      if (
        !cells.some(
          (cell) =>
            r >= cell.row &&
            r < cell.row + cell.rowspan &&
            c >= cell.col &&
            c < cell.col + cell.colspan,
        )
      )
        cells.push(newCell(r, c));
  return { ...t, [sizeKey]: values.map((v) => (v * total) / sum), cells };
}
function finite(value: number, min: number, max: number) {
  return Number.isFinite(value) && value >= min && value <= max;
}
function string(value: unknown, max = 20000): value is string {
  return typeof value === "string" && value.length <= max;
}
export function readDocument(raw: unknown): WritingDocument {
  if (!raw || typeof raw !== "object") throw new Error("文档格式不正确");
  if (!("version" in raw)) {
    const old = raw as Record<string, unknown>;
    if (
      !string(old.body) ||
      !string(old.name, 120) ||
      !["A4", "A5"].includes(String(old.paper)) ||
      !finite(old.margin_mm as number, 0, 60)
    )
      throw new Error("旧文档格式不正确");
    const d: WritingDocument = {
      ...blank,
      name: old.name,
      paper: old.paper as "A4" | "A5",
      margin_mm: old.margin_mm as number,
      elements: [],
    };
    const e = newText(d, old.body);
    e.width_mm = pageSize(d)[0] - 2 * d.margin_mm;
    e.height_mm = pageSize(d)[1] - 2 * d.margin_mm;
    for (const key of [
      "font",
      "font_size_mm",
      "char_gap_mm",
      "line_gap_mm",
      "align",
    ] as const)
      if (old[key] !== undefined) Object.assign(e, { [key]: old[key] });
    d.elements = old.body ? [e] : [];
    return readDocument(d);
  }
  const d = raw as WritingDocument;
  if (
    d.version !== 2 ||
    !string(d.name, 120) ||
    !["A4", "A5"].includes(d.paper) ||
    !finite(d.margin_mm, 0, 60) ||
    !Array.isArray(d.elements) ||
    d.elements.length > 100
  )
    throw new Error("文档字段不正确");
  const ids = new Set<string>();
  let count = 0;
  for (const e of d.elements) {
    if (
      !e ||
      !string(e.id, 120) ||
      !e.id ||
      ids.has(e.id) ||
      !["text", "table"].includes(e.type) ||
      !Number.isFinite(e.x_mm) ||
      !Number.isFinite(e.y_mm) ||
      !finite(e.width_mm, 0.001, 297) ||
      !finite(e.height_mm, 0.001, 297) ||
      !string(e.font, 240) ||
      !finite(e.font_size_mm, 4, 40) ||
      !finite(e.char_gap_mm, 0, 20) ||
      !finite(e.line_gap_mm, 0, 30)
    )
      throw new Error("元素内容或尺寸不正确");
    ids.add(e.id);
    if (e.type === "text") {
      if (!string(e.text) || !["left", "center", "right"].includes(e.align))
        throw new Error("文本框格式不正确");
      count += e.text.length;
    } else {
      if (
        !Array.isArray(e.column_widths) ||
        !Array.isArray(e.row_heights) ||
        !e.column_widths.length ||
        !e.row_heights.length ||
        e.column_widths.length > 40 ||
        e.row_heights.length > 80 ||
        ![...e.column_widths, ...e.row_heights].every((v) =>
          finite(v, 0.001, 297),
        ) ||
        Math.abs(e.column_widths.reduce((a, b) => a + b, 0) - e.width_mm) >
          0.00001 ||
        Math.abs(e.row_heights.reduce((a, b) => a + b, 0) - e.height_mm) >
          0.00001 ||
        !finite(e.padding_mm, 0, 10) ||
        typeof e.draw_borders !== "boolean" ||
        !Array.isArray(e.cells) ||
        e.cells.length > 3200
      )
        throw new Error("表格行列尺寸不正确");
      const occupied = new Set<string>();
      for (const c of e.cells) {
        if (
          !c ||
          ![c.row, c.col, c.rowspan, c.colspan].every(Number.isInteger) ||
          c.row < 0 ||
          c.col < 0 ||
          c.rowspan < 1 ||
          c.colspan < 1 ||
          c.row + c.rowspan > e.row_heights.length ||
          c.col + c.colspan > e.column_widths.length ||
          !string(c.text) ||
          !["left", "center", "right"].includes(c.align) ||
          !["top", "center", "bottom"].includes(c.valign)
        )
          throw new Error("单元格格式不正确");
        count += c.text.length;
        for (let r = c.row; r < c.row + c.rowspan; r++)
          for (let col = c.col; col < c.col + c.colspan; col++) {
            const key = `${r}:${col}`;
            if (occupied.has(key)) throw new Error("单元格重叠");
            occupied.add(key);
          }
      }
      if (occupied.size !== e.column_widths.length * e.row_heights.length)
        throw new Error("表格单元格不完整");
    }
  }
  if (count > 20000) throw new Error("整页文字不能超过20000字符");
  return structuredClone(d);
}
