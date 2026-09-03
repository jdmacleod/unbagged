import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { day, money, number } from "../format";

const ROWS = [
  { key: "visits", label: "Visits", format: number },
  { key: "total_spend", label: "Total spend", format: money },
  { key: "distinct_products", label: "Distinct products", format: number },
  { key: "identifier_count", label: "Identifiers held for you", format: number },
  { key: "inference_count", label: "Inferred attributes", format: number },
  {
    key: "appended_inference_count",
    label: "…of those, appended from elsewhere",
    format: number,
  },
  { key: "absent_disclosures", label: "Categories not addressed", format: number },
] as const;

export function Compare() {
  const compare = useAsync(() => api.compare(), []);

  if (compare.error) return <ErrorBox error={compare.error} />;
  if (!compare.data) return <Spinner label="Comparing" />;

  const { requests, comparable } = compare.data;

  if (!comparable) {
    return (
      <Empty>
        Comparison needs a second retailer&rsquo;s response. Once one arrives, this
        view puts them side by side.
      </Empty>
    );
  }

  return (
    <Card title="Side by side">
      <div className="scroll-x">
        <table className="w-full min-w-[32rem] text-sm">
          <thead>
            <tr className="text-left">
              <th className="py-2 font-medium"> </th>
              {requests.map((r) => (
                <th key={r.id} className="py-2 font-medium">
                  {r.display_name}
                  <span className="block text-xs font-normal text-stone-500 dark:text-stone-400">
                    {r.disclosed ? (
                      `${day(r.first_visit)} → ${day(r.last_visit)}`
                    ) : (
                      // Said plainly at the top of the column, because a column
                      // of em dashes on its own is ambiguous: it could mean zero.
                      <span className="text-amber-700 dark:text-amber-400">
                        disclosed no data
                      </span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-200 dark:divide-stone-800">
            {ROWS.map((row) => (
              <tr key={row.key}>
                <th className="py-2 text-left font-normal text-stone-600 dark:text-stone-400">
                  {row.label}
                </th>
                {requests.map((r) => (
                  <td
                    key={r.id}
                    className="py-2 tabular-nums"
                    title={
                      r.disclosed
                        ? undefined
                        : `${r.display_name} did not disclose this. A dash means not disclosed, not zero.`
                    }
                  >
                    {row.format(r[row.key] as number | null)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {requests.some((r) => !r.disclosed) && (
        <p className="mt-3 text-xs text-stone-600 dark:text-stone-400">
          A dash means the retailer disclosed nothing of that kind, which is not
          the same as a zero. Categories not addressed is counted for every
          retailer, because that is a finding about them either way.
        </p>
      )}
    </Card>
  );
}
