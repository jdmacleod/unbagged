import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Card, Empty, ErrorBox, Pill, ProvenanceTag, Spinner } from "../components/ui";
import { humanise } from "../format";
import type { Identity, Inference } from "../types";

const ORIGIN_COPY: Record<string, { title: string; blurb: string }> = {
  first_party_model: {
    title: "Modelled from your shopping",
    blurb:
      "Scores the retailer computed from the baskets in this same report. You can check them against the timeline.",
  },
  appended_third_party: {
    title: "Appended from somewhere else",
    blurb:
      "Nothing in a grocery basket says how long you have lived at your address or whether you will take a cruise. These attributes were obtained elsewhere, and the response does not say where.",
  },
  unknown: {
    title: "Origin unclear",
    blurb: "The adapter could not tell where these came from.",
  },
};

export function Profile({ requestId }: { requestId: number }) {
  const profile = useAsync(() => api.profile(requestId), [requestId]);

  if (profile.error) return <ErrorBox error={profile.error} />;
  if (!profile.data) return <Spinner label="Reading the profile" />;

  const { identities, inferences_by_origin, household_scoped_count } = profile.data;
  const origins = Object.entries(inferences_by_origin).filter(([, v]) => v.length > 0);

  return (
    <div className="space-y-4">
      <Card
        title={`Identifiers (${identities.length})`}
        actions={
          <span className="text-xs text-stone-500 dark:text-stone-400">
            separate keys the retailer holds for you
          </span>
        }
      >
        {identities.length === 0 ? (
          <Empty>No identifiers were found in this response.</Empty>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {identities.map((identity) => (
              <IdentityRow key={identity.id} identity={identity} />
            ))}
          </ul>
        )}
      </Card>

      {household_scoped_count > 0 && (
        <p className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
          {household_scoped_count} of these attributes describe your{" "}
          <strong>household</strong>, not you. They cover people who never signed up
          for anything.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {origins.map(([origin, inferences]) => (
          <Card
            key={origin}
            title={ORIGIN_COPY[origin]?.title ?? humanise(origin)}
            actions={<Pill tone={origin === "appended_third_party" ? "warn" : "neutral"}>
              {inferences.length}
            </Pill>}
          >
            <p className="mb-3 text-xs text-stone-600 dark:text-stone-400">
              {ORIGIN_COPY[origin]?.blurb}
            </p>
            <ul className="space-y-2">
              {inferences.map((inference) => (
                <InferenceCard key={inference.id} inference={inference} />
              ))}
            </ul>
          </Card>
        ))}
      </div>

      {origins.length === 0 && (
        <Empty>This response contained no inferred attributes.</Empty>
      )}
    </div>
  );
}

function IdentityRow({ identity }: { identity: Identity }) {
  return (
    <li className="flex items-baseline justify-between gap-2 rounded border border-stone-200 px-3 py-2 text-sm dark:border-stone-800">
      <span className="text-stone-500 dark:text-stone-400">
        {humanise(identity.id_type)}
      </span>
      <span className="ml-auto truncate font-mono text-xs" title={identity.value}>
        {identity.value}
      </span>
      {identity.scope === "household" && (
        <Pill tone="warn" title="Covers everyone at the address, not only you.">
          household
        </Pill>
      )}
      <ProvenanceTag provenance={identity.provenance} />
    </li>
  );
}

function InferenceCard({ inference }: { inference: Inference }) {
  return (
    <li className="rounded border border-stone-200 px-3 py-2 dark:border-stone-800">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{humanise(inference.label)}</span>
        <span className="ml-auto text-sm tabular-nums">{inference.value_raw}</span>
        <ProvenanceTag provenance={inference.provenance} />
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        {inference.subject === "household" && (
          <Pill tone="warn" title="Describes your household, not you.">
            household
          </Pill>
        )}
        {inference.scale && <Pill>{humanise(inference.scale)}</Pill>}
        <Derivability value={inference.derivable_from_txns} />
      </div>
    </li>
  );
}

/**
 * Three states, not two. "We cannot tell whether the baskets explain this" is a
 * different claim from "the baskets do not explain this", and collapsing them
 * would overstate what the adapter knows.
 */
function Derivability({ value }: { value: boolean | null }) {
  if (value === true)
    return (
      <Pill title="This could have been worked out from the purchases in this report.">
        derivable from your baskets
      </Pill>
    );
  if (value === false)
    return (
      <Pill
        tone="bad"
        title="Nothing in the purchases in this report explains this value."
      >
        not derivable from your baskets
      </Pill>
    );
  return (
    <Pill tone="warn" title="The response does not contain enough to tell either way.">
      derivability unknown
    </Pill>
  );
}
