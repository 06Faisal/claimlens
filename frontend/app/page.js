"use client";
import { useEffect, useRef, useState } from "react";

const inr = (v) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(v));
const digits = (s) => String(s).replace(/[^\d]/g, "");
const show = (s) => (s ? new Intl.NumberFormat("en-IN").format(Number(s)) : "");
const monthsBetween = (a, b) => {
  const [y1, m1, d1] = a.split("-").map(Number), [y2, m2, d2] = b.split("-").map(Number);
  return Math.max(0, (y2 - y1) * 12 + (m2 - m1) - (d2 < d1 ? 1 : 0));
};

const VERDICT = {
  payable: ["Fully covered", "ok"],
  partially_payable: ["Partly covered", "warn"],
  rejected: ["Not covered", "bad"],
};

function Money({ id, label, hint, value, onChange, optional }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}{optional && <span className="opt"> optional</span>}</label>
      <div className="money"><span aria-hidden="true">₹</span>
        <input id={id} inputMode="numeric" autoComplete="off" placeholder="0" value={show(value)} onChange={(e) => onChange(digits(e.target.value).slice(0, 9))} />
      </div>
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

export default function Page() {
  const [samples, setSamples] = useState([]);
  const [policy, setPolicy] = useState(null); // {id, name, uploaded?}
  const [upBusy, setUpBusy] = useState(false);
  const [upErr, setUpErr] = useState("");
  const [drag, setDrag] = useState(false);
  const [f, setF] = useState({ description: "", pre: "", bought: "", renewed: "", admitted: new Date().toISOString().slice(0, 10),
    rent: "", days: "", fees: "", other: "", nonpay: "" });
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const fileRef = useRef(null);
  const resultRef = useRef(null);

  useEffect(() => { fetch("/api/samples").then((r) => r.json()).then(setSamples).catch(() => {}); }, []);
  useEffect(() => { if (res || err) resultRef.current?.focus(); }, [res, err]);

  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const setMoney = (k) => (v) => setF((s) => ({ ...s, [k]: v }));
  const total = Number(f.rent || 0) * Number(f.days || 0) + Number(f.fees || 0) + Number(f.other || 0) + Number(f.nonpay || 0);

  async function upload(file) {
    if (!file) return;
    setUpBusy(true); setUpErr(""); setRes(null);
    try {
      const body = new FormData(); body.append("file", file);
      const r = await fetch("/api/policies/upload", { method: "POST", body });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(r.status === 429 ? "Too many uploads. Please wait a minute." : r.status === 413 ? "That file is larger than 5 MB." : j.detail || "We could not read that file.");
      setPolicy({ id: j.policy_id, name: j.name, uploaded: true, chunks: j.chunks });
    } catch (x) { setUpErr(x.message || "Upload failed. Please try again."); } finally { setUpBusy(false); }
  }

  const missing = [!policy && "choose your policy", !f.description.trim() && "describe the treatment",
    !f.pre && "say whether you had the condition before buying the policy", !f.bought && "enter when you bought the policy",
    !total && "enter the bill amounts"].filter(Boolean);

  async function submit(e) {
    e.preventDefault();
    if (missing.length) { setErr(`Almost there: please ${missing[0]}.`); return; }
    setBusy(true); setErr(""); setRes(null);
    const body = {
      policy_id: policy.id, ...(policy.chunks && { policy_chunks: policy.chunks }), description: f.description, pre_existing: f.pre === "yes" ? true : f.pre === "no" ? false : null,
      policy_start_date: f.renewed || f.bought, claim_date: f.admitted, continuous_coverage_months: monthsBetween(f.bought, f.admitted),
      room_rent_per_day: Number(f.rent || 0), room_days: Number(f.days || 0), associated_charges: Number(f.fees || 0),
      other_charges: Number(f.other || 0), non_payable_charges: Number(f.nonpay || 0),
    };
    try {
      const r = await fetch("/api/assess", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (r.status === 429) throw new Error("Too many checks in a short time. Please wait a minute and try again.");
      if (r.status === 503) throw new Error("The checking service is not set up yet. Please try again later.");
      if (!r.ok) throw new Error(r.status === 422 ? "Some details look wrong. Please check the dates and amounts." : "Something went wrong on our side. Please try again.");
      setRes(await r.json());
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  }

  const v = res?.status === "answered" ? VERDICT[res.verdict] : null;
  const pay = res ? Number(res.payable) : 0, claimed = res ? Number(res.claimed) : 0;
  const clean = (n) => n.replace(/ \(.*\)/, "");

  return (
    <>
      <header className="top"><div className="wrap">
        <span className="brand">ClaimLens</span>
        <span className="tag">Check a health insurance claim before you file it</span>
      </div></header>

      <main className="wrap layout">
        <form onSubmit={submit} noValidate>
          <h1>What will your insurer pay?</h1>
          <p className="lede">Answer three short sections. We read your policy, apply its rules and show exactly where each number comes from.</p>

          <section className="step" aria-labelledby="s1">
            <h2 id="s1"><span className="n">1</span>Your policy</h2>
            {policy ? (
              <div className="chosen"><div><strong>{policy.name}</strong><span className="hint">{policy.uploaded ? "Your uploaded file" : "Sample policy"}</span></div>
                <button type="button" className="link" onClick={() => { setPolicy(null); setRes(null); }}>Change</button></div>
            ) : (
              <>
                <div className={`drop ${drag ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
                  onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files[0]); }}>
                  <p className="big">{upBusy ? "Reading your policy…" : "Drop your policy document here"}</p>
                  <p className="hint">PDF, Word (.docx) or text file, up to 5 MB</p>
                  <button type="button" className="secondary" disabled={upBusy} onClick={() => fileRef.current.click()}>Choose a file</button>
                  <input ref={fileRef} type="file" hidden accept=".pdf,.docx,.txt,.md,application/pdf" onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
                </div>
                {upErr && <p role="alert" className="error">{upErr}</p>}
                <p className="hint">Your file is read once and kept only in this browser tab. Our server does not store it.</p>
                {samples.length > 0 && (
                  <p className="samples">No document handy? Try a sample:{" "}
                    {samples.map((s) => <button type="button" key={s.id} className="chip" onClick={() => setPolicy({ id: s.id, name: clean(s.name) })}>{clean(s.name)}</button>)}</p>
                )}
              </>
            )}
          </section>

          <section className="step" aria-labelledby="s2">
            <h2 id="s2"><span className="n">2</span>The treatment</h2>
            <div className="field">
              <label htmlFor="desc">What was the treatment or illness?</label>
              <textarea id="desc" maxLength={2000} placeholder="For example: knee replacement surgery for arthritis" value={f.description} onChange={set("description")} />
            </div>
            <fieldset className="field">
              <legend>Did you already have this condition before you bought the policy?</legend>
              <div className="seg">
                {[["no", "No"], ["yes", "Yes"], ["unsure", "Not sure"]].map(([k, l]) => (
                  <label key={k} className={f.pre === k ? "on" : ""}><input type="radio" name="pre" value={k} checked={f.pre === k} onChange={set("pre")} />{l}</label>
                ))}
              </div>
            </fieldset>
            <div className="two">
              <div className="field"><label htmlFor="bought">When did you first buy this policy?</label><input id="bought" type="date" max={f.admitted} value={f.bought} onChange={set("bought")} />
                <p className="hint">Your insurer’s welcome letter shows this date.</p></div>
              <div className="field"><label htmlFor="adm">Date you were admitted</label><input id="adm" type="date" value={f.admitted} onChange={set("admitted")} /></div>
            </div>
          </section>

          <section className="step" aria-labelledby="s3">
            <h2 id="s3"><span className="n">3</span>The hospital bill</h2>
            <p className="hint top0">Copy these from the final bill. Rough numbers are fine for an estimate.</p>
            <div className="two">
              <Money id="rent" label="Room rent per day" value={f.rent} onChange={setMoney("rent")} />
              <div className="field"><label htmlFor="days">Number of days in the room</label>
                <input id="days" inputMode="numeric" placeholder="0" value={f.days} onChange={(e) => setMoney("days")(digits(e.target.value).slice(0, 3))} /></div>
            </div>
            <Money id="fees" label="Doctor, nursing and surgeon fees" hint="Also operation theatre and consultation charges." value={f.fees} onChange={setMoney("fees")} />
            <Money id="other" label="Medicines, implants and tests" hint="Pharmacy, scans, blood tests, stents, knee implants." value={f.other} onChange={setMoney("other")} />
            <button type="button" className="link" aria-expanded={more} onClick={() => setMore(!more)}>{more ? "Hide" : "More details"} (optional)</button>
            {more && <div className="more">
              <Money id="nonpay" label="Items insurers never pay for" hint="Gloves, admission fees, visitor meals. Your bill may list these as “non-medical”." value={f.nonpay} onChange={setMoney("nonpay")} optional />
              <div className="field"><label htmlFor="renew">Latest renewal date</label><input id="renew" type="date" max={f.admitted} value={f.renewed} onChange={set("renewed")} />
                <p className="hint">Leave empty if you have not renewed yet.</p></div>
            </div>}
            <p className="total"><span>Total bill</span><strong>{inr(total)}</strong></p>
          </section>

          <button className="go" disabled={busy || upBusy}>{busy ? "Checking your policy…" : "See what I can claim"}</button>
        </form>

        <aside className="result" ref={resultRef} tabIndex={-1} aria-live="polite">
          {busy && <div className="card"><p className="big">Reading your policy and checking every rule…</p><p className="hint">This usually takes 10 to 20 seconds.</p></div>}
          {err && !busy && <div className="card bad-card" role="alert"><p>{err}</p></div>}
          {v && !busy && (
            <div className="card">
              <span className={`pill ${v[1]}`}>{v[0]}</span>
              <p className="label">Your insurer should pay about</p>
              <p className="amount">{inr(pay)}</p>
              <p className="sub">of {inr(claimed)} billed. You pay about {inr(claimed - pay)}.</p>
              <div className="bar" role="img" aria-label={`Insurer pays ${inr(pay)}, you pay ${inr(claimed - pay)}`}><i style={{ width: `${claimed ? (pay / claimed) * 100 : 0}%` }} /></div>
              <p>{res.explanation}</p>
              <h3>How we worked it out</h3>
              <ol className="steps">{res.steps.map((s, i) => <li key={i}><div><strong>{s.name}</strong><span>{s.note}</span></div><b>{inr(s.amount_after)}</b></li>)}</ol>
              <details><summary>Where this comes from in your policy</summary>
                {res.citations.map((c) => <blockquote key={c.clause_id}><strong>{c.title}</strong>{c.text}</blockquote>)}</details>
            </div>
          )}
          {res?.status === "abstained" && !busy && (
            <div className="card"><span className="pill warn">We are not sure</span>
              <p className="big">We could not give you a reliable number.</p>
              <p>{res.reason}</p>
              <p className="hint">We would rather say so than guess. Your insurer’s helpline or a claims adviser can confirm.</p></div>
          )}
          {!res && !err && !busy && <div className="card quiet"><p className="big">Your estimate will appear here.</p>
            <p className="hint">We show the amount, the reasoning in plain steps and the exact lines of your policy used.</p></div>}
          <p className="note">{res?.disclaimer || "Informational estimate only. Not legal or financial advice. Your insurer’s decision and your policy wording prevail."}</p>
        </aside>
      </main>
    </>
  );
}
