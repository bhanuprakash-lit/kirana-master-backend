import React from 'react';

// Stops a render error in one page from blanking the whole panel.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('Admin panel error:', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="m-8 p-6 bg-rose-50 border border-rose-200 rounded-xl">
          <h2 className="text-lg font-bold text-rose-700">Something broke on this page</h2>
          <p className="text-sm text-rose-600 mt-1 font-mono">{String(this.state.error?.message || this.state.error)}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-4 px-4 py-2 text-sm font-bold text-white bg-rose-600 hover:bg-rose-700 rounded-lg"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
