import { ArrowLeft, Building2 } from "lucide-react";
import { Link, useParams } from "react-router";
import { FlatCard, themeFor } from "../components/FlatCard";
import { cx, ErrorNote, Loading, MonthPicker, PageHeader } from "../components/ui";
import { qs } from "../lib/api";
import { useApi, usePeriod } from "../lib/hooks";
import { formatINR, formatPeriod } from "../lib/money";
import type { Building } from "../lib/types";

interface BuildingsResp {
  period: string;
  buildings: Building[];
}

export function BuildingChoice({ buildings, period }: { buildings: Building[]; period: string }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {buildings.map((b) => {
        const t = themeFor(b.code);
        const occupied = b.flats.filter((f) => f.occupancy === "tenant_occupied").length;
        const outstanding = b.flats.reduce((s, f) => s + f.period_outstanding_paise, 0);
        return (
          <Link key={b.id} to={`/buildings/${b.code}?month=${period}`}
            className={cx("flex items-center gap-4 rounded-2xl border p-5 shadow-sm transition hover:shadow-md", t.soft, t.border)}>
            <BuildingGlyph building={b} />
            <div>
              <div className={cx("text-xl font-semibold", t.accent)}>{b.name}</div>
              <div className="mt-1 text-sm text-stone-700">
                {b.flats.length} flats · {occupied} let
              </div>
              <div className="mt-0.5 text-sm text-stone-700">
                Outstanding {formatPeriod(period, true)}: <span className="money font-medium">{formatINR(outstanding)}</span>
              </div>
            </div>
          </Link>
        );
      })}
    </div>
  );
}

/** Tiny schematic of the building: one row per floor, one block per flat. */
function BuildingGlyph({ building }: { building: Building }) {
  const floors = groupByFloor(building).reverse();
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-white/60 bg-white/70 p-1.5" aria-hidden>
      {floors.map(([floor, flats]) => (
        <div key={floor} className="flex gap-0.5">
          {flats.map((f) => (
            <span key={f.id} className={cx("h-3.5 rounded-[2px]", flats.length === 1 ? "w-[30px]" : "w-3.5",
              f.period_outstanding_paise > 0 ? "bg-red-400" : f.occupancy === "tenant_occupied" ? themeFor(building.code).bar : "bg-stone-300")} />
          ))}
        </div>
      ))}
    </div>
  );
}

function groupByFloor(b: Building): [number, Building["flats"]][] {
  const map = new Map<number, Building["flats"]>();
  for (const f of b.flats) map.set(f.floor_index, [...(map.get(f.floor_index) ?? []), f]);
  return [...map.entries()].sort((a, c) => a[0] - c[0]).map(([k, v]) => [k, v.sort((x, y) => x.position - y.position)]);
}

export function BuildingsPage() {
  const [period, setPeriod] = usePeriod();
  const { data, error, loading } = useApi<BuildingsResp>(`/api/buildings${qs({ period })}`);
  return (
    <>
      <PageHeader title="Buildings" subtitle="Choose a building to see its flats."
        actions={<MonthPicker value={period} onChange={setPeriod} />} />
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : data && <BuildingChoice buildings={data.buildings} period={period} />}
    </>
  );
}

export function BuildingPage() {
  const { code } = useParams();
  const [period, setPeriod] = usePeriod();
  const { data, error, loading } = useApi<BuildingsResp>(`/api/buildings${qs({ period })}`);
  const b = data?.buildings.find((x) => x.code === code);
  const t = themeFor(code ?? "");
  return (
    <>
      <Link to={`/buildings?month=${period}`} className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink no-print">
        <ArrowLeft className="size-4" /> All buildings
      </Link>
      <PageHeader
        title={<span className={cx("inline-flex items-center gap-2", t.accent)}><Building2 className="size-6" />{b?.name ?? "Building"}</span>}
        subtitle={`Rent status for ${formatPeriod(period)}. Tap a flat to record a payment or see its history.`}
        actions={<MonthPicker value={period} onChange={setPeriod} />}
      />
      <ErrorNote error={error} />
      {loading && !data && <Loading />}
      {data && !b && <p className="text-muted">Unknown building.</p>}
      {b && (
        <div className={cx("rounded-2xl border p-3 sm:p-5", t.soft, t.border)}>
          <div className="flex flex-col gap-3">
            {groupByFloor(b).reverse().map(([floor, flats]) => (
              <div key={floor}>
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-stone-600">{flats[0].floor_label}</div>
                <div className={cx("grid gap-3", flats.length > 1 ? "sm:grid-cols-2" : "")}>
                  {flats.map((f) => <FlatCard key={f.id} flat={f} code={b.code} period={period} />)}
                </div>
              </div>
            ))}
          </div>
          <div className={cx("mt-3 h-1.5 rounded-full", t.bar)} aria-hidden />
        </div>
      )}
    </>
  );
}
