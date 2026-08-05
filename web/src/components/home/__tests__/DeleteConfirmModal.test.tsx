import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeleteConfirmModal from "../DeleteConfirmModal";

describe("DeleteConfirmModal", () => {
  const renderModal = (props = {}) => {
    return render(
      <DeleteConfirmModal
        open={true}
        onCancel={vi.fn()}
        onOk={vi.fn()}
        confirmLoading={false}
        selectedCount={3}
        {...props}
      />,
    );
  };

  it("renders selected count", () => {
    renderModal();
    expect(screen.getByText(/确认删除选中的/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("calls onOk when confirm button clicked", async () => {
    const onOk = vi.fn();
    const user = userEvent.setup();
    renderModal({ onOk });
    const okButton = await screen.findByRole("button", { name: /确认删除/ });
    await user.click(okButton);
    expect(onOk).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when cancel button clicked", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    renderModal({ onCancel });
    const cancelButton = await screen.findByRole("button", { name: /取\s*消/ });
    await user.click(cancelButton);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
