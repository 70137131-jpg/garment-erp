import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { BomVersion, Colour, Colourway, Material, SizeRange, Style } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { pct, qty } from "../lib/format";

export default function StyleDetail() {
  const { can } = useAuthorization();
  const { id } = useParams();
  const sid = Number(id);
  const style = useAsync(() => api.get<Style>(`/styles/${sid}`), [sid]);
  const colours = useAsync(() => api.get<Colour[]>("/masters/colours"));
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"));
  const cways = useAsync(() => api.get<Colourway[]>(`/styles/${sid}/colourways`), [sid]);
  const boms = useAsync(() => api.get<BomVersion[]>(`/styles/${sid}/bom-versions`), [sid]);
  const ranges = useAsync(() => api.get<SizeRange[]>("/masters/size-ranges"));
  const toast = useToast();

  const [cwOpen, setCwOpen] = useState(false);
  const [bomOpen, setBomOpen] = useState(false);

  if (style.loading) return <Spinner />;
  if (!style.data) return <div className="err">Style not found.</div>;
  const s = style.data;
  const sizeRange = ranges.data?.find((r) => r.id === s.size_range_id);
  const colourName = (cid: number) => colours.data?.find((c) => c.id === cid)?.name ?? `#${cid}`;
  const materialName = (mid: number) => materials.data?.find((m) => m.id === mid)?.name ?? `#${mid}`;

  async function approveLabDip(cwId: number) {
    try {
      await api.post(`/styles/colourways/${cwId}/approve-lab-dip`);
      toast.push("Lab dip approved");
      cways.reload();
    } catch (e) {
      toast.push("Failed", { detail: (e as ApiError).message, bad: true });
    }
  }
  async function approveBom(bomId: number) {
    try {
      await api.post(`/styles/bom-versions/${bomId}/approve`);
      toast.push("BOM version approved");
      boms.reload();
    } catch (e) {
      toast.push("Failed", { detail: (e as ApiError).message, bad: true });
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow={<Link to="/styles" style={{ color: "var(--madder)" }}>← Styles</Link>}
        title={`${s.style_number} · ${s.description}`}
        subtitle={sizeRange ? `Size range ${sizeRange.code}: ${sizeRange.sizes.map((z) => z.label).join(" · ")}` : undefined}
        actions={<Chip status={s.status} />}
      />

      <div className="grid cols-2">
        <Card title="Colourways" hint="Module 1.8"
          actions={can("merchandiser") ? <button className="btn primary sm" onClick={() => setCwOpen(true)}>+ Add</button> : undefined}>
          {cways.loading ? <Spinner /> : (
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Colour</th><th>Buyer ref</th><th>Lab dip</th><th></th></tr></thead>
                <tbody>
                  {cways.data?.map((cw) => (
                    <tr key={cw.id}>
                      <td>
                        <span className="swatch" style={{ background: colours.data?.find((c) => c.id === cw.colour_id)?.hex || "#ccc" }} />{" "}
                        {colourName(cw.colour_id)}
                      </td>
                      <td className="mono muted">{cw.buyer_reference || "—"}</td>
                      <td>{cw.lab_dip_approved ? <Chip tone="ok" label="Approved" /> : <Chip tone="warn" label="Pending" />}</td>
                      <td className="right">{can("quality_inspector", "merchandiser") && !cw.lab_dip_approved && <button className="btn sm" onClick={() => approveLabDip(cw.id)}>Approve</button>}</td>
                    </tr>
                  ))}
                  {!cways.data?.length && <tr><td colSpan={4} className="muted">No colourways.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Standard details">
          <div className="card-pad">
            <dl className="kv">
              <dt>Style №</dt><dd className="mono">{s.style_number}</dd>
              <dt>Gender</dt><dd>{s.gender}</dd>
              <dt>Status</dt><dd><Chip status={s.status} /></dd>
              <dt>Standard SAM</dt><dd>{s.standard_sam ? `${parseFloat(s.standard_sam).toFixed(2)} min` : "—"}</dd>
              <dt>Size range</dt><dd>{sizeRange?.name ?? "—"}</dd>
            </dl>
          </div>
        </Card>
      </div>

      <div className="section-title">Bill of Materials — versioned</div>
      <Card actions={can("merchandiser") ? <button className="btn primary sm" onClick={() => setBomOpen(true)}>+ New BOM version</button> : undefined} title="BOM versions" hint="approving supersedes the prior — history preserved">
        {boms.loading ? <Spinner /> : (
          <div style={{ padding: boms.data?.length ? 0 : undefined }}>
            {!boms.data?.length && <div className="empty"><div className="big">No BOM yet</div>Create the first per-size recipe for this style.</div>}
            {boms.data?.map((b) => (
              <div key={b.id} style={{ borderBottom: "1px solid var(--line)" }}>
                <div className="flex-between" style={{ padding: "12px 18px" }}>
                  <div className="flex">
                    <span className="mono" style={{ fontWeight: 600 }}>v{b.version_no}</span>
                    <Chip status={b.status} />
                    {b.approved_by && <span className="mono muted" style={{ fontSize: 11 }}>by {b.approved_by}</span>}
                  </div>
                  {can("merchandiser") && b.status === "draft" && <button className="btn sm" onClick={() => approveBom(b.id)}>Approve</button>}
                </div>
                <div className="table-wrap" style={{ padding: "0 18px 14px" }}>
                  <table className="tbl">
                    <thead><tr><th>Material</th><th className="num">Wastage</th><th>Per-size consumption (base UoM)</th></tr></thead>
                    <tbody>
                      {b.lines.map((ln) => (
                        <tr key={ln.id}>
                          <td>{materialName(ln.material_id)}</td>
                          <td className="num">{pct((parseFloat(ln.wastage_pct) * 100).toString())}</td>
                          <td>
                            <div className="tag-list">
                              {ln.size_consumption.map((sc) => (
                                <span key={sc.size_label} className="chip neutral">{sc.size_label}: {qty(sc.consumption)}</span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {cwOpen && (
        <ColourwayForm styleId={sid} colours={colours.data ?? []} onClose={() => setCwOpen(false)}
          onDone={() => { setCwOpen(false); cways.reload(); }} />
      )}
      {bomOpen && sizeRange && (
        <BomForm styleId={sid} sizeRange={sizeRange} materials={materials.data ?? []} colours={colours.data ?? []}
          onClose={() => setBomOpen(false)} onDone={() => { setBomOpen(false); boms.reload(); }} />
      )}
    </div>
  );
}

function ColourwayForm({ styleId, colours, onClose, onDone }: { styleId: number; colours: Colour[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [colourId, setColourId] = useState(0);
  const [ref, setRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true); setError(null);
    try {
      await api.post(`/styles/${styleId}/colourways`, { colour_id: Number(colourId), buyer_reference: ref || null });
      toast.push("Colourway added"); onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }
  return (
    <Drawer title="Add colourway" sub="Module 1.8" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !colourId} onClick={save}>Add</button></>}>
      {error && <ErrorBox message={error} />}
      <Field label="Colour" required>
        <select className="select" value={colourId} onChange={(e) => setColourId(Number(e.target.value))}>
          <option value={0}>Select…</option>
          {colours.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
        </select>
      </Field>
      <Field label="Buyer reference"><input className="input mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="BR-NAVY" /></Field>
    </Drawer>
  );
}

function BomForm({ styleId, sizeRange, materials, colours, onClose, onDone }: {
  styleId: number; sizeRange: SizeRange; materials: Material[]; colours: Colour[]; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [materialId, setMaterialId] = useState(0);
  const [wastage, setWastage] = useState("0.05");
  const [cons, setCons] = useState<Record<string, string>>(Object.fromEntries(sizeRange.sizes.map((z) => [z.label, ""])));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true); setError(null);
    try {
      const size_consumption = sizeRange.sizes
        .filter((z) => cons[z.label] !== "" && cons[z.label] != null)
        .map((z) => ({ size_label: z.label, consumption: cons[z.label] }));
      await api.post(`/styles/${styleId}/bom-versions`, {
        lines: [{ material_id: Number(materialId), wastage_pct: wastage, size_consumption }],
      });
      toast.push("BOM version created"); onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  return (
    <Drawer title="New BOM version" sub="Module 1.9 · per-size consumption" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !materialId} onClick={save}>Create draft</button></>}>
      {error && <ErrorBox message={error} />}
      <div className="form-row two">
        <Field label="Material" required>
          <select className="select" value={materialId} onChange={(e) => setMaterialId(Number(e.target.value))}>
            <option value={0}>Select…</option>
            {materials.map((m) => <option key={m.id} value={m.id}>{m.code} — {m.name}</option>)}
          </select>
        </Field>
        <Field label="Wastage" hint="fraction, e.g. 0.05 = 5%"><input className="input mono" value={wastage} onChange={(e) => setWastage(e.target.value)} /></Field>
      </div>
      <Field label="Per-size consumption" hint="base UoM per garment; blank = 0">
        <div className="matrix">
          <table>
            <thead><tr>{sizeRange.sizes.map((z) => <th key={z.label}>{z.label}</th>)}</tr></thead>
            <tbody>
              <tr>
                {sizeRange.sizes.map((z) => (
                  <td key={z.label}><input value={cons[z.label] ?? ""} onChange={(e) => setCons({ ...cons, [z.label]: e.target.value })} placeholder="0" /></td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </Field>
      <div className="hintline">The BOM is created as a draft. Approve it to make it the style's live recipe (superseding any prior).</div>
    </Drawer>
  );
}
