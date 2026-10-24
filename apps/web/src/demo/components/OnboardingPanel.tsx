/**
 * Scenario 1a — tree onboarding.
 *
 * The demo shows the two consents that genuinely gate collection (`FR-HG-001`): camera-collection
 * consent is separate from organizational-operations consent and is never implied. Turning either
 * off blocks the rest of the demo, exactly as it blocks the scheduler.
 */

import { useState, type ReactElement } from "react";

import type { DemoStore, TreeProfile } from "../demoStore";

export function OnboardingPanel({
  store,
  tree,
}: {
  readonly store: DemoStore;
  readonly tree: TreeProfile;
}): ReactElement {
  const [regionLabel, setRegionLabel] = useState(tree.regionLabel);
  const [ownerConsent, setOwnerConsent] = useState(tree.ownerConsentActive);
  const [cameraConsent, setCameraConsent] = useState(tree.cameraCollectionConsent);
  const blocked = !ownerConsent || !cameraConsent;

  return (
    <section aria-labelledby="onboarding-heading" className="card" data-testid="onboarding-panel">
      <h2 id="onboarding-heading">1 · Tree onboarding</h2>
      <p className="note">
        A tree is registered with a <strong>coarse</strong> geohash ({tree.coarseGeohash}) only.
        Exact coordinates cannot be entered here or in the API.
      </p>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          store.completeOnboarding({
            regionLabel,
            ownerConsentActive: ownerConsent,
            cameraCollectionConsent: cameraConsent,
          });
        }}
      >
        <label className="field">
          <span>Neighbourhood label</span>
          <input
            value={regionLabel}
            maxLength={60}
            onChange={(event) => setRegionLabel(event.target.value)}
            data-testid="region-input"
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={ownerConsent}
            onChange={(event) => setOwnerConsent(event.target.checked)}
            data-testid="owner-consent"
          />
          <span>Owner consent for organizational operations is active</span>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={cameraConsent}
            onChange={(event) => setCameraConsent(event.target.checked)}
            data-testid="camera-consent"
          />
          <span>Owner consent for camera collection is active</span>
        </label>
        <button type="submit" className="primary" data-testid="save-tree">
          Save tree record
        </button>
      </form>
      <dl className="facts">
        <div>
          <dt>Tree</dt>
          <dd data-testid="tree-id">{tree.treeId}</dd>
        </div>
        <div>
          <dt>Cultivar</dt>
          <dd>{tree.cultivarId} · prior review status: unreviewed fixture</dd>
        </div>
        <div>
          <dt>Collection schedule</dt>
          <dd>{tree.collectionSchedule}</dd>
        </div>
        <div>
          <dt>Collection status</dt>
          <dd data-testid="collection-status">
            {blocked ? "Blocked — both consents are required" : "Consented; awaiting an approved view"}
          </dd>
        </div>
      </dl>
    </section>
  );
}
