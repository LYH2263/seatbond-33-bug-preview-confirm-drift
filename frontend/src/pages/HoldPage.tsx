import { useEffect, useState } from "react";
import { api } from "../api/client";

type Show = { id: number; film_title: string; hall_name?: string };
type Hold = {
  id: number;
  order_code: string;
  row: number;
  start_col: number;
  end_col: number;
  party_size: number;
};
type Precheck = {
  token: string;
  showtime_id: number;
  row: number;
  start_col: number;
  end_col: number;
  party_size: number;
  expires_at: string;
};

function fmtExpiry(iso: string) {
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleTimeString();
}

export default function HoldPage() {
  const [shows, setShows] = useState<Show[]>([]);
  const [sid, setSid] = useState<number | "">("");
  const [party, setParty] = useState(3);
  const [prefRow, setPrefRow] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [pre, setPre] = useState<Precheck | null>(null);
  const [last, setLast] = useState<Hold | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Show[]>("/showtimes").then((s) => {
      setShows(s);
      if (s[0]) setSid(s[0].id);
    });
  }, []);

  // 输入一变，旧预检坐标即失效，必须重新预检
  function invalidate() {
    setPre(null);
  }

  async function precheck() {
    setMsg("");
    setErr("");
    setLast(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = { showtime_id: sid, party_size: party };
      if (prefRow) body.preferred_row = Number(prefRow);
      const p = await api<Precheck>("/holds/precheck", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setPre(p);
    } catch (e) {
      setPre(null);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!pre) return;
    setMsg("");
    setErr("");
    setBusy(true);
    try {
      const hold = await api<Hold>("/holds/confirm", {
        method: "POST",
        body: JSON.stringify({ token: pre.token }),
      });
      setPre(null);
      setLast(hold);
      setMsg(`已锁座 ${hold.order_code}：第${hold.row}排 ${hold.start_col}-${hold.end_col}`);
    } catch (e) {
      // 令牌过期或座位被占：丢弃预检结果，要求重新预检
      setPre(null);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h2>锁座</h2>
      <div className="toolbar">
        <select
          value={sid}
          onChange={(e) => {
            setSid(Number(e.target.value));
            invalidate();
          }}
        >
          {shows.map((s) => (
            <option key={s.id} value={s.id}>
              {s.film_title} · {s.hall_name}
            </option>
          ))}
        </select>
        <label>
          人数{" "}
          <input
            type="number"
            min={1}
            max={12}
            value={party}
            onChange={(e) => {
              setParty(Number(e.target.value));
              invalidate();
            }}
            style={{ width: 72 }}
          />
        </label>
        <label>
          优先排{" "}
          <input
            value={prefRow}
            onChange={(e) => {
              setPrefRow(e.target.value);
              invalidate();
            }}
            placeholder="可选"
            style={{ width: 72 }}
          />
        </label>
        <button onClick={precheck} disabled={busy || sid === ""}>
          预检连座
        </button>
      </div>

      {pre && (
        <div className="toolbar" style={{ border: "1px dashed #5a4a30", padding: ".6rem" }}>
          <span className="mono">
            预检结果：第{pre.row}排 {pre.start_col}-{pre.end_col} 列 · {pre.party_size} 人 · 令牌{" "}
            {fmtExpiry(pre.expires_at)} 前有效
          </span>
          <button onClick={confirm} disabled={busy}>
            确认锁座
          </button>
          <button
            onClick={() => setPre(null)}
            disabled={busy}
            style={{ background: "#3a2a20", color: "var(--text)" }}
          >
            取消
          </button>
        </div>
      )}

      {msg && <div className="ok">{msg}</div>}
      {err && <div className="err">{err}</div>}
      {last && (
        <p className="mono">
          订单 {last.order_code} · {last.party_size} 人 · R{last.row} C{last.start_col}-
          {last.end_col}
        </p>
      )}
    </>
  );
}
