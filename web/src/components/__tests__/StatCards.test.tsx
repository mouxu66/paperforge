import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StatCards from "../StatCards";

const mockStats = {
  totalPapers: 10,
  reportCount: 3,
  reportAvgScore: 0.82,
  reportAvgFidelity: 0.91,
  totalChunks: 100,
  totalSize: 50000,
  byCategory: [],
  bySource: [],
};

describe("StatCards", () => {
  it("renders paper stats by default", () => {
    render(<StatCards stats={mockStats} />);
    expect(screen.getByText("stats.totalPapers")).toBeInTheDocument();
    expect(screen.getByText("stats.textChunks")).toBeInTheDocument();
    expect(screen.getByText("stats.indexSize")).toBeInTheDocument();
  });

  it("renders report stats when isReport is true", () => {
    render(<StatCards stats={mockStats} isReport />);
    expect(screen.getByText("stats.reportCount")).toBeInTheDocument();
    expect(screen.getByText("stats.reportAvgScore")).toBeInTheDocument();
    expect(screen.getByText("stats.reportAvgFidelity")).toBeInTheDocument();
  });

  it("shows 0 values when stats is null", () => {
    render(<StatCards stats={null} isReport />);
    expect(screen.getByText("stats.reportCount")).toBeInTheDocument();
    expect(screen.getByText("stats.reportAvgScore")).toBeInTheDocument();
    expect(screen.getByText("stats.reportAvgFidelity")).toBeInTheDocument();
  });
});
