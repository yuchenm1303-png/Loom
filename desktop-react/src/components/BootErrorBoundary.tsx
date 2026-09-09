import { Component, type ErrorInfo, type ReactNode } from "react";

interface BootErrorBoundaryProps {
  children: ReactNode;
}

interface BootErrorBoundaryState {
  error: Error | null;
}

export class BootErrorBoundary extends Component<BootErrorBoundaryProps, BootErrorBoundaryState> {
  state: BootErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BootErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Loom renderer crashed during bootstrap", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="boot-error">
        <div className="boot-error-card">
          <div className="brand-mark large">L</div>
          <h1>Loom renderer failed to start</h1>
          <p>{error.message || String(error)}</p>
          <button className="button primary" onClick={() => window.location.reload()}>Reload</button>
        </div>
      </div>
    );
  }
}
