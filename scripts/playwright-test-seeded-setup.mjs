export const TEST_REFERENCE_FILE_NAME = "test-workspace-reference.pdf";

const TEST_REFERENCE_DATA_URL = "data:application/pdf;base64,JVBERi0xLjQKJVRlc3QK";

const TEST_QUOTE_DETAILS = {
  client: {
    name: "Internal Test Workspace",
    attention: "Test Team",
    title: "Project Team",
    address: "Singapore",
  },
  project: {
    title: "RE: Internal Test Exhibition Booth",
    show_name: "Internal Test Show",
  },
  project_number: "KI-TEST-001",
  rich_text: {
    clientName: "<div><strong>Internal Test Workspace</strong></div>",
    clientAttention: "<div><strong>Test Team</strong></div>",
    clientTitle: "<div>Project Team</div>",
    clientAddress: "<div>Singapore</div>",
    projectTitle: "<div><strong>RE: Internal Test Exhibition Booth</strong></div>",
    projectNumber: "<div>KI-TEST-001</div>",
  },
};

export async function seedQuoteDraftFromTestFixture(page, options = {}) {
  const fileName = options.fileName || TEST_REFERENCE_FILE_NAME;
  await page.locator("#imageIntake").waitFor({ state: "visible", timeout: 15000 });
  await page.evaluate(async ({ details, fileName, dataUrl }) => {
    if (!state.profiles.length) await loadProfiles();
    renderProfileOptions();
    renderPresetOptions();
    selectPricingReferenceOptionValue(firstPricingReferenceOptionValue());
    selectPresetValue(firstAvailablePresetValue());
    loadSelectedPreset({ silent: true });
    updateGeneratorCopy();
    applyQuoteDetails(details, { partial: true });
    state.images = [{
      name: fileName,
      type: "application/pdf",
      size: 24,
      data_url: dataUrl,
    }];
    await persistSessionFiles(sessionFileRecordsFromDraft()).catch(() => {});
    saveSessionState();
    renderFiles();
    setImageUploadStatus("1 test reference file loaded.");
    setWorkflowStage("ready_to_analyze");
    syncControlStates();
  }, {
    details: TEST_QUOTE_DETAILS,
    fileName,
    dataUrl: TEST_REFERENCE_DATA_URL,
  });
  await page.locator("#fileList .file-item", { hasText: fileName }).waitFor({ timeout: 15000 });
}
