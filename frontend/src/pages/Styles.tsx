import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { SizeRange, Style } from "../api/types";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { titled } from "../lib/format";

const GENDERS = ["mens", "womens", "boys", "girls", "unisex"];

export default function Styles() {
  const { data, loading, reload } = useAsync(() => api.get<Style[]>("/styles"));
  const [open, setOpen] = useState(false);
  return (
    <div>
      <PageHeader
        eyebrow="Module 1.7 – 1.10"
        title="Styles & BOM"
        subtitle="Each style carries approved colourways and a versioned, per-size bill of materials — the recipe the whole factory runs on."
        actions={<button className="btn primary" onClick={() => setOpen(true)}>+ New style</button>}
      />
      <Card>
        {loading ? <Spinner /> : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Style №</th><th>Description</th><th>Gender</th><th>Status</th><th className="num">SAM</th><th></th></tr></thead>
              <tbody>
                {data?.map((s) => (
                  <tr key={s.id} className="clickable">
                    <td className="code"><Link to={`/styles/${s.id}`}>{s.style_number}</Link></td>
                    <td>{s.description}</td>
                    <td>{titled(s.gender)}</td>
                    <td><Chip status={s.status} /></td>
                    <td className="num">{s.standard_sam ? parseFloat(s.standard_sam).toFixed(1) : "—"}</td>
                    <td className="right"><Link to={`/styles/${s.id}`} className="btn ghost sm">Open →</Link></td>
                  </tr>
                ))}
                {!data?.length && <tr><td colSpan={6} className="muted">No styles yet.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {open && <StyleForm onClose={() => setOpen(false)} onDone={() => { setOpen(false); reload(); }} />}
    </div>
  );
}

function StyleForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const ranges = useAsync(() => api.get<SizeRange[]>("/masters/size-ranges"));
  const [f, setF] = useState({ style_number: "", description: "", size_range_id: 0, gender: "mens", standard_sam: "12.5" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: string, v: any) => setF({ ...f, [k]: v });

  async function save() {
    setSaving(true); setError(null);
    try {
      await api.post("/styles", { ...f, size_range_id: Number(f.size_range_id), standard_sam: f.standard_sam || null });
      toast.push("Style created");
      onDone();
    } catch (e) {
      const m = e instanceof ApiError ? e.message : "Failed";
      setError(m); toast.push("Could not save", { detail: m, bad: true });
    } finally { setSaving(false); }
  }

  return (
    <Drawer title="New style" sub="Module 1.7" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !f.style_number || !f.size_range_id} onClick={save}>Save style</button></>}>
      {error && <ErrorBox message={error} />}
      <div className="form-row two">
        <Field label="Style number" required><input className="input mono" value={f.style_number} onChange={(e) => set("style_number", e.target.value.toUpperCase())} placeholder="TS-100" /></Field>
        <Field label="Standard SAM"><input className="input mono" value={f.standard_sam} onChange={(e) => set("standard_sam", e.target.value)} /></Field>
      </div>
      <Field label="Description" required><input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} placeholder="Crew Neck Tee" /></Field>
      <div className="form-row two">
        <Field label="Size range" required>
          <select className="select" value={f.size_range_id} onChange={(e) => set("size_range_id", e.target.value)}>
            <option value={0}>Select…</option>
            {ranges.data?.map((r) => <option key={r.id} value={r.id}>{r.code} — {r.name}</option>)}
          </select>
        </Field>
        <Field label="Gender">
          <select className="select" value={f.gender} onChange={(e) => set("gender", e.target.value)}>
            {GENDERS.map((g) => <option key={g} value={g}>{titled(g)}</option>)}
          </select>
        </Field>
      </div>
    </Drawer>
  );
}
