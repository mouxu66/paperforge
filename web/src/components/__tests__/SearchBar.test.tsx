import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "@/test-utils";
import SearchBar from "../SearchBar";
import { fetchSuggest } from "@/api/papers";
import type { SuggestItem } from "@/api/types";

vi.mock("@/api/papers", () => ({
  fetchSuggest: vi.fn(),
}));

vi.mock("antd", () => ({
  AutoComplete: ({
    value,
    options,
    onChange,
    onSearch,
  }: {
    value: string;
    options: { value: string }[];
    onChange: (value: string) => void;
    onSearch: (value: string) => void;
  }) => (
    <div>
      <input
        aria-label="search input"
        value={value}
        onChange={(event) => {
          const nextValue = event.target.value;
          onChange(nextValue);
          onSearch(nextValue);
        }}
      />
      <div data-testid="suggestions">
        {options.map((option) => (
          <span key={option.value}>{option.value}</span>
        ))}
      </div>
    </div>
  ),
  Input: () => null,
  Select: () => null,
  Space: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Switch: () => null,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("SearchBar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(fetchSuggest).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not let an older suggestion response replace newer results", async () => {
    const oldRequest = deferred<SuggestItem[]>();
    const newRequest = deferred<SuggestItem[]>();
    vi.mocked(fetchSuggest)
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise);

    render(
      <MemoryRouter>
        <SearchBar
          keyword=""
          sort="year_desc"
          semantic={false}
          onKeyword={vi.fn()}
          onSort={vi.fn()}
          onSemantic={vi.fn()}
        />
      </MemoryRouter>,
    );

    const input = screen.getByRole("textbox", { name: "search input" });
    fireEvent.change(input, { target: { value: "old" } });
    await vi.advanceTimersByTimeAsync(300);

    fireEvent.change(input, { target: { value: "new" } });
    await vi.advanceTimersByTimeAsync(300);

    await act(async () => {
      newRequest.resolve([{ id: "new-result", title: "New result" }]);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("suggestions")).toHaveTextContent("new-result");

    await act(async () => {
      oldRequest.resolve([{ id: "old-result", title: "Old result" }]);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("suggestions")).toHaveTextContent("new-result");
    expect(screen.getByTestId("suggestions")).not.toHaveTextContent("old-result");
  });
});
