import { useState, type ReactElement } from "react";

import type {
  DonationReceipt,
  FieldError,
  MembershipRole,
  ReceiptInput,
  RejectionReason,
  WeightSource,
} from "../domain/types";

const REJECTION_REASONS: readonly RejectionReason[] = [
  "spoiled",
  "damaged",
  "undersized",
  "pest_damage",
  "recipient_declined",
];

interface FormState {
  readonly gleanId: string;
  readonly recipientId: string;
  readonly harvestedOn: string;
  readonly grossKg: string;
  readonly netKg: string;
  readonly acceptedKg: string;
  readonly rejectedKg: string;
  readonly source: WeightSource;
  readonly scaleReference: string;
  readonly rejectionReasons: readonly RejectionReason[];
}

const INITIAL: FormState = {
  gleanId: "glean-fixture",
  recipientId: "recipient-fixture",
  harvestedOn: "2026-10-30",
  grossKg: "",
  netKg: "",
  acceptedKg: "",
  rejectedKg: "0",
  source: "calibrated_scale",
  scaleReference: "",
  rejectionReasons: [],
};

export function toReceiptInput(state: FormState): ReceiptInput {
  return {
    gleanId: state.gleanId.trim(),
    recipientId: state.recipientId.trim(),
    harvestedOn: state.harvestedOn,
    weights: {
      grossKg: state.grossKg,
      netKg: state.netKg,
      acceptedKg: state.acceptedKg,
      rejectedKg: state.rejectedKg,
      source: state.source,
      rejectionReasons: state.rejectionReasons,
      ...(state.scaleReference.trim() ? { scaleReference: state.scaleReference } : {}),
    },
  };
}

export function ReceiptForm({
  roles,
  onSubmit,
  receipt,
  errors,
  busy,
}: {
  readonly roles: readonly MembershipRole[];
  readonly onSubmit: (input: ReceiptInput) => void;
  readonly receipt: DonationReceipt | null;
  readonly errors: readonly FieldError[];
  readonly busy: boolean;
}): ReactElement {
  const [state, setState] = useState<FormState>(INITIAL);
  const coordinator = roles.includes("coordinator");

  const update = <Key extends keyof FormState>(key: Key, value: FormState[Key]): void => {
    setState((current) => ({ ...current, [key]: value }));
  };

  const errorFor = (path: string): FieldError | undefined =>
    errors.find((error) => error.path === path);

  const field = (
    path: string,
    label: string,
    key: "grossKg" | "netKg" | "acceptedKg" | "rejectedKg",
  ): ReactElement => {
    const error = errorFor(path);
    const id = `field-${key}`;
    return (
      <div className="field">
        <label htmlFor={id}>{label}</label>
        <input
          id={id}
          inputMode="decimal"
          value={state[key]}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => update(key, event.target.value)}
        />
        {error ? (
          <p className="error" id={`${id}-error`}>
            {error.message}
          </p>
        ) : null}
      </div>
    );
  };

  return (
    <section aria-labelledby="receipt-heading" className="card">
      <h2 id="receipt-heading">Donation receipt</h2>
      <p className="note">
        Weights are entered by the event leader or coordinator and produce a non-sensitive
        receipt: no address, volunteer identity, rejection detail, or scale reference is
        exported.
      </p>
      <form
        className="receipt-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(toReceiptInput(state));
        }}
      >
        <div className="field">
          <label htmlFor="field-glean">Glean</label>
          <input
            id="field-glean"
            value={state.gleanId}
            onChange={(event) => update("gleanId", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="field-recipient">Recipient coordinator</label>
          <input
            id="field-recipient"
            value={state.recipientId}
            onChange={(event) => update("recipientId", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="field-date">Harvest date</label>
          <input
            id="field-date"
            type="date"
            value={state.harvestedOn}
            onChange={(event) => update("harvestedOn", event.target.value)}
          />
        </div>
        {field("weights.grossKg", "Gross weight (kg)", "grossKg")}
        {field("weights.netKg", "Net weight (kg)", "netKg")}
        {field("weights.acceptedKg", "Accepted weight (kg)", "acceptedKg")}
        {field("weights.rejectedKg", "Rejected weight (kg)", "rejectedKg")}
        <div className="field">
          <label htmlFor="field-source">Weight source</label>
          <select
            id="field-source"
            value={state.source}
            onChange={(event) => update("source", event.target.value as WeightSource)}
          >
            <option value="calibrated_scale">Calibrated scale</option>
            <option value="coordinator_manual" disabled={!coordinator}>
              Coordinator manual entry
            </option>
          </select>
          {errorFor("weights.source") ? (
            <p className="error">{errorFor("weights.source")?.message}</p>
          ) : null}
        </div>
        <div className="field">
          <label htmlFor="field-scale">Scale or manual reference</label>
          <input
            id="field-scale"
            value={state.scaleReference}
            aria-describedby={errorFor("weights.scaleReference") ? "scale-error" : undefined}
            onChange={(event) => update("scaleReference", event.target.value)}
          />
          {errorFor("weights.scaleReference") ? (
            <p className="error" id="scale-error">
              {errorFor("weights.scaleReference")?.message}
            </p>
          ) : null}
        </div>
        <fieldset className="field">
          <legend>Rejection reasons</legend>
          {REJECTION_REASONS.map((reason) => (
            <label key={reason} className="check">
              <input
                type="checkbox"
                checked={state.rejectionReasons.includes(reason)}
                onChange={(event) =>
                  update(
                    "rejectionReasons",
                    event.target.checked
                      ? [...state.rejectionReasons, reason]
                      : state.rejectionReasons.filter((item) => item !== reason),
                  )
                }
              />
              {reason.replaceAll("_", " ")}
            </label>
          ))}
          {errorFor("weights.rejectionReasons") ? (
            <p className="error">{errorFor("weights.rejectionReasons")?.message}</p>
          ) : null}
        </fieldset>
        {errors.length > 0 ? (
          <ul className="error-summary" role="alert">
            {errors.map((error) => (
              <li key={`${error.path}-${error.code}`}>{error.message}</li>
            ))}
          </ul>
        ) : null}
        <div className="actions">
          <button type="submit" className="primary" disabled={busy}>
            Record and issue receipt
          </button>
        </div>
      </form>
      {receipt ? (
        <article aria-labelledby="receipt-result" className="receipt">
          <h3 id="receipt-result">Receipt {receipt.receiptNumber}</h3>
          <dl className="facts">
            <div>
              <dt>Donation date</dt>
              <dd>
                <time dateTime={receipt.donationDate}>{receipt.donationDate}</time>
              </dd>
            </div>
            <div>
              <dt>Accepted weight</dt>
              <dd>{receipt.acceptedKg} kg</dd>
            </div>
            <div>
              <dt>Crop</dt>
              <dd>{receipt.crop.replaceAll("_", " ")}</dd>
            </div>
            <div>
              <dt>Provenance</dt>
              <dd>{receipt.provenance}</dd>
            </div>
          </dl>
          {receipt.provenance === "fixture" ? (
            <p className="note">Fixture receipt — not a real donation record.</p>
          ) : null}
        </article>
      ) : null}
    </section>
  );
}
