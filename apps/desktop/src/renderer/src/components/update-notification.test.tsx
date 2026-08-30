import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UpdateNotification } from "./update-notification";

const mocks = vi.hoisted(() => ({
  installUpdate: vi.fn(),
  openExternal: vi.fn(),
}));

type UpdateDownloadedListener = (info: {
  version: string;
  releaseNotes?: string;
}) => void;

describe("UpdateNotification", () => {
  let updateDownloaded: UpdateDownloadedListener;

  beforeEach(() => {
    mocks.installUpdate.mockReset().mockResolvedValue(undefined);
    mocks.openExternal.mockReset().mockResolvedValue(undefined);

    Object.defineProperty(window, "desktopAPI", {
      configurable: true,
      value: { openExternal: mocks.openExternal },
    });
    Object.defineProperty(window, "updater", {
      configurable: true,
      value: {
        installRequiresStoppedRuntime: false,
        onUpdateDownloaded: (listener: UpdateDownloadedListener) => {
          updateDownloaded = listener;
          return vi.fn();
        },
        installUpdate: mocks.installUpdate,
      },
    });
  });

  it("opens the downloaded version's changelog from the update prompt", () => {
    render(<UpdateNotification />);
    act(() => updateDownloaded({ version: "0.4.27" }));

    expect(screen.queryByRole("button", { name: "Later" })).not.toBeInTheDocument();
    expect(screen.getByText("v0.4.27 will be applied on next launch.")).toBeInTheDocument();
    expect(screen.queryByText(/Installation waits/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "See changelog" }));

    expect(mocks.openExternal).toHaveBeenCalledWith(
      "https://multica.ai/changelog#release-0-4-27",
    );
  });

  it("still installs the update immediately from the primary action", () => {
    render(<UpdateNotification />);
    act(() => updateDownloaded({ version: "0.4.27" }));

    fireEvent.click(screen.getByRole("button", { name: "Restart now" }));

    expect(mocks.installUpdate).toHaveBeenCalledOnce();
  });

  it("links custom updates to the fork release and explains runtime deferral", () => {
    Object.defineProperty(window.updater, "installRequiresStoppedRuntime", { value: true });
    render(<UpdateNotification />);
    act(() => updateDownloaded({ version: "0.4.36-custom.2" }));
    expect(screen.getByText(/Installation waits until the bundled runtime is stopped/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "See changelog" }));
    expect(mocks.openExternal).toHaveBeenCalledWith(
      "https://github.com/medking82/multica/releases/tag/desktop-v0.4.36-custom.2",
    );
  });
});
