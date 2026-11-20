function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|");
}

function isSeparator(line: string): boolean {
  return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line.trim());
}

function renderTable(rows: string[]): string {
  const body = rows.filter((row) => !isSeparator(row));
  if (body.length < 2) return body.map((row) => `<p>${row}</p>`).join("");
  const header = tableCells(body[0]);
  const records = body.slice(1).map(tableCells);
  const head = header.map((cell) => `<th>${cell}</th>`).join("");
  const rendered = records
    .map((cells) => `<tr>${header.map((_, index) => `<td>${cells[index] || "—"}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="answer-table-wrap"><table class="answer-table"><thead><tr>${head}</tr></thead><tbody>${rendered}</tbody></table></div>`;
}

function formatAnswer(source: string): string {
  const cleaned = (source || "")
    .replace(/\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/ +([,.;:!?])/g, "$1");
  const escaped = cleaned
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const blocks: string[] = [];
  let list: string[] = [];
  let table: string[] = [];
  const flushList = () => {
    if (!list.length) return;
    blocks.push(`<ul>${list.map((item) => `<li>${item}</li>`).join("")}</ul>`);
    list = [];
  };
  const flushTable = () => {
    if (!table.length) return;
    blocks.push(renderTable(table));
    table = [];
  };
  for (const raw of escaped.split("\n")) {
    const line = raw.trim();
    if (isTableRow(line) || (table.length && isSeparator(line))) {
      flushList();
      table.push(line);
      continue;
    }
    flushTable();
    const bullet = line.match(/^(?:\*|-)+\s+(.*)$/);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }
    flushList();
    if (line) blocks.push(`<p>${line}</p>`);
  }
  flushTable();
  flushList();
  return blocks.join("");
}

export function RichText({ text }: { text: string }) {
  return <div className="rich-answer" dangerouslySetInnerHTML={{ __html: formatAnswer(text || "") }} />;
}
