import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorBox } from "./ui";

/**
 * The last thing standing between a render throw and a blank page.
 *
 * React unmounts the whole tree when a render throws, so without this the
 * reader gets white. That matters more here than in most apps: everything the
 * views render is adapter-derived from a file this project has usually never
 * seen. Adapters are built to degrade rather than raise, but that contract ends
 * at the parse — once a value reaches a view, a shape nobody anticipated is a
 * render throw, and the reader gets nothing instead of this app's careful "the
 * response did not say" language.
 *
 * Wrapped around `<main>` and nothing wider, on purpose. The header, the tabs,
 * the response selector and the footer all survive, and the footer carries the
 * version string — which is how a person reports what they were running. A
 * boundary around the whole app would take that with it, leaving someone with a
 * blank page and no way to say which build produced it.
 *
 * A class, because there is still no hook equivalent: `getDerivedStateFromError`
 * and `componentDidCatch` are the only way to catch a render throw in React 19.
 * It is the one class component in this codebase and this comment is why.
 *
 * Issue #49.
 */
type Props = {
  children: ReactNode;
  /** Remounts the subtree when it changes, so moving tab clears a stuck view. */
  resetKey?: string;
};

type State = { message: string | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(error: unknown): State {
    return {
      message: error instanceof Error ? error.message : String(error),
    };
  }

  componentDidUpdate(previous: Props) {
    // Changing view is the one recovery a reader can perform without being
    // told to. Without this the boundary stays caught for the life of the page
    // and every tab looks broken, when only one of them was.
    if (previous.resetKey !== this.props.resetKey && this.state.message) {
      this.setState({ message: null });
    }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // The console is the only place this can go. No telemetry, no reporting
    // endpoint, nothing leaves the machine — see CLAUDE.md. Someone opening the
    // dev tools is the intended reader, which is also why the message on screen
    // says the response is still stored: the recovery is a reload, and the fear
    // is that the upload was lost.
    console.error("A view failed to render.", error, info.componentStack);
  }

  render() {
    if (this.state.message === null) return this.props.children;
    return (
      <div className="space-y-4">
        <ErrorBox
          error={`This view could not be drawn: ${this.state.message}`}
          onRetry={() => this.setState({ message: null })}
        />
        <p className="max-w-[62ch] text-muted">
          The response itself is still stored and nothing has been lost —
          this is a fault in drawing it, not in reading it. Try another view, or
          reload the page. If it keeps happening, the version at the foot of
          this page is the thing to quote.
        </p>
      </div>
    );
  }
}
