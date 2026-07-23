import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { listDocuments, type DocumentSummary } from "@/api/client";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/ui/page-header";
import { Panel } from "@/components/ui/panel";
import { docStatusTone } from "@/lib/status";

const ALL = "__all__";
const EMPTY_DOCUMENTS: DocumentSummary[] = [];
const COL_WIDTHS = ["31%", "14%", "11%", "12%", "13%", "10%", "9%"];

const columns: ColumnDef<DocumentSummary>[] = [
  {
    accessorKey: "title",
    header: "Title",
    cell: ({ row }) => (
      <Link
        to={`/documents/${row.original.doc_id}`}
        className="block truncate font-serif font-medium text-primary hover:underline"
      >
        {row.original.title}
      </Link>
    ),
  },
  {
    accessorKey: "category",
    header: "Category",
    cell: ({ row }) => (
      <span className="block truncate text-muted-foreground">{row.original.category}</span>
    ),
  },
  {
    accessorKey: "doc_type",
    header: "Type",
    cell: ({ row }) => (
      <span className="block truncate text-muted-foreground">{row.original.doc_type}</span>
    ),
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => (
      <Badge variant={docStatusTone(row.original.status)}>{row.original.status}</Badge>
    ),
  },
  {
    accessorKey: "source_index",
    header: "Source",
    cell: ({ row }) => (
      <span className="block truncate font-mono text-[11px] text-faint">
        {row.original.source_index ?? "—"}
      </span>
    ),
  },
  {
    accessorKey: "official_number",
    header: "No.",
    cell: ({ row }) => (
      <span className="block truncate font-mono text-[11.5px] text-muted-foreground">
        {row.original.official_number ?? "—"}
      </span>
    ),
  },
  {
    accessorKey: "chunk_count",
    header: () => <div className="text-right">Chunks</div>,
    cell: ({ row }) => (
      <div className="text-right font-mono">{row.original.chunk_count}</div>
    ),
  },
];

export default function CorpusList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
  });

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState(ALL);
  const [docType, setDocType] = useState(ALL);
  const [status, setStatus] = useState(ALL);
  const [sourceIndex, setSourceIndex] = useState(ALL);

  const docs = data?.documents ?? EMPTY_DOCUMENTS;

  const categories = useMemo(
    () => Array.from(new Set(docs.map((d) => d.category))).sort(),
    [docs],
  );
  const docTypes = useMemo(
    () => Array.from(new Set(docs.map((d) => d.doc_type))).sort(),
    [docs],
  );
  const statuses = useMemo(
    () => Array.from(new Set(docs.map((d) => d.status))).sort(),
    [docs],
  );
  const sourceIndexes = useMemo(
    () =>
      Array.from(new Set(docs.map((d) => d.source_index).filter((s): s is string => Boolean(s)))).sort(),
    [docs],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return docs.filter((d) => {
      if (q) {
        const inTitle = d.title.toLowerCase().includes(q);
        const inTags = d.tags.some((t) => t.toLowerCase().includes(q));
        if (!inTitle && !inTags) return false;
      }
      if (category !== ALL && d.category !== category) return false;
      if (docType !== ALL && d.doc_type !== docType) return false;
      if (status !== ALL && d.status !== status) return false;
      if (sourceIndex !== ALL && d.source_index !== sourceIndex) return false;
      return true;
    });
  }, [docs, search, category, docType, status, sourceIndex]);

  const table = useReactTable({
    data: filtered,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 15 } },
  });

  function clearFilters() {
    setSearch("");
    setCategory(ALL);
    setDocType(ALL);
    setStatus(ALL);
    setSourceIndex(ALL);
  }

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  if (error) return <p className="text-danger">Failed to load documents.</p>;

  return (
    <div className="mx-auto max-w-[1240px]">
      <PageHeader
        eyebrow="Primary sources"
        title="Corpus"
        subtitle="A curated allowlist of Philippine-law primary sources, chunked and indexed for retrieval."
      />

      <div className="mb-3.5 flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search title or tags…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <Select value={category} onValueChange={(v) => setCategory(v ?? ALL)}>
          <SelectTrigger className="w-[160px]" aria-label="Category">
            <SelectValue placeholder="Category" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All categories</SelectItem>
            {categories.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={docType} onValueChange={(v) => setDocType(v ?? ALL)}>
          <SelectTrigger className="w-[160px]" aria-label="Type">
            <SelectValue placeholder="Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All types</SelectItem>
            {docTypes.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={(v) => setStatus(v ?? ALL)}>
          <SelectTrigger className="w-[160px]" aria-label="Status">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {statuses.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sourceIndex} onValueChange={(v) => setSourceIndex(v ?? ALL)}>
          <SelectTrigger className="w-[160px]" aria-label="Source">
            <SelectValue placeholder="Source" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All sources</SelectItem>
            {sourceIndexes.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={clearFilters}>
          Clear filters
        </Button>
        <span className="ml-auto font-mono text-[12.5px] text-faint">
          {filtered.length} of {docs.length} documents
        </span>
      </div>

      <Panel>
        <Table className="table-fixed">
          <colgroup>
            {COL_WIDTHS.map((w, i) => (
              <col key={i} style={{ width: w }} />
            ))}
          </colgroup>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="bg-muted">
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id}>
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className="px-[18px] py-2.5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>

      <div className="mt-3 flex items-center justify-between">
        <span className="font-mono text-[12px] text-faint">
          Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount() || 1}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
