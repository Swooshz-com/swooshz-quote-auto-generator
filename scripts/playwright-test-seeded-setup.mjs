export const TEST_REFERENCE_FILE_NAME = "test-workspace-reference.pdf";

// Public synthetic one-page vector PDF, including page objects and xref/trailer.
const TEST_REFERENCE_DATA_URL = "data:application/pdf;base64,JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCA3MiA3Ml0gL1Jlc291cmNlcyA8PCA+PiAvQ29udGVudHMgNCAwIFIgPj4KZW5kb2JqCjQgMCBvYmoKPDwgL0xlbmd0aCAzNiA+PgpzdHJlYW0KMC4yIDAuNCAwLjYgUkcKMSB3CjEyIDEyIDQ4IDQ4IHJlIFMKZW5kc3RyZWFtCmVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMDkgMDAwMDAgbiAKMDAwMDAwMDA1OCAwMDAwMCBuIAowMDAwMDAwMTE1IDAwMDAwIG4gCjAwMDAwMDAyMTcgMDAwMDAgbiAKdHJhaWxlcgo8PCAvU2l6ZSA1IC9Sb290IDEgMCBSID4+CnN0YXJ0eHJlZgozMDIKJSVFT0YK";

export function createTestReferenceMedia(reference) {
  if (reference === null || typeof reference !== "object" || Array.isArray(reference)) {
    throw new Error("A synthetic media record object is required.");
  }
  const { name, data_url: dataUrl } = reference;
  if (typeof name !== "string" || !name.trim() || typeof dataUrl !== "string") {
    throw new Error("A named synthetic media record is required.");
  }
  const match = dataUrl.match(/^data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})$/);
  if (!match) throw new Error("Synthetic media must use a base64 data URL.");
  const bytes = Buffer.from(match[2], "base64");
  if (!bytes.length || bytes.toString("base64") !== match[2]) {
    throw new Error("Synthetic media must contain canonical nonempty base64 bytes.");
  }
  const type = match[1];
  if ((reference.type !== undefined && reference.type !== type)
      || (reference.size !== undefined && reference.size !== bytes.length)) {
    throw new Error("Synthetic media metadata must match its data URL and bytes.");
  }
  return Object.freeze({ name, type, size: bytes.length, data_url: dataUrl });
}

export const TEST_REFERENCE_MEDIA = createTestReferenceMedia({
  name: TEST_REFERENCE_FILE_NAME,
  data_url: TEST_REFERENCE_DATA_URL,
});

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
  const reference = createTestReferenceMedia(
    options.reference === undefined ? TEST_REFERENCE_MEDIA : options.reference,
  );
  await page.locator("#imageIntake").waitFor({ state: "visible", timeout: 15000 });
  await page.evaluate(async ({ details, reference }) => {
    if (!state.profiles.length) await loadProfiles();
    renderProfileOptions();
    renderPresetOptions();
    selectPricingReferenceOptionValue(firstPricingReferenceOptionValue());
    selectPresetValue(firstAvailablePresetValue());
    loadSelectedPreset({ silent: true });
    updateGeneratorCopy();
    applyQuoteDetails(details, { partial: true });
    state.images = [await ensureContentFingerprint(reference)];
    await persistSessionFiles(sessionFileRecordsFromDraft()).catch(() => {});
    saveSessionState();
    renderFiles();
    setImageUploadStatus("1 test reference file loaded.");
    setWorkflowStage("ready_to_analyze");
    syncControlStates();
  }, {
    details: TEST_QUOTE_DETAILS,
    reference,
  });
  await page.locator("#fileList .file-item", { hasText: reference.name }).waitFor({ timeout: 15000 });
}
