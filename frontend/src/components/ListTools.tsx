import { ReactNode, useMemo, useState } from "react";

export function useListView<T>(
  items: T[],
  search: (item: T) => string,
  compare: (a: T, b: T) => number,
  pageSize = 25,
) {
  const [query, setQuery] = useState("");
  const [descending, setDescending] = useState(true);
  const [page, setPage] = useState(1);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = needle ? items.filter((item) => search(item).toLowerCase().includes(needle)) : [...items];
    rows.sort((a, b) => (descending ? -1 : 1) * compare(a, b));
    return rows;
  }, [items, query, descending, search, compare]);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pages);
  return {
    rows: filtered.slice((safePage - 1) * pageSize, safePage * pageSize),
    query,
    setQuery: (value: string) => { setQuery(value); setPage(1); },
    descending,
    toggleDirection: () => setDescending((value) => !value),
    page: safePage,
    pages,
    total: filtered.length,
    previous: () => setPage((value) => Math.max(1, value - 1)),
    next: () => setPage((value) => Math.min(pages, value + 1)),
  };
}

export function ListToolbar({
  query,
  onQuery,
  placeholder = "Search register...",
  descending,
  onDirection,
  exportPath,
  page,
  pages,
  total,
  previous,
  next,
  filters,
}: {
  query: string;
  onQuery: (value: string) => void;
  placeholder?: string;
  descending: boolean;
  onDirection: () => void;
  exportPath?: string;
  page: number;
  pages: number;
  total: number;
  previous: () => void;
  next: () => void;
  filters?: ReactNode;
}) {
  return (
    <div className="list-toolbar">
      <div className="list-search">
        <span aria-hidden="true">S</span>
        <input value={query} onChange={(event) => onQuery(event.target.value)} placeholder={placeholder} aria-label={placeholder} />
      </div>
      {filters}
      <button className="btn sm" type="button" onClick={onDirection}>
        {descending ? "Newest first" : "Oldest first"}
      </button>
      {exportPath && <a className="btn sm" href={`/api${exportPath}`} download>Export CSV</a>}
      <div className="pager">
        <span>{total} records</span>
        <button type="button" onClick={previous} disabled={page <= 1} aria-label="Previous page">Prev</button>
        <b>{page} / {pages}</b>
        <button type="button" onClick={next} disabled={page >= pages} aria-label="Next page">Next</button>
      </div>
    </div>
  );
}

export function PdfLink({ path, children = "Print PDF" }: { path: string; children?: ReactNode }) {
  return <a className="btn sm" href={`/api${path}`} target="_blank" rel="noreferrer">{children}</a>;
}
