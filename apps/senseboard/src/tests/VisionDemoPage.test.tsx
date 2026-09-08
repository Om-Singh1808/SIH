import { fireEvent, render, screen } from "@testing-library/react";
import { VisionDemoPage } from "@/pages/VisionDemoPage";

describe("VisionDemoPage", () => {
  it("moves between operational scenarios and report views", () => {
    render(<VisionDemoPage />);

    expect(screen.getByRole("heading", { name: "Anonymous retail intelligence, live." })).toBeInTheDocument();
    expect(screen.getByLabelText("Anonymized retail floor CCTV demonstration")).toHaveAttribute(
      "src",
      "/demo/retail-floor-privacy-preview.mp4",
    );

    fireEvent.click(screen.getByRole("button", { name: "Queue overload" }));
    expect(screen.getByText("Open Counter 2 within 3 min")).toBeInTheDocument();
    expect(screen.getByText("Unstable")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Weekly report" }));
    expect(screen.getByRole("heading", { name: "Sales and missed opportunity" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Three moves for next week" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Privacy & model" }));
    expect(screen.getByRole("heading", { name: "From pixels to anonymous operations" })).toBeInTheDocument();
    expect(screen.getByText("What leaves the edge")).toBeInTheDocument();
  });
});
