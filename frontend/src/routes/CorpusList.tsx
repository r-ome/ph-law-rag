import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
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

const ALL = "__all__";

function statusVariant(status: string): "default" | "secondary" | "outline" {
  if (status === "operative") return "default";
  if (status === "unknown") return "outline";
  return "secondary";
}

const columns: ColumnDef<DocumentSummary>[] = [
  {
    accessorKey: "title",
    header: "Title",
    cell: ({ row }) => (
      <Link to={`/documents/${row.original.doc_id}`} className="font-medium hover:underline">
        {row.original.title}
      </Link>
    ),
  },
  { accessorKey: "category", header: "Category" },
  { accessorKey: "doc_type", header: "Type" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => (
      <Badge variant={statusVariant(row.original.status)}>{row.original.status}</Badge>
    ),
  },
  { accessorKey: "source_index", header: "Source" },
  {
    accessorKey: "official_number",
    header: "No.",
    cell: ({ row }) => row.original.official_number ?? "—",
  },
  {
    accessorKey: "chunk_count",
    header: () => <div className="text-right">Chunks</div>,
    cell: ({ row }) => <div className="text-right">{row.original.chunk_count}</div>,
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

  const docs = data?.documents ?? [];

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
  });

  function clearFilters() {
    setSearch("");
    setCategory(ALL);
    setDocType(ALL);
    setStatus(ALL);
    setSourceIndex(ALL);
  }

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p className="text-red-600">Failed to load documents.</p>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
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
      </div>

      <p className="text-sm text-muted-foreground">
        {filtered.length} of {docs.length} documents
      </p>

      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
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
                <TableCell key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
