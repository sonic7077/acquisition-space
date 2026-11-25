import { useEffect, useState } from "react";
import { api, type Row } from "./api";
export type Ctx = {
  me: Row;
  ips: Row[];
  ip: string;
  version: number;
  run: (fn: () => Promise<any>, success?: string) => Promise<any>;
  go: (p: string, selectedId?: string) => void;
  notify: (s: string) => void;
};
export function useRows(path: string, version: number) {
  const [rows, setRows] = useState<Row[]>([]);
  useEffect(() => {
    let active = true;
    api<Row[]>(path)
      .then((d) => {
        if (active) setRows(d);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [path, version]);
  return rows;
}
export function filterIp(rows: Row[], ip: string) {
  return ip ? rows.filter((r) => r.ip_id === ip) : rows;
}
export function verifiedKnowledge(rows: Row[]) {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
  return rows.filter((row) => row.status === "verified" && (!row.valid_until || row.valid_until >= today));
}
export function IpSelect({
  ips,
  value,
  onChange,
}: {
  ips: Row[];
  value: string;
  onChange: (s: string) => void;
}) {
  return (
    <select
      aria-label="目标IP"
      required
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">请选择目标IP</option>
      {ips.map((i) => (
        <option key={i.id} value={i.id}>
          {i.name}
        </option>
      ))}
    </select>
  );
}
