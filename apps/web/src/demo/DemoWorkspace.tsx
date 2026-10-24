/**
 * Interactive judge demo for the static GitHub Pages build.
 *
 * It composes the four scenarios the recording script walks through, all driven by
 * {@link DemoStore}: onboarding and consent, the privacy ROI/mask boundary with a simulated Ring
 * frame, the Open-Meteo ripening curve with a warmth slider, and coordinator-approved dispatch.
 *
 * Everything is labelled fixture and nothing here can reach a network endpoint. The banner and
 * footer say so on every screen, because a stand-alone page is exactly where a fixture screenshot
 * is most likely to be mistaken for live evidence.
 */

import { useMemo, type ReactElement } from "react";

import { DemoStore } from "./demoStore";
import { DispatchPanel } from "./components/DispatchPanel";
import { OnboardingPanel } from "./components/OnboardingPanel";
import { PrivacyMaskEditor } from "./components/PrivacyMaskEditor";
import { RipeningCurve } from "./components/RipeningCurve";
import { useDemoStore } from "./useDemoStore";

export function DemoWorkspace({ store }: { readonly store?: DemoStore }): ReactElement {
  const resolved = useMemo(() => store ?? new DemoStore(), [store]);
  const state = useDemoStore(resolved);

  return (
    <div className="app demo" data-testid="demo-workspace">
      <header className="masthead">
        <p className="eyebrow">HarvestGrid · interactive demo</p>
        <h1>Walk a glean from tree onboarding to volunteer dispatch</h1>
        <p className="session">
          Static page · no backend · coarse area {state.tree.coarseGeohash} · every value below is a
          labelled fixture
        </p>
        <p className="banner banner-fixture" role="status" data-testid="demo-banner">
          FIXTURE DATA — no Ring device, weather service, telephony account, or database is
          connected. Nothing on this page is live evidence, and the forecast stays experimental.
        </p>
      </header>
      <main>
        <div className="demo-grid">
          <OnboardingPanel store={resolved} tree={state.tree} />
          <RipeningCurve store={resolved} state={state} />
        </div>
        <PrivacyMaskEditor store={resolved} state={state} />
        <DispatchPanel store={resolved} state={state} />
        <section aria-labelledby="activity-heading" className="card" data-testid="activity-panel">
          <h2 id="activity-heading">Activity log</h2>
          <p className="note">
            Every action you take is recorded here with its consequence, including the ones that
            were refused.
          </p>
          <ol className="activity" data-testid="activity-log">
            {state.activity.map((entry, index) => (
              <li key={`${entry.at}-${index}`}>{entry.message}</li>
            ))}
          </ol>
        </section>
      </main>
      <footer className="colophon">
        <p>
          Visual estimates never assert internal ripeness or food safety, and volunteers are never
          dispatched automatically. Exact addresses appear only for a confirmed, assigned volunteer
          inside the reveal window — which this demo does not model.
        </p>
      </footer>
    </div>
  );
}
