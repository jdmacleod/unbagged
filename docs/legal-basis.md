# Legal basis for the compliance view

> **This tool reports observations, not conclusions.** It records what a retailer's
> response contained and what it did not. It does not determine whether a retailer
> complied with the law, and nothing here is legal advice. Whether a particular
> omission is a violation depends on facts this tool cannot see — whether the
> business is in scope, whether an exemption applies, what was disclosed elsewhere
> in a privacy policy, and what you actually asked for. If you think your rights
> were violated, talk to a lawyer or contact the
> [California Privacy Protection Agency](https://cppa.ca.gov/).

## Where the categories come from

The compliance view has one row per disclosure obligation in the California
Consumer Privacy Act as amended by the California Privacy Rights Act. The keys below
are stable identifiers used in the `disclosure.category` column and generated into
the UI; they are not quotations of the statute.

Citations are to the California Civil Code.

| Key | Citation | What the business must disclose |
|---|---|---|
| `CATEGORIES_COLLECTED` | § 1798.110(a)(1) | The categories of personal information it collected about you. |
| `SOURCES` | § 1798.110(a)(2) | The categories of sources from which that information was collected. |
| `BUSINESS_PURPOSE` | § 1798.110(a)(3) | The business or commercial purpose for collecting, selling, or sharing it. |
| `THIRD_PARTIES_SHARED_WITH` | § 1798.110(a)(4) | The categories of third parties to whom it discloses personal information. |
| `SPECIFIC_PIECES` | § 1798.110(a)(5) | The specific pieces of personal information it has collected about you. |
| `SOLD_OR_SHARED` | § 1798.115(a)(2)–(3) | The categories of personal information sold or shared, and the categories of third parties that bought or received it. |
| `DISCLOSED_FOR_BUSINESS_PURPOSE` | § 1798.115(a)(3) | The categories of personal information disclosed for a business purpose, as distinct from sold or shared. |
| `RETENTION_PERIOD` | § 1798.100(a)(3) | How long each category is retained, or the criteria used to decide. |

### Notes on individual categories

**`SPECIFIC_PIECES` is the one most responses actually answer.** A retailer that
returns two years of itemised baskets has satisfied § 1798.110(a)(5) and usually
nothing else. A matrix that is green in this column and red in every other one is
the common shape, not an anomaly — which is exactly why the other columns exist.

**`SOLD_OR_SHARED` and `DISCLOSED_FOR_BUSINESS_PURPOSE` are separate columns
because the statute separates them.** "Sold or shared" carries the cross-context
behavioural advertising meaning added by the CPRA; "disclosed for a business
purpose" covers service providers and contractors. A response that addresses one
has not addressed the other.

**`RETENTION_PERIOD` was added by the CPRA** and is the obligation most often
missing entirely from responses drafted against the original 2018 CCPA.

## How status is decided

| Status | Meaning |
|---|---|
| `provided` | The response addresses the category with content specific to you or to the categories the statute names. |
| `partial` | The category is addressed, but incompletely — for example, a coverage window shorter than requested, or categories named without the third parties receiving them. |
| `absent` | The response does not address the category at all. |

`absent` is a recorded finding, never a missing row. An adapter that finds no
section on categories of sources emits
`Disclosure(category=SOURCES, status=ABSENT)` with a note explaining what it looked
for. Silence in the data model would be indistinguishable from "not yet parsed",
and the difference matters: one is a fact about the retailer, the other is a bug.

An adapter never upgrades a status on inference. If a response mentions "our
affiliates" without naming them, that is `partial` with the phrase quoted as
evidence — not `provided`, and not `absent`.

## Scope limits worth knowing

- **12-month lookback.** § 1798.130(a)(2) obliges disclosure covering the 12 months
  preceding the request. Since 1 January 2022, consumers may request information
  beyond that window under § 1798.130(a)(3)(B), though a business need not comply if
  doing so proves impossible or would involve disproportionate effort. A response
  that stops at 24 months while offering more on request is recorded as a
  `supplemental_period` follow-up, not as a compliance failure.
- **Verification.** Businesses may refuse requests they cannot verify
  (§ 1798.140(ak)). A refusal on verification grounds is not a disclosure failure and
  should not be scored as one.
- **Other statutes.** The matrix is CCPA/CPRA-shaped. The `request.statute` column
  exists so GDPR Article 15 responses can be scored against their own categories
  later; do not reuse these keys for that.
