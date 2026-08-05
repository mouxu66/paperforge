import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import AskInput from "../AskInput";

const baseProps = {
  query: "",
  onQueryChange: vi.fn(),
  loading: false,
  onSubmit: vi.fn(),
  paperOptions: [],
  selectedIds: [],
  onSelectedIdsChange: vi.fn(),
};

describe("AskInput accessibility", () => {
  it("names the paper scope selector and question field", () => {
    render(<AskInput {...baseProps} />);

    expect(screen.getByRole("combobox", { name: "ask.paperScope" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "ask.inputPlaceholder" })).toBeInTheDocument();
  });
});
