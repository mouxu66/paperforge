import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import EvidenceSeverityCard from "../EvidenceSeverityCard";

describe("EvidenceSeverityCard", () => {
  it("renders a fatal card with red left border and icon", () => {
    render(
      <EvidenceSeverityCard severity="fatal" showIcon data-testid="card">
        content
      </EvidenceSeverityCard>,
    );

    const card = screen.getByTestId("card");
    expect(card).toHaveAttribute("data-severity", "fatal");
    expect(card).toContainElement(screen.getByLabelText("fatal"));
    // border-left 颜色来自 FATAL_COLOR (#ff4d4f)
    expect(card.style.borderLeftColor).toBe("#ff4d4f");
  });

  it("renders a minor card without the fatal icon", () => {
    render(
      <EvidenceSeverityCard severity="minor" showIcon data-testid="card">
        content
      </EvidenceSeverityCard>,
    );

    const card = screen.getByTestId("card");
    expect(card).toHaveAttribute("data-severity", "minor");
    expect(screen.queryByLabelText("fatal")).not.toBeInTheDocument();
  });

  it("renders as a table row and does not show the icon", () => {
    render(
      <table>
        <tbody>
          <EvidenceSeverityCard as="tr" severity="fatal" showIcon data-testid="row">
            <td>cell</td>
          </EvidenceSeverityCard>
        </tbody>
      </table>,
    );

    const row = screen.getByTestId("row");
    expect(row.tagName).toBe("TR");
    expect(screen.queryByLabelText("fatal")).not.toBeInTheDocument();
  });

  it("applies highlighted style when data-active is true", () => {
    render(
      <EvidenceSeverityCard severity="minor" data-testid="card" data-active>
        content
      </EvidenceSeverityCard>,
    );

    const card = screen.getByTestId("card");
    expect(card).toHaveAttribute("data-active", "true");
    expect(card.style.background).toBe("rgb(255, 251, 230)");
  });

  it("falls back to unknown when severity is not fatal/minor", () => {
    render(
      <EvidenceSeverityCard severity="weird" data-testid="card">
        content
      </EvidenceSeverityCard>,
    );

    const card = screen.getByTestId("card");
    expect(card).toHaveAttribute("data-severity", "weird");
    expect(card.style.borderLeftColor).toBe("transparent");
  });
});
