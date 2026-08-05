import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AskHistorySidebar from "../AskHistorySidebar";
import type { HistoryItem } from "@/store/useHistoryStore";

const item: HistoryItem = {
  id: "history-1",
  query: "How does this paper work?",
  answer: "It explains the method.",
  references: [],
  timestamp: Date.UTC(2026, 7, 4, 12, 30),
  selectedIds: ["paper-1"],
};

describe("AskHistorySidebar accessibility", () => {
  it("allows selecting a history item with Enter and Space", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <AskHistorySidebar
        items={[item]}
        activeHistoryId={null}
        onSelect={onSelect}
        onRemoveItem={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );

    const historyItem = screen.getByRole("button", { name: /How does this paper/ });
    expect(historyItem).toHaveAttribute("aria-pressed", "false");

    historyItem.focus();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");
    expect(onSelect).toHaveBeenCalledTimes(2);
    expect(onSelect).toHaveBeenCalledWith(item);
  });

  it("exposes an accessible delete button without selecting the item", () => {
    const onSelect = vi.fn();
    const onRemoveItem = vi.fn();
    render(
      <AskHistorySidebar
        items={[item]}
        activeHistoryId={null}
        onSelect={onSelect}
        onRemoveItem={onRemoveItem}
        onClearAll={vi.fn()}
      />,
    );

    const deleteButton = screen.getByRole("button", { name: "ask.removeHistory" });
    fireEvent.click(deleteButton);

    expect(onRemoveItem).toHaveBeenCalledWith(item.id);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
