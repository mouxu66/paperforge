import * as React from "react";
import { MemoryRouter as RRMemoryRouter } from "react-router-dom";

/**
 * MemoryRouter wrapper that enables React Router v7 future flags.
 *
 * Use this in place of react-router-dom's MemoryRouter in tests to avoid
 * the `v7_startTransition` / `v7_relativeSplatPath` console warnings.
 */
export function MemoryRouter(props: React.ComponentProps<typeof RRMemoryRouter>) {
  return (
    <RRMemoryRouter
      {...props}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    />
  );
}
