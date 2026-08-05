import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import HomeHeader from "../HomeHeader";

describe("HomeHeader", () => {
  it("renders title and subtitle", () => {
    render(<HomeHeader />);

    expect(screen.getByText("home.title")).toBeInTheDocument();
    expect(screen.getByText("home.subtitle")).toBeInTheDocument();
  });
});
