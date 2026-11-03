import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "@gravity-ui/uikit";
import { describe, expect, it } from "vitest";
import { CompanyAnalysisPlanCard } from "./CompanyAnalysisPlanCard.jsx";


describe("CompanyAnalysisPlanCard channel availability", () => {
  it("offers only eligible sources and labels a personal account without a checkbox", () => {
    render(
      <ThemeProvider theme="dark">
        <CompanyAnalysisPlanCard
          metadata={{
            saved_result_id: 1,
            business_ids: [7],
            available_channels: { website: [7], telegram: [], vk: [7] },
            unavailable_channels: [{
              business_id: 7,
              key: "telegram",
              label: "Telegram",
              url: "https://t.me/alice",
              status: "personal",
              reason: "Личный Telegram, не анализируется",
            }],
          }}
          onStart={() => {}}
        />
      </ThemeProvider>,
    );

    expect(screen.getByRole("checkbox", { name: /Сайт/i })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /ВКонтакте/i })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: /^Telegram/i })).toBeNull();
    expect(screen.getByText("Личный Telegram, не анализируется")).toBeTruthy();
  });
});
